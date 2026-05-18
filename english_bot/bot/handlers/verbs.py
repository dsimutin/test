from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

from ..config import Config
from ..keyboards.reply import BTN_VERBS
from ..services import selection
from ..storage import db
from . import _session
from .states import StudyStates

router = Router(name="verbs")


@router.message(F.text == BTN_VERBS)
async def on_start_verbs(msg: types.Message, state: FSMContext,
                          cfg: Config) -> None:
    await state.clear()
    ids = await selection.pick_verb_ids(msg.from_user.id,
                                        cfg.verb_batch_size)
    if not ids:
        await msg.answer("Пока нет глаголов для изучения. Нажми 🔄 Синхронизировать.")
        return

    session_id = await db.start_session(msg.from_user.id, mode="verb")
    await state.set_state(StudyStates.showing_cards)
    await state.update_data(
        mode="verb", item_ids=ids, idx=0,
        session_id=session_id,
    )
    await msg.answer(f"⚡ Учим {len(ids)} глаголов.")
    await _session.show_next_card(msg, state, cfg)


@router.callback_query(F.data.startswith("card:verb:"))
async def on_verb_card_button(cb: types.CallbackQuery, state: FSMContext,
                               cfg: Config) -> None:
    await _session.handle_card_button(cb, state, cfg)
