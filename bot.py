import html
import logging
import os
import random

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database import (
    get_card_counts,
    get_due_cards,
    get_or_create_progress,
    get_random_options,
    get_user_stats,
    get_words_by_ids,
    init_db,
    sync_words,
    update_progress,
)
from card_renderer import render_verb_card, render_word_card
from sheets import fetch_vocabulary, get_sheets_info, write_learned_word
from spaced_repetition import next_review_date, sm2

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

QUIZ_AFTER_WORDS = 10
QUIZ_AFTER_VERBS = 5
QUIZ_QUESTIONS   = 5

MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📖 Учить слова"), KeyboardButton("⚡ Учить глаголы")],
        [KeyboardButton("📊 Статистика"),  KeyboardButton("🔄 Синхронизировать")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

H = html.escape  # shorthand


# ── card text builders (HTML) ──────────────────────────────────────────────────

def _word_card_text(english, russian, transcription, example) -> str:
    parts = [f"📖  <code>{H(english)}</code>"]
    if transcription:
        parts.append(f"<i>{H(transcription)}</i>")
    parts.append("")
    parts.append(f"🇷🇺  <b>{H(russian)}</b>")
    if example:
        parts.append(f"\n<i>{H(example)}</i>")
    return "\n".join(parts)


def _verb_card_text(infinitive, russian, past_simple, past_participle) -> str:
    ps = H(past_simple) if past_simple else "—"
    pp = H(past_participle) if past_participle else "—"
    return (
        f"⚡ <b>Неправильный глагол</b>\n\n"
        f"<code>{H(infinitive)}</code>\n"
        f"<i>{H(russian)}</i>\n\n"
        f"<b>Past Simple</b>      <code>{ps}</code>\n"
        f"<b>Past Participle</b>  <code>{pp}</code>"
    )


def _quiz_word_text(english, transcription, q_num, q_total) -> str:
    sub = f"\n<i>{H(transcription)}</i>" if transcription else ""
    return (
        f"🎯 <b>Вопрос {q_num} из {q_total}</b>\n\n"
        f"Переведи слово:\n\n"
        f"<code>{H(english)}</code>{sub}"
    )


def _quiz_verb_text(infinitive, russian, q_num, q_total) -> str:
    return (
        f"🎯 <b>Вопрос {q_num} из {q_total}</b>\n\n"
        f"Выбери <b>Past Simple</b>:\n\n"
        f"<code>{H(infinitive)}</code>  —  <i>{H(russian)}</i>"
    )


# ── keyboards ──────────────────────────────────────────────────────────────────

def _card_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Знаю", callback_data="know"),
        InlineKeyboardButton("🔄 Повторить", callback_data="again"),
    ]])


def _start_quiz_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🎯 Проверить себя", callback_data="start_quiz"),
        InlineKeyboardButton("▶ Продолжить", callback_data="skip_quiz"),
    ]])


def _options_keyboard(options: list) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(opt, callback_data=f"ans_{i}")] for i, opt in enumerate(options)]
    )


def _result_keyboard(chosen_idx, correct_idx, options, correct) -> InlineKeyboardMarkup:
    rows = []
    for i, opt in enumerate(options):
        if i == correct_idx:
            rows.append([InlineKeyboardButton(f"✓  {opt}", callback_data="noop")])
        elif i == chosen_idx and not correct:
            rows.append([InlineKeyboardButton(f"✗  {opt}", callback_data="noop")])
    rows.append([InlineKeyboardButton("→ Следующий вопрос", callback_data="next_q")])
    return InlineKeyboardMarkup(rows)


def _mode_keyboard(counts: dict) -> InlineKeyboardMarkup:
    rows = []
    if counts.get("word"):
        rows.append([InlineKeyboardButton(
            f"📖 Слова — {counts['word']} карточек", callback_data="mode_word"
        )])
    if counts.get("verb"):
        rows.append([InlineKeyboardButton(
            f"⚡ Глаголы — {counts['verb']} карточек", callback_data="mode_verb"
        )])
    return InlineKeyboardMarkup(rows)


# ── session helpers ────────────────────────────────────────────────────────────

def _add_to_session(context, word_id):
    session = context.user_data.setdefault("session_ids", [])
    if word_id not in session:
        session.append(word_id)
    context.user_data["session_count"] = context.user_data.get("session_count", 0) + 1


def _should_offer_quiz(context, mode) -> bool:
    count     = context.user_data.get("session_count", 0)
    threshold = QUIZ_AFTER_WORDS if mode == "word" else QUIZ_AFTER_VERBS
    return count > 0 and count % threshold == 0


def _reset_session(context):
    context.user_data["session_ids"]   = []
    context.user_data["session_count"] = 0


# ── send card ──────────────────────────────────────────────────────────────────

async def _send_card(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    user_id = update.effective_user.id
    mode    = context.user_data.get("mode", "word")
    due     = get_due_cards(user_id, card_type=mode, limit=1)

    if not due:
        other = "verb" if mode == "word" else "word"
        if get_due_cards(user_id, card_type=other, limit=1):
            other_name = "слова" if other == "word" else "глаголы"
            text = f"🎉 В этом разделе всё готово!\nПопробуй <b>{other_name}</b> 👇"
        else:
            text = "🎉 На сегодня всё! Все карточки повторены.\nВозвращайся завтра 👋"
        if edit:
            await update.callback_query.edit_message_text(text, parse_mode="HTML")
        else:
            await update.message.reply_text(text, parse_mode="HTML", reply_markup=MAIN_MENU)
        return

    word_id, english, russian, transcription, example, past_simple, past_participle = due[0]
    context.user_data.update({
        "word_id":        word_id,
        "english":        english,
        "russian":        russian,
        "transcription":  transcription or "",
        "example":        example or "",
        "past_simple":    past_simple or "",
        "past_participle":past_participle or "",
    })

    if mode == "word":
        image = render_word_card(english, russian, transcription or "", example or "")
    else:
        image = render_verb_card(english, russian, past_simple or "", past_participle or "")

    kb = _card_keyboard()
    if edit:
        # Can't edit a photo message easily — delete old and send new
        try:
            await update.callback_query.message.delete()
        except Exception:
            pass
        await update.effective_chat.send_photo(photo=image, reply_markup=kb)
    else:
        await update.message.reply_photo(photo=image, reply_markup=kb)


async def _offer_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode  = context.user_data.get("mode", "word")
    count = context.user_data.get("session_count", 0)
    label = "слов" if mode == "word" else "глаголов"
    await update.effective_chat.send_message(
        f"💡 Отлично! Ты прошёл <b>{count} {label}</b>.\nПроверим, что запомнилось?",
        reply_markup=_start_quiz_keyboard(),
        parse_mode="HTML",
    )


# ── quiz ───────────────────────────────────────────────────────────────────────

def _build_quiz_queue(context) -> list:
    session_ids = context.user_data.get("session_ids", [])
    mode = context.user_data.get("mode", "word")
    pool = [r for r in get_words_by_ids(session_ids) if r[5] == mode]
    return random.sample(pool, min(QUIZ_QUESTIONS, len(pool)))


def _make_quiz_question(context) -> tuple[str, InlineKeyboardMarkup] | tuple[None, None]:
    queue = context.user_data.get("quiz_queue", [])
    idx   = context.user_data.get("quiz_idx", 0)
    if idx >= len(queue):
        return None, None

    word_id, english, russian, transcription, past_simple, _ = queue[idx]
    mode    = context.user_data.get("mode", "word")
    total   = len(queue)

    if mode == "word":
        correct = russian
        wrongs  = get_random_options(word_id, "word", "russian", count=2)
        text    = _quiz_word_text(english, transcription or "", idx + 1, total)
    else:
        correct = past_simple or russian
        wrongs  = get_random_options(word_id, "verb", "past_simple", count=2)
        text    = _quiz_verb_text(english, russian, idx + 1, total)

    options = wrongs + [correct]
    random.shuffle(options)
    correct_idx = options.index(correct)

    context.user_data.update({
        "quiz_correct_idx": correct_idx,
        "quiz_options":     options,
        "quiz_english":     english,
        "quiz_russian":     russian,
        "quiz_transcription": transcription or "",
    })
    return text, _options_keyboard(options)


# ── command / menu handlers ────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я помогу тебе учить английские слова.\n\nВыбери раздел в меню внизу 👇",
        reply_markup=MAIN_MENU,
    )


async def _start_mode(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    user_id = update.effective_user.id
    if not get_card_counts(user_id).get(mode):
        label = "слов" if mode == "word" else "глаголов"
        await update.message.reply_text(
            f"📭 Нет карточек {label}. Нажми «🔄 Синхронизировать»",
            reply_markup=MAIN_MENU,
        )
        return
    context.user_data["mode"] = mode
    _reset_session(context)
    await _send_card(update, context)


async def cmd_study(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    counts  = get_card_counts(user_id)
    if not counts:
        await update.message.reply_text(
            "📭 Карточек нет. Нажми «🔄 Синхронизировать»", reply_markup=MAIN_MENU
        )
        return
    if len(counts) == 1:
        await _start_mode(update, context, next(iter(counts)))
        return
    await update.message.reply_text("Что будем учить?", reply_markup=_mode_keyboard(counts))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats   = get_user_stats(user_id)
    if not stats:
        await update.message.reply_text("Пока нет данных. Начни учить слова 👇",
                                        reply_markup=MAIN_MENU)
        return
    lines = ["📊 <b>Твоя статистика</b>\n━━━━━━━━━━━━━━━━━━\n"]
    for card_type, s in stats.items():
        label = "📖 Слова" if card_type == "word" else "⚡ Неправильные глаголы"
        pct   = f"  ·  точность {round(s['correct_reviews'] / s['total_reviews'] * 100)}%" \
                if s["total_reviews"] else ""
        lines.append(f"{label}\nВыучено: <b>{s['learned']}</b> / {s['total']}{pct}\n")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=MAIN_MENU)


async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Синхронизирую слова из Google Sheets…")
    try:
        words = fetch_vocabulary()
        new   = sync_words(words)
        wc = sum(1 for w in words if w[4] == "word")
        vc = sum(1 for w in words if w[4] == "verb")
        await msg.edit_text(
            f"✅ Готово! Добавлено новых карточек: <b>{new}</b>\n\n"
            f"📖 Слов: {wc}\n⚡ Глаголов: {vc}",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.exception("Sync failed")
        await msg.edit_text(f"❌ Ошибка синхронизации:\n<code>{H(str(exc))}</code>",
                            parse_mode="HTML")


async def cmd_debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show sheets structure — helps diagnose missing verbs."""
    msg = await update.message.reply_text("🔍 Читаю таблицу…")
    info = get_sheets_info()
    await msg.edit_text(info, parse_mode="HTML")


async def on_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📖 Учить слова":
        await _start_mode(update, context, "word")
    elif text == "⚡ Учить глаголы":
        await _start_mode(update, context, "verb")
    elif text == "📊 Статистика":
        await cmd_stats(update, context)
    elif text == "🔄 Синхронизировать":
        await cmd_sync(update, context)


# ── callback handler ───────────────────────────────────────────────────────────

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    data    = query.data
    user_id = update.effective_user.id

    if data == "noop":
        return

    if data.startswith("mode_"):
        context.user_data["mode"] = data[5:]
        _reset_session(context)
        await _send_card(update, context, edit=True)
        return

    if data in ("know", "again"):
        quality  = 5 if data == "know" else 1
        word_id  = context.user_data.get("word_id")
        if word_id:
            _add_to_session(context, word_id)
            just_learned = _record_progress(user_id, context, quality)
            if just_learned:
                write_learned_word(
                    update.effective_user.full_name or str(user_id),
                    user_id,
                    context.user_data.get("english", ""),
                    context.user_data.get("mode", "word"),
                    context.user_data.get("correct_reviews_after", 0),
                )
        mode = context.user_data.get("mode", "word")
        await _send_card(update, context, edit=True)
        if _should_offer_quiz(context, mode):
            await _offer_quiz(update, context)
        return

    if data == "skip_quiz":
        await query.edit_message_text("▶ Продолжаем!", parse_mode="HTML")
        await _send_card(update, context)
        return

    if data == "start_quiz":
        queue = _build_quiz_queue(context)
        if not queue:
            await query.edit_message_text("Нет слов для проверки, продолжаем учить!")
            await _send_card(update, context)
            return
        context.user_data.update({"quiz_queue": queue, "quiz_idx": 0, "quiz_score": 0})
        text, kb = _make_quiz_question(context)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        return

    if data.startswith("ans_"):
        chosen_idx  = int(data.split("_")[1])
        correct_idx = context.user_data.get("quiz_correct_idx", 0)
        options     = context.user_data.get("quiz_options", [])
        correct     = chosen_idx == correct_idx
        if correct:
            context.user_data["quiz_score"] = context.user_data.get("quiz_score", 0) + 1
            result_line = "\n\n✅ <b>Правильно!</b>"
        else:
            right = H(options[correct_idx])
            result_line = f"\n\n❌ Неверно\nПравильный ответ: <b>{right}</b>"

        mode  = context.user_data.get("mode", "word")
        idx   = context.user_data.get("quiz_idx", 0)
        total = len(context.user_data.get("quiz_queue", []))
        if mode == "word":
            base = _quiz_word_text(
                context.user_data.get("quiz_english", ""),
                context.user_data.get("quiz_transcription", ""),
                idx + 1, total,
            )
        else:
            base = _quiz_verb_text(
                context.user_data.get("quiz_english", ""),
                context.user_data.get("quiz_russian", ""),
                idx + 1, total,
            )
        await query.edit_message_text(
            base + result_line,
            reply_markup=_result_keyboard(chosen_idx, correct_idx, options, correct),
            parse_mode="HTML",
        )
        return

    if data == "next_q":
        context.user_data["quiz_idx"] = context.user_data.get("quiz_idx", 0) + 1
        text, kb = _make_quiz_question(context)
        if text is None:
            score = context.user_data.get("quiz_score", 0)
            total = len(context.user_data.get("quiz_queue", []))
            emoji = "🎉" if score == total else ("👍" if score >= total // 2 else "💪")
            await query.edit_message_text(
                f"{emoji} <b>Проверка завершена!</b>\n\n"
                f"Правильных: <b>{score} из {total}</b>\n\nПродолжаем учить 👇",
                parse_mode="HTML",
            )
            await _send_card(update, context)
        else:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        return


def _record_progress(user_id: int, context, quality: int) -> bool:
    word_id = context.user_data.get("word_id")
    if word_id is None:
        return False
    progress = get_or_create_progress(user_id, word_id)
    new_ef, new_interval, new_reps = sm2(
        quality, progress["ease_factor"], progress["interval"], progress["repetitions"]
    )
    just_learned = update_progress(
        user_id, word_id, new_ef, new_interval, new_reps,
        next_review_date(new_interval),
        correct=1 if quality >= 3 else 0,
    )
    context.user_data["correct_reviews_after"] = (
        progress["correct_reviews"] + (1 if quality >= 3 else 0)
    )
    return just_learned


# ── entry point ────────────────────────────────────────────────────────────────

def main():
    init_db()
    token = os.environ["BOT_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("study",  cmd_study))
    app.add_handler(CommandHandler("stats",  cmd_stats))
    app.add_handler(CommandHandler("sync",   cmd_sync))
    app.add_handler(CommandHandler("debug",  cmd_debug))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_menu_button))
    app.add_handler(CallbackQueryHandler(on_button))

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
