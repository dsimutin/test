"""Shared session helpers used by both vocabulary and verb handlers.

The FSM `state` holds:
    mode            : 'vocab' | 'verb'
    item_ids        : list[str]
    idx             : int        — index of current card
    session_id      : int        — sessions.id
    quiz            : list[Question dicts]
    q_idx           : int
    correct         : int
    wrong           : int
    sent_card_ids   : list[int]  — bot message_ids of cards shown
    sent_quiz_ids   : list[int]  — bot message_ids of quiz Q/A msgs
"""
from __future__ import annotations

import logging

from aiogram import Bot, types
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile

from ..config import Config
from ..keyboards.inline import card_buttons, quiz_options_keyboard
from ..services import card_renderer, progress, quiz
from ..services.quiz import Question
from ..storage import db
from .states import StudyStates

logger = logging.getLogger(__name__)


# ── message history tracking ──────────────────────────────────────────────

async def _track(state: FSMContext, key: str, message_id: int) -> None:
    data = await state.get_data()
    ids = list(data.get(key) or [])
    ids.append(message_id)
    await state.update_data(**{key: ids})


async def _delete_tracked(bot: Bot, chat_id: int, state: FSMContext,
                          keys: list[str]) -> None:
    """Try to delete every tracked bot message under `keys` and reset them."""
    data = await state.get_data()
    update: dict = {}
    for key in keys:
        ids = data.get(key) or []
        for mid in ids:
            try:
                await bot.delete_message(chat_id, mid)
            except Exception as e:
                logger.debug("delete_message failed for %s: %s", mid, e)
        update[key] = []
    if update:
        await state.update_data(**update)


async def clear_all_history(bot: Bot, chat_id: int, state: FSMContext) -> None:
    """Public: wipe both card and quiz history for this user."""
    await _delete_tracked(bot, chat_id, state,
                           keys=["sent_card_ids", "sent_quiz_ids"])


# ── card flow ─────────────────────────────────────────────────────────────

async def show_next_card(msg: types.Message, state: FSMContext,
                          cfg: Config) -> None:
    data = await state.get_data()
    mode = data["mode"]
    idx  = data["idx"]
    ids  = data["item_ids"]

    if idx >= len(ids):
        await _start_quiz(msg, state, cfg)
        return

    number = f"{idx + 1:02d}"
    if mode == "vocab":
        items = await db.get_vocabulary_by_ids([ids[idx]])
        if not items:
            warn = await msg.answer("⚠️ Слово не найдено, пропускаю.")
            await _track(state, "sent_card_ids", warn.message_id)
            await state.update_data(idx=idx + 1)
            await show_next_card(msg, state, cfg)
            return
        png = await card_renderer.render_vocabulary_card(items[0], number, cfg)
    else:
        items = await db.get_verbs_by_ids([ids[idx]])
        if not items:
            warn = await msg.answer("⚠️ Глагол не найден, пропускаю.")
            await _track(state, "sent_card_ids", warn.message_id)
            await state.update_data(idx=idx + 1)
            await show_next_card(msg, state, cfg)
            return
        png = await card_renderer.render_irregular_card(items[0], number, cfg)

    sent = await msg.answer_photo(
        FSInputFile(png),
        reply_markup=card_buttons(mode, idx),
    )
    await _track(state, "sent_card_ids", sent.message_id)


async def handle_card_button(cb: types.CallbackQuery, state: FSMContext,
                              cfg: Config) -> None:
    """callback_data: card:{mode}:{know|repeat}:{idx}"""
    parts = cb.data.split(":")
    if len(parts) != 4:
        await cb.answer()
        return
    _, mode, action, idx_str = parts
    idx = int(idx_str)
    data = await state.get_data()
    if data.get("mode") != mode or data.get("idx") != idx:
        await cb.answer("Эта карточка уже не активна.")
        return

    item_id = data["item_ids"][idx]
    knew = action == "know"
    if mode == "vocab":
        await progress.mark_vocab_seen(cb.from_user.id, item_id, knew)
    else:
        await progress.mark_verb_seen(cb.from_user.id, item_id, knew)

    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cb.answer("✅ Понял" if knew else "🔁 Повторим")

    await state.update_data(idx=idx + 1)
    await show_next_card(cb.message, state, cfg)


# ── quiz flow ─────────────────────────────────────────────────────────────

async def _start_quiz(msg: types.Message, state: FSMContext,
                       cfg: Config) -> None:
    data = await state.get_data()
    mode = data["mode"]
    ids = data["item_ids"]

    if mode == "vocab":
        items = await db.get_vocabulary_by_ids(ids)
        questions = await quiz.build_vocabulary_quiz(items)
        intro = f"Проверим {len(items)} слов 👇"
    else:
        items = await db.get_verbs_by_ids(ids)
        questions = await quiz.build_verb_quiz(items)
        intro = f"Проверим {len(items)} глаголов 👇"

    if not questions:
        await msg.answer("Не удалось собрать проверку, попробуй позже.")
        await state.clear()
        return

    # 🔒 Anti-cheat: wipe card messages so user can't scroll up
    # and peek during the test.
    await _delete_tracked(msg.bot, msg.chat.id, state,
                           keys=["sent_card_ids"])

    sent = await msg.answer(intro)
    await _track(state, "sent_quiz_ids", sent.message_id)

    await state.set_state(StudyStates.in_quiz)
    await state.update_data(
        quiz=[_q_to_dict(q) for q in questions],
        q_idx=0, correct=0, wrong=0,
    )
    await _ask_question(msg, state)


def _q_to_dict(q: Question) -> dict:
    return {
        "prompt": q.prompt, "options": q.options,
        "correct_idx": q.correct_idx, "item_id": q.item_id,
        "kind": q.kind, "meta": q.meta,
    }


async def _ask_question(msg: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    quiz_list = data["quiz"]
    qi = data["q_idx"]
    if qi >= len(quiz_list):
        await _finish_quiz(msg, state)
        return
    q = quiz_list[qi]
    progress_str = f"<b>{qi + 1} / {len(quiz_list)}</b>\n\n"
    sent = await msg.answer(
        progress_str + f"{q['prompt']}",
        reply_markup=quiz_options_keyboard(qi, q["options"]),
    )
    await _track(state, "sent_quiz_ids", sent.message_id)


async def handle_quiz_answer(cb: types.CallbackQuery,
                              state: FSMContext) -> None:
    """callback_data: quiz:{qidx}:{answer_idx}"""
    parts = cb.data.split(":")
    if len(parts) != 3:
        await cb.answer()
        return
    _, qidx_s, ans_s = parts
    qidx, ans = int(qidx_s), int(ans_s)

    data = await state.get_data()
    quiz_list = data.get("quiz") or []
    if qidx != data.get("q_idx") or qidx >= len(quiz_list):
        await cb.answer("Этот вопрос уже отвечен.")
        return

    q = quiz_list[qidx]
    correct = ans == q["correct_idx"]
    meta = q["meta"]
    mode = data["mode"]

    if mode == "vocab":
        await progress.record_vocab_answer(cb.from_user.id, q["item_id"], correct)
        right_text = f"{meta['word']} — {meta['translation']}"
    else:
        await progress.record_verb_answer(cb.from_user.id, q["item_id"],
                                          correct, q["kind"])
        right_text = (f"{meta['infinitive']} — {meta['past_simple']} — "
                       f"{meta['past_participle']}")

    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    if correct:
        resp = await cb.message.answer("✅ Верно")
    else:
        resp = await cb.message.answer(
            f"❌ Не совсем. Правильно: <b>{right_text}</b>"
        )
    await _track(state, "sent_quiz_ids", resp.message_id)

    await cb.answer()
    new_correct = data.get("correct", 0) + (1 if correct else 0)
    new_wrong   = data.get("wrong",   0) + (0 if correct else 1)
    await state.update_data(
        q_idx=qidx + 1,
        correct=new_correct,
        wrong=new_wrong,
    )
    await _ask_question(cb.message, state)


async def _finish_quiz(msg: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    total = len(data.get("quiz") or [])
    correct = data.get("correct", 0)
    wrong = data.get("wrong", 0)
    pct = int(round(100 * correct / total)) if total else 0

    await db.finish_session(
        data["session_id"], total=total,
        correct=correct, wrong=wrong,
    )
    # Keep the summary message in chat; clear earlier quiz Q/A so the
    # student can't review which were marked wrong (they'll see those
    # words again next session anyway).
    await _delete_tracked(msg.bot, msg.chat.id, state,
                           keys=["sent_quiz_ids"])

    await msg.answer(
        f"🏁 Готово!\n\n"
        f"Правильно: <b>{correct} / {total}</b>  ({pct}%)\n"
        f"Ошибок: <b>{wrong}</b>\n\n"
        f"Слова с ошибками вернутся в повторение."
    )
    await state.clear()
