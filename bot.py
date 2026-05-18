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
    get_due_cards,
    get_or_create_progress,
    get_total_words,
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


# ── helpers ──────────────────────────────────────────────────────────────────

def _card_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("👁 Показать перевод", callback_data="show_answer")]]
    )


def _rating_keyboard():
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("❌ Снова", callback_data="rate_1"),
            InlineKeyboardButton("😐 Сложно", callback_data="rate_3"),
            InlineKeyboardButton("✅ Легко", callback_data="rate_5"),
        ]]
    )


def _build_card_text(english: str, example: str | None) -> str:
    text = f"🇬🇧 *{english}*"
    if example:
        text += f"\n\n_{example}_"
    return text


async def _send_next_card(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    user_id = update.effective_user.id
    due = get_due_cards(user_id, limit=1)

    if not due:
        text = "🎉 На сегодня всё! Все карточки повторены.\nВозвращайся завтра 👋"
        if edit:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return

    word_id, english, russian, example = due[0]
    context.user_data.update(
        {
            "word_id": word_id,
            "english": english,
            "russian": russian,
            "example": example or "",
        }
    )

    text = _build_card_text(english, example)
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
    await _send_next_card(update, context)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    s = get_user_stats(user_id)
    total = get_total_words()
    due_count = len(get_due_cards(user_id, limit=9999))

    lines = [
        "📊 *Твоя статистика*\n",
        f"📚 Слов в базе: {total}",
        f"✅ Изучено слов: {s['learned']}",
        f"📅 Ждут повторения сегодня: {due_count}",
        f"🔄 Всего ответов: {s['total_reviews']}",
        f"🎯 Правильных: {s['correct_reviews']}",
    ]
    if s["total_reviews"]:
        pct = round(s["correct_reviews"] / s["total_reviews"] * 100)
        lines.append(f"💯 Точность: {pct}%")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Синхронизирую слова из Google Sheets…")
    try:
        words = fetch_vocabulary()
        new = sync_words(words)
        await msg.edit_text(
            f"✅ Готово! Добавлено новых слов: *{new}* (всего в таблице: {len(words)})",
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

    if data == "show_answer":
        english = context.user_data.get("english", "")
        russian = context.user_data.get("russian", "")
        example = context.user_data.get("example", "")

        text = _build_card_text(english, example)
        text += f"\n\n🇷🇺 *{russian}*"

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
