from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_VOCAB = "📚 Учить слова"
BTN_VERBS = "⚡ Учить глаголы"
BTN_STATS = "📊 Статистика"
BTN_SYNC  = "🔄 Синхронизировать"

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_VOCAB), KeyboardButton(text=BTN_VERBS)],
        [KeyboardButton(text=BTN_STATS), KeyboardButton(text=BTN_SYNC)],
    ],
    resize_keyboard=True,
    is_persistent=True,
)
