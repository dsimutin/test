"""Translate session events (card-button presses, quiz answers) into
status updates persisted in SQLite."""
from __future__ import annotations

from ..storage import db


# ── card-button presses (mid-session, tentative) ──────────────────────────

async def mark_vocab_seen(user_id: int, word_id: str, knew_it: bool) -> None:
    status = "learning" if knew_it else "repeat"
    await db.upsert_vocab_progress(user_id, word_id, status=status)


async def mark_verb_seen(user_id: int, verb_id: str, knew_it: bool) -> None:
    status = "learning" if knew_it else "repeat"
    await db.upsert_verb_progress(user_id, verb_id, status=status)


# ── quiz answer (final) ───────────────────────────────────────────────────

async def record_vocab_answer(user_id: int, word_id: str,
                              correct: bool) -> None:
    if correct:
        await db.upsert_vocab_progress(
            user_id, word_id, status="known",
            correct_delta=1,
        )
    else:
        await db.upsert_vocab_progress(
            user_id, word_id, status="repeat",
            wrong_delta=1,
        )


async def record_verb_answer(user_id: int, verb_id: str,
                             correct: bool, kind: str) -> None:
    ps_err = 0
    pp_err = 0
    if not correct:
        if kind == "past_simple":
            ps_err = 1
        elif kind == "past_participle":
            pp_err = 1
    if correct:
        await db.upsert_verb_progress(
            user_id, verb_id, status="known",
            correct_delta=1,
        )
    else:
        await db.upsert_verb_progress(
            user_id, verb_id, status="repeat",
            wrong_delta=1, ps_err_delta=ps_err, pp_err_delta=pp_err,
        )
