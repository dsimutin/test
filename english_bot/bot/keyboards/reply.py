from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

# Common
BTN_VOCAB = "📚 Учить слова"
BTN_VERBS = "⚡ Учить глаголы"
BTN_STATS = "📊 Моя статистика"

# Teacher-only
BTN_STUDENTS = "👥 Ученики"
BTN_SYNC     = "🔄 Синхронизация"
BTN_EXPORT   = "📋 Экспорт CSV"


def main_menu(is_teacher: bool) -> ReplyKeyboardMarkup:
    """Reply keyboard. Teachers see 3 extra buttons."""
    rows = [
        [KeyboardButton(text=BTN_VOCAB), KeyboardButton(text=BTN_VERBS)],
        [KeyboardButton(text=BTN_STATS)],
    ]
    if is_teacher:
        rows[1].append(KeyboardButton(text=BTN_STUDENTS))
        rows.append([KeyboardButton(text=BTN_SYNC),
                     KeyboardButton(text=BTN_EXPORT)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
    )
