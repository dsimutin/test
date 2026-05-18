import logging
import os
import random

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from database import (
    get_card_counts,
    get_due_cards,
    get_or_create_progress,
    get_random_options,
    get_user_stats,
    init_db,
    sync_words,
    update_progress,
)
from sheets import fetch_vocabulary
from spaced_repetition import next_review_date, sm2

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LINE = "──────────────────"


# ── card text builders ────────────────────────────────────────────────────────

def _word_card_text(english, russian, transcription, example, position=None) -> str:
    lines = [
        LINE,
        f"📖  *{english}*",
        LINE,
    ]
    if transcription:
        lines.append(f"\n🔤  _{transcription}_")
    lines.append(f"\n🇷🇺  *{russian}*")
    if example:
        lines.append(f"\n💬  _{example}_")
    if position:
        lines.append(f"\n{LINE}\n_{position}_")
    return "\n".join(lines)


def _verb_card_text(infinitive, russian, past_simple, past_participle, position=None) -> str:
    ps = past_simple or "—"
    pp = past_participle or "—"
    lines = [
        LINE,
        f"⚡  *Неправильный глагол*",
        LINE,
        f"\n*V1*  {infinitive}   —   _{russian}_",
        f"*V2*  {ps}",
        f"*V3*  {pp}",
    ]
    if position:
        lines.append(f"\n{LINE}\n_{position}_")
    return "\n".join(lines)


def _quiz_word_text(english, transcription) -> str:
    lines = [
        LINE,
        f"🎯  *Проверка*",
        LINE,
        f"\nВыбери правильный перевод:\n",
        f"*{english}*",
    ]
    if transcription:
        lines.append(f"_{transcription}_")
    return "\n".join(lines)


def _quiz_verb_text(infinitive, russian) -> str:
    return "\n".join([
        LINE,
        f"🎯  *Проверка глагола*",
        LINE,
        f"\nВыбери правильный *Past Simple (V2)*:\n",
        f"*{infinitive}*  —  _{russian}_",
    ])


# ── keyboards ─────────────────────────────────────────────────────────────────

def _study_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Знаю", callback_data="know"),
            InlineKeyboardButton("🔄 Повторить", callback_data="again"),
        ],
        [InlineKeyboardButton("🎯 Проверить себя", callback_data="quiz")],
    ])


def _options_keyboard(options: list[str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(opt, callback_data=f"ans_{i}")] for i, opt in enumerate(options)]
    )


def _result_keyboard(chosen_idx: int, correct_idx: int, options: list[str], correct: bool) -> InlineKeyboardMarkup:
    rows = []
    for i, opt in enumerate(options):
        if i == correct_idx:
            rows.append([InlineKeyboardButton(f"✓  {opt}", callback_data="noop")])
        elif i == chosen_idx and not correct:
            rows.append([InlineKeyboardButton(f"✗  {opt}", callback_data="noop")])
    rows.append([InlineKeyboardButton("→ Следующая карточка", callback_data="next")])
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


# ── core: show card / show quiz ───────────────────────────────────────────────

def _prepare_card(user_id: int, mode: str) -> dict | None:
    """Fetch next due card and return a state dict, or None if nothing due."""
    due = get_due_cards(user_id, card_type=mode, limit=1)
    if not due:
        return None
    row = due[0]
    word_id, english, russian, transcription, example, past_simple, past_participle = row
    return {
        "word_id": word_id,
        "english": english,
        "russian": russian,
        "transcription": transcription or "",
        "example": example or "",
        "past_simple": past_simple or "",
        "past_participle": past_participle or "",
    }


async def _send_card(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    user_id = update.effective_user.id
    mode = context.user_data.get("mode", "word")
    card = _prepare_card(user_id, mode)

    if card is None:
        other = "verb" if mode == "word" else "word"
        other_card = _prepare_card(user_id, other)
        if other_card:
            other_name = "слова" if other == "word" else "неправильные глаголы"
            text = f"🎉 В этом разделе всё готово на сегодня!\n\nПопробуй *{other_name}* — напиши /study"
        else:
            text = "🎉 На сегодня всё! Все карточки повторены.\nВозвращайся завтра 👋"
        if edit:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, parse_mode="Markdown")
        return

    context.user_data.update(card)

    if mode == "word":
        text = _word_card_text(
            card["english"], card["russian"],
            card["transcription"], card["example"],
        )
    else:
        text = _verb_card_text(
            card["english"], card["russian"],
            card["past_simple"], card["past_participle"],
        )

    kb = _study_keyboard()
    if edit:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")


async def _send_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data.get("mode", "word")
    word_id   = context.user_data["word_id"]
    english   = context.user_data["english"]
    russian   = context.user_data["russian"]
    transcription = context.user_data.get("transcription", "")
    past_simple   = context.user_data.get("past_simple", "")

    if mode == "word":
        correct_answer = russian
        wrong_answers  = get_random_options(word_id, "word", "russian", count=2)
        text = _quiz_word_text(english, transcription)
    else:
        correct_answer = past_simple
        wrong_answers  = get_random_options(word_id, "verb", "past_simple", count=2)
        text = _quiz_verb_text(english, russian)

    options = wrong_answers + [correct_answer]
    random.shuffle(options)
    correct_idx = options.index(correct_answer)

    context.user_data["quiz_options"]     = options
    context.user_data["quiz_correct_idx"] = correct_idx

    await update.callback_query.edit_message_text(
        text,
        reply_markup=_options_keyboard(options),
        parse_mode="Markdown",
    )


# ── command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я помогу тебе учить английские слова.\n\n"
        "Команды:\n"
        "/study — начать повторение карточек\n"
        "/stats — твоя статистика\n"
        "/sync — загрузить слова из Google Sheets"
    )


async def cmd_study(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    counts = get_card_counts(user_id)

    if not counts:
        await update.message.reply_text(
            "📭 Карточек пока нет. Загрузи слова командой /sync"
        )
        return

    if len(counts) == 1:
        context.user_data["mode"] = next(iter(counts))
        await _send_card(update, context)
        return

    await update.message.reply_text(
        "Что будем учить сегодня?",
        reply_markup=_mode_keyboard(counts),
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats = get_user_stats(user_id)

    lines = [f"📊 *Твоя статистика*\n{LINE}\n"]
    for card_type, s in stats.items():
        label = "📖 Слова" if card_type == "word" else "⚡ Неправильные глаголы"
        pct = ""
        if s["total_reviews"]:
            pct = f"   точность {round(s['correct_reviews'] / s['total_reviews'] * 100)}%"
        lines.append(f"{label}\nВыучено: {s['learned']} / {s['total']}{pct}\n")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Синхронизирую слова из Google Sheets…")
    try:
        words = fetch_vocabulary()
        new = sync_words(words)
        word_count = sum(1 for w in words if w[4] == "word")
        verb_count = sum(1 for w in words if w[4] == "verb")
        await msg.edit_text(
            f"✅ Готово! Добавлено новых карточек: *{new}*\n\n"
            f"📖 Слов в таблице: {word_count}\n"
            f"⚡ Неправильных глаголов: {verb_count}",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.exception("Sync failed")
        await msg.edit_text(f"❌ Ошибка синхронизации:\n`{exc}`", parse_mode="Markdown")


# ── callback handler ──────────────────────────────────────────────────────────

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data == "noop":
        return

    if data.startswith("mode_"):
        context.user_data["mode"] = data[5:]
        await _send_card(update, context, edit=True)
        return

    if data == "quiz":
        await _send_quiz(update, context)
        return

    if data.startswith("ans_"):
        chosen_idx  = int(data.split("_")[1])
        correct_idx = context.user_data.get("quiz_correct_idx", 0)
        options     = context.user_data.get("quiz_options", [])
        correct     = chosen_idx == correct_idx
        quality     = 5 if correct else 1

        # save result
        _record_progress(user_id, context, quality)
        context.user_data["quiz_quality"] = quality

        mode = context.user_data.get("mode", "word")
        english = context.user_data.get("english", "")
        russian = context.user_data.get("russian", "")
        transcription = context.user_data.get("transcription", "")
        past_simple   = context.user_data.get("past_simple", "")

        if mode == "word":
            base_text = _quiz_word_text(english, transcription)
        else:
            base_text = _quiz_verb_text(english, russian)

        if correct:
            result_line = "\n\n✅ *Правильно!*"
        else:
            right = options[correct_idx]
            result_line = f"\n\n❌ Неверно\nПравильный ответ: *{right}*"

        await query.edit_message_text(
            base_text + result_line,
            reply_markup=_result_keyboard(chosen_idx, correct_idx, options, correct),
            parse_mode="Markdown",
        )
        return

    if data in ("know", "again", "next"):
        if data == "know":
            _record_progress(user_id, context, quality=5)
        elif data == "again":
            _record_progress(user_id, context, quality=1)
        # "next" — progress already saved after quiz answer
        await _send_card(update, context, edit=True)
        return


def _record_progress(user_id: int, context: ContextTypes.DEFAULT_TYPE, quality: int):
    word_id = context.user_data.get("word_id")
    if word_id is None:
        return
    progress = get_or_create_progress(user_id, word_id)
    new_ef, new_interval, new_reps = sm2(
        quality,
        progress["ease_factor"],
        progress["interval"],
        progress["repetitions"],
    )
    update_progress(
        user_id, word_id,
        new_ef, new_interval, new_reps,
        next_review_date(new_interval),
        correct=1 if quality >= 3 else 0,
    )


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    init_db()
    token = os.environ["BOT_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("study", cmd_study))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("sync", cmd_sync))
    app.add_handler(CallbackQueryHandler(on_button))

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
