import logging
import os

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


# ── keyboards ─────────────────────────────────────────────────────────────────

def _mode_keyboard(counts: dict) -> InlineKeyboardMarkup:
    words_due = counts.get("word", 0)
    verbs_due = counts.get("verb", 0)
    rows = []
    if words_due:
        rows.append([InlineKeyboardButton(
            f"📖 Слова ({words_due} карточек)", callback_data="mode_word"
        )])
    if verbs_due:
        rows.append([InlineKeyboardButton(
            f"⚡ Неправильные глаголы ({verbs_due} карточек)", callback_data="mode_verb"
        )])
    if not rows:
        return None
    return InlineKeyboardMarkup(rows)


def _card_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("👁 Показать ответ", callback_data="show_answer")]]
    )


def _rating_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Снова", callback_data="rate_1"),
        InlineKeyboardButton("😐 Сложно", callback_data="rate_3"),
        InlineKeyboardButton("✅ Легко", callback_data="rate_5"),
    ]])


# ── card text builders ────────────────────────────────────────────────────────

def _word_card_question(english: str, example: str | None) -> str:
    text = f"📖 *{english}*"
    if example:
        text += f"\n\n_{example}_"
    return text


def _word_card_answer(english: str, russian: str, example: str | None) -> str:
    text = f"📖 *{english}*"
    if example:
        text += f"\n_{example}_"
    text += f"\n\n🇷🇺 *{russian}*"
    return text


def _verb_card_question(infinitive: str) -> str:
    return (
        f"⚡ *{infinitive}*\n\n"
        f"Как будет Past Simple и Past Participle?"
    )


def _verb_card_answer(infinitive: str, russian: str, past_simple: str, past_participle: str) -> str:
    return (
        f"⚡ *{infinitive}* — {russian}\n\n"
        f"▸ Past Simple: *{past_simple}*\n"
        f"▸ Past Participle: *{past_participle}*"
    )


# ── send next card ────────────────────────────────────────────────────────────

async def _send_next_card(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    user_id = update.effective_user.id
    mode = context.user_data.get("mode", "word")

    due = get_due_cards(user_id, card_type=mode, limit=1)

    if not due:
        # Try to switch to other mode if it has cards
        other_mode = "verb" if mode == "word" else "word"
        other_due = get_due_cards(user_id, card_type=other_mode, limit=1)
        if other_due:
            mode_name = "слова" if other_mode == "word" else "неправильные глаголы"
            text = f"🎉 В этом разделе всё готово на сегодня!\n\nПереключиться на *{mode_name}*? Напиши /study"
        else:
            text = "🎉 На сегодня всё! Все карточки повторены.\nВозвращайся завтра 👋"
        if edit:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, parse_mode="Markdown")
        return

    word_id, english, russian, example, past_simple, past_participle = due[0]
    context.user_data.update({
        "word_id": word_id,
        "english": english,
        "russian": russian,
        "example": example or "",
        "past_simple": past_simple or "",
        "past_participle": past_participle or "",
    })

    if mode == "verb":
        text = _verb_card_question(english)
    else:
        text = _word_card_question(english, example)

    if edit:
        await update.callback_query.edit_message_text(
            text, reply_markup=_card_keyboard(), parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            text, reply_markup=_card_keyboard(), parse_mode="Markdown"
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
            "📭 Карточек пока нет. Сначала загрузи слова командой /sync"
        )
        return

    # If only one type available — start immediately
    if len(counts) == 1:
        context.user_data["mode"] = next(iter(counts))
        await _send_next_card(update, context)
        return

    keyboard = _mode_keyboard(counts)
    await update.message.reply_text(
        "Что будем учить сегодня?",
        reply_markup=keyboard,
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats = get_user_stats(user_id)

    lines = ["📊 *Твоя статистика*\n"]

    for card_type, s in stats.items():
        label = "📖 Слова" if card_type == "word" else "⚡ Неправильные глаголы"
        pct = ""
        if s["total_reviews"]:
            pct = f" (точность {round(s['correct_reviews'] / s['total_reviews'] * 100)}%)"
        lines.append(
            f"{label}: выучено {s['learned']}/{s['total']}{pct}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Синхронизирую слова из Google Sheets…")
    try:
        words = fetch_vocabulary()
        new = sync_words(words)
        word_count = sum(1 for w in words if w[3] == "word")
        verb_count = sum(1 for w in words if w[3] == "verb")
        await msg.edit_text(
            f"✅ Готово! Добавлено новых карточек: *{new}*\n"
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
    user_id = update.effective_user.id
    data = query.data

    if data.startswith("mode_"):
        context.user_data["mode"] = data[5:]  # "word" or "verb"
        await _send_next_card(update, context, edit=True)

    elif data == "show_answer":
        mode = context.user_data.get("mode", "word")
        english = context.user_data.get("english", "")
        russian = context.user_data.get("russian", "")
        example = context.user_data.get("example", "")
        past_simple = context.user_data.get("past_simple", "")
        past_participle = context.user_data.get("past_participle", "")

        if mode == "verb":
            text = _verb_card_answer(english, russian, past_simple, past_participle)
        else:
            text = _word_card_answer(english, russian, example)

        await query.edit_message_text(
            text, reply_markup=_rating_keyboard(), parse_mode="Markdown"
        )

    elif data.startswith("rate_"):
        quality = int(data.split("_")[1])
        word_id = context.user_data.get("word_id")

        if word_id is None:
            await query.edit_message_text("Сессия устарела. Напиши /study заново.")
            return

        progress = get_or_create_progress(user_id, word_id)
        new_ef, new_interval, new_reps = sm2(
            quality,
            progress["ease_factor"],
            progress["interval"],
            progress["repetitions"],
        )
        update_progress(
            user_id,
            word_id,
            new_ef,
            new_interval,
            new_reps,
            next_review_date(new_interval),
            correct=1 if quality >= 3 else 0,
        )

        await _send_next_card(update, context, edit=True)


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
