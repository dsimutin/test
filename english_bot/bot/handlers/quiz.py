from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

from . import _session

router = Router(name="quiz")


@router.callback_query(F.data.startswith("quiz:"))
async def on_quiz_answer(cb: types.CallbackQuery, state: FSMContext) -> None:
    await _session.handle_quiz_answer(cb, state)
