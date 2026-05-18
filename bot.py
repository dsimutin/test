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
from sheets import fetch_vocabulary, write_learned_word
from spaced_repetition import next_review_date, sm2

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LINE = "──────────────────"

# After how many cards to offer a self-check quiz
QUIZ_AFTER_WORDS = 10
QUIZ_AFTER_VERBS = 5
QUIZ_QUESTIONS   = 5   # how many questions in each quiz round

MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📖 Учить слова"), KeyboardButton("⚡ Учить глаголы")],
        [KeyboardButton("📊 Статистика"),  KeyboardButton("🔄 Синхронизировать")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)


# ── card text builders ─────────────────────────────────────────────────────────

def _word_card_text(english, russian, transcription, example) -> str:
    lines = [LINE, f"📖  *{english}*", LINE]
    if transcription:
        lines.append(f"\n🔤  _{transcription}_")
    lines.append(f"\n🇷🇺  *{russian}*")
    if example:
        lines.append(f"\n💬  _{example}_")
    return "\n".join(lines)


def _verb_card_text(infinitive, russian, past_simple, past_participle) -> str:
    ps = past_simple or "—"
    pp = past_participle or "—"
    return "\n".join([
        LINE,
        f"⚡  *Неправильный глагол*",
        LINE,
        f"\n*V1*  {infinitive}   —   _{russian}_",
        f"*V2*  {ps}",
        f"*V3*  {pp}",
    ])


def _quiz_question_text(mode, english, russian, transcription) -> str:
    if mode == "word":
        lines = [LINE, "🎯  *Проверка*", LINE, "\nВыбери правильный перевод:\n", f"*{english}*"]
        if transcription:
            lines.append(f"_{transcription}_")
        return "\n".join(lines)
    else:
        return "\n".join([
            LINE, "🎯  *Проверка глагола*", LINE,
            f"\nВыбери правильный *Past Simple (V2)*:\n",
            f"*{english}*  —  _{russian}_",
        ])


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
            f"⚡ Неправильные глаголы — {counts['verb']} карточек", callback_data="mode_verb"
        )])
    return InlineKeyboardMarkup(rows)


# ── session helpers ────────────────────────────────────────────────────────────

def _session_threshold(mode: str) -> int:
    return QUIZ_AFTER_WORDS if mode == "word" else QUIZ_AFTER_VERBS


def _add_to_session(context: ContextTypes.DEFAULT_TYPE, word_id: int):
    session = context.user_data.setdefault("session_ids", [])
    if word_id not in session:
        session.append(word_id)
    context.user_data["session_count"] = context.user_data.get("session_count", 0) + 1


def _should_offer_quiz(context: ContextTypes.DEFAULT_TYPE, mode: str) -> bool:
    count = context.user_data.get("session_count", 0)
    threshold = _session_threshold(mode)
    return count > 0 and count % threshold == 0


def _reset_session(context: ContextTypes.DEFAULT_TYPE):
    context.user_data["session_ids"] = []
    context.user_data["session_count"] = 0


# ── send card ──────────────────────────────────────────────────────────────────

async def _send_card(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    user_id = update.effective_user.id
    mode = context.user_data.get("mode", "word")
    due = get_due_cards(user_id, card_type=mode, limit=1)

    if not due:
        other = "verb" if mode == "word" else "word"
        if get_due_cards(user_id, card_type=other, limit=1):
            other_name = "слова" if other == "word" else "неправильные глаголы"
            text = f"🎉 В этом разделе всё готово!\n\nПопробуй *{other_name}* 👇"
        else:
            text = "🎉 На сегодня всё! Все карточки повторены.\nВозвращайся завтра 👋"
        if edit:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, parse_mode="Markdown",
                                            reply_markup=MAIN_MENU)
        return

    word_id, english, russian, transcription, example, past_simple, past_participle = due[0]
    context.user_data.update({
        "word_id": word_id,
        "english": english,
        "russian": russian,
        "transcription": transcription or "",
        "example": example or "",
        "past_simple": past_simple or "",
        "past_participle": past_participle or "",
    })

    if mode == "word":
        text = _word_card_text(english, russian, transcription, example)
    else:
        text = _verb_card_text(english, russian, past_simple, past_participle)

    if edit:
        await update.callback_query.edit_message_text(
            text, reply_markup=_card_keyboard(), parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            text, reply_markup=_card_keyboard(), parse_mode="Markdown"
        )


async def _offer_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a separate quiz-offer message after reaching threshold."""
    mode = context.user_data.get("mode", "word")
    count = context.user_data.get("session_count", 0)
    label = "слов" if mode == "word" else "глаголов"
    await update.effective_chat.send_message(
        f"💡 Отлично! Ты прошёл *{count} {label}*.\nПроверим, что запомнилось?",
        reply_markup=_start_quiz_keyboard(),
        parse_mode="Markdown",
    )


# ── quiz logic ─────────────────────────────────────────────────────────────────

def _build_quiz_queue(context: ContextTypes.DEFAULT_TYPE) -> list:
    """Pick up to QUIZ_QUESTIONS words from session to quiz."""
    session_ids = context.user_data.get("session_ids", [])
    mode = context.user_data.get("mode", "word")
    pool = get_words_by_ids(session_ids)
    # pool rows: (id, english, russian, transcription, past_simple, card_type)
    pool = [r for r in pool if r[5] == mode]
    sample = random.sample(pool, min(QUIZ_QUESTIONS, len(pool)))
    return sample


def _next_quiz_question(update_or_query, context: ContextTypes.DEFAULT_TYPE, edit: bool):
    """Prepare next question state. Returns (text, keyboard) or None if quiz done."""
    queue = context.user_data.get("quiz_queue", [])
    idx   = context.user_data.get("quiz_idx", 0)
    if idx >= len(queue):
        return None, None

    row = queue[idx]
    word_id, english, russian, transcription, past_simple, _ = row
    mode = context.user_data.get("mode", "word")

    if mode == "word":
        correct = russian
        wrongs  = get_random_options(word_id, "word", "russian", count=2)
    else:
        correct = past_simple or russian
        wrongs  = get_random_options(word_id, "verb", "past_simple", count=2)

    options = wrongs + [correct]
    random.shuffle(options)
    correct_idx = options.index(correct)

    context.user_data["quiz_correct_idx"] = correct_idx
    context.user_data["quiz_options"]     = options
    context.user_data["quiz_word_id"]     = word_id
    context.user_data["quiz_english"]     = english
    context.user_data["quiz_russian"]     = russian

    total = len(queue)
    text  = f"_Вопрос {idx + 1} из {total}_\n\n"
    text += _quiz_question_text(mode, english, russian, transcription)
    kb = _options_keyboard(options)
    return text, kb


# ── command handlers ───────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я помогу тебе учить английские слова.\n\n"
        "Выбери раздел в меню внизу 👇",
        reply_markup=MAIN_MENU,
    )


async def _start_mode(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    user_id = update.effective_user.id
    counts  = get_card_counts(user_id)

    if not counts.get(mode):
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
    """Fallback /study — shows mode picker if both available."""
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
    lines = [f"📊 *Твоя статистика*\n{LINE}\n"]
    for card_type, s in stats.items():
        label = "📖 Слова" if card_type == "word" else "⚡ Неправильные глаголы"
        pct   = ""
        if s["total_reviews"]:
            pct = f"  ·  точность {round(s['correct_reviews'] / s['total_reviews'] * 100)}%"
        lines.append(f"{label}\nВыучено: *{s['learned']}* / {s['total']}{pct}\n")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown",
                                    reply_markup=MAIN_MENU)


async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Синхронизирую слова из Google Sheets…")
    try:
        words = fetch_vocabulary()
        new   = sync_words(words)
        wc = sum(1 for w in words if w[4] == "word")
        vc = sum(1 for w in words if w[4] == "verb")
        await msg.edit_text(
            f"✅ Готово! Добавлено новых карточек: *{new}*\n\n"
            f"📖 Слов: {wc}\n⚡ Неправильных глаголов: {vc}",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.exception("Sync failed")
        await msg.edit_text(f"❌ Ошибка синхронизации:\n`{exc}`", parse_mode="Markdown")


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

    # Mode selection from inline picker
    if data.startswith("mode_"):
        mode = data[5:]
        context.user_data["mode"] = mode
        _reset_session(context)
        await _send_card(update, context, edit=True)
        return

    # Know / Repeat on a study card
    if data in ("know", "again"):
        quality = 5 if data == "know" else 1
        word_id = context.user_data.get("word_id")
        if word_id:
            _add_to_session(context, word_id)
            just_learned = _record_progress(user_id, context, quality)
            if just_learned:
                english   = context.user_data.get("english", "")
                card_type = context.user_data.get("mode", "word")
                user_name = update.effective_user.full_name or str(user_id)
                write_learned_word(user_name, user_id, english, card_type,
                                   context.user_data.get("correct_reviews_after", 0))

        mode = context.user_data.get("mode", "word")
        if _should_offer_quiz(context, mode):
            # First advance to next card silently, then offer quiz
            await _send_card(update, context, edit=True)
            await _offer_quiz(update, context)
        else:
            await _send_card(update, context, edit=True)
        return

    # Quiz offer buttons
    if data == "skip_quiz":
        await query.edit_message_text("▶ Продолжаем учить!", parse_mode="Markdown")
        await _send_card(update, context)
        return

    if data == "start_quiz":
        queue = _build_quiz_queue(context)
        if not queue:
            await query.edit_message_text("Нет слов для проверки. Продолжаем учить!")
            await _send_card(update, context)
            return
        context.user_data["quiz_queue"] = queue
        context.user_data["quiz_idx"]   = 0
        context.user_data["quiz_score"] = 0
        text, kb = _next_quiz_question(update, context, edit=False)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        return

    # Quiz answer
    if data.startswith("ans_"):
        chosen_idx  = int(data.split("_")[1])
        correct_idx = context.user_data.get("quiz_correct_idx", 0)
        options     = context.user_data.get("quiz_options", [])
        correct     = chosen_idx == correct_idx

        if correct:
            context.user_data["quiz_score"] = context.user_data.get("quiz_score", 0) + 1
            result_line = "\n\n✅ *Правильно!*"
        else:
            right = options[correct_idx]
            result_line = f"\n\n❌ Неверно\nПравильный ответ: *{right}*"

        mode    = context.user_data.get("mode", "word")
        english = context.user_data.get("quiz_english", "")
        russian = context.user_data.get("quiz_russian", "")
        base    = _quiz_question_text(mode, english, russian,
                                      context.user_data.get("transcription", ""))
        idx_label = f"_Вопрос {context.user_data.get('quiz_idx', 0) + 1} из {len(context.user_data.get('quiz_queue', []))}_\n\n"

        await query.edit_message_text(
            idx_label + base + result_line,
            reply_markup=_result_keyboard(chosen_idx, correct_idx, options, correct),
            parse_mode="Markdown",
        )
        return

    # Next quiz question
    if data == "next_q":
        context.user_data["quiz_idx"] = context.user_data.get("quiz_idx", 0) + 1
        text, kb = _next_quiz_question(update, context, edit=True)

        if text is None:
            # Quiz finished
            score = context.user_data.get("quiz_score", 0)
            total = len(context.user_data.get("quiz_queue", []))
            emoji = "🎉" if score == total else ("👍" if score >= total // 2 else "💪")
            summary = (
                f"{emoji} *Проверка завершена!*\n\n"
                f"Правильных ответов: *{score} из {total}*\n\n"
                f"Продолжаем учить 👇"
            )
            await query.edit_message_text(summary, parse_mode="Markdown")
            await _send_card(update, context)
        else:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        return


def _record_progress(user_id: int, context: ContextTypes.DEFAULT_TYPE, quality: int) -> bool:
    word_id = context.user_data.get("word_id")
    if word_id is None:
        return False
    progress = get_or_create_progress(user_id, word_id)
    new_ef, new_interval, new_reps = sm2(
        quality,
        progress["ease_factor"],
        progress["interval"],
        progress["repetitions"],
    )
    just_learned = update_progress(
        user_id, word_id, new_ef, new_interval, new_reps,
        next_review_date(new_interval),
        correct=1 if quality >= 3 else 0,
    )
    # Store current correct count for sheets write
    context.user_data["correct_reviews_after"] = progress["correct_reviews"] + (1 if quality >= 3 else 0)
    return just_learned


# ── entry point ────────────────────────────────────────────────────────────────

def main():
    init_db()
    token = os.environ["BOT_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("study", cmd_study))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("sync", cmd_sync))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_menu_button))
    app.add_handler(CallbackQueryHandler(on_button))

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
