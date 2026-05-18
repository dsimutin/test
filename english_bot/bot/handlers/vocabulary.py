from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

from ..config import Config
from ..keyboards.reply import BTN_VOCAB
from ..services import selection
from ..storage import db
from . import _session
from .states import StudyStates

router = Router(name="vocabulary")


@router.message(F.text == BTN_VOCAB)
async def on_start_vocab(msg: types.Message, state: FSMContext,
                         cfg: Config) -> None:
    await state.clear()
    ids = await selection.pick_vocabulary_ids(msg.from_user.id,
                                              cfg.vocab_batch_size)
    if not ids:
        await msg.answer("Пока нет слов для изучения. Нажми 🔄 Синхронизировать.")
        return

    session_id = await db.start_session(msg.from_user.id, mode="vocab")
    await state.set_state(StudyStates.showing_cards)
    await state.update_data(
        mode="vocab", item_ids=ids, idx=0,
        session_id=session_id,
    )
    await msg.answer(f"📚 Учим {len(ids)} слов.")
    await _session.show_next_card(msg, state, cfg)


@router.callback_query(F.data.startswith("card:vocab:"))
async def on_vocab_card_button(cb: types.CallbackQuery, state: FSMContext,
                                cfg: Config) -> None:
    await _session.handle_card_button(cb, state, cfg)
