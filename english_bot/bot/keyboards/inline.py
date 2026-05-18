from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def card_buttons(mode: str, idx: int) -> InlineKeyboardMarkup:
    """`mode` ∈ {'vocab', 'verb'}, idx is the card index within session."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Знаю",
                             callback_data=f"card:{mode}:know:{idx}"),
        InlineKeyboardButton(text="🔁 Повторить",
                             callback_data=f"card:{mode}:repeat:{idx}"),
    ]])


def quiz_buttons(qidx: int, n_options: int) -> InlineKeyboardMarkup:
    """One row per option (so long words fit)."""
    rows = [
        [InlineKeyboardButton(text=f"{i+1}",
                              callback_data=f"quiz:{qidx}:{i}")]
        for i in range(n_options)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def quiz_options_keyboard(qidx: int, options: list[str]) -> InlineKeyboardMarkup:
    """Inline kb where each button shows the option text directly."""
    rows = [
        [InlineKeyboardButton(text=opt,
                              callback_data=f"quiz:{qidx}:{i}")]
        for i, opt in enumerate(options)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
