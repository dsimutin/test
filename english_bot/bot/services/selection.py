"""Pick next N items per student.

Pool = student_assignments for this user.
If the user has no assignments AND has no config row in _students yet
(brand new, sync hasn't run), fall back to global active pool so they
aren't blocked from learning.

Order pool by priority:
  1. repeat
  2. learning
  3. new
  4. oldest last_seen_at
"""
from __future__ import annotations

from ..storage import db


async def pick_vocabulary_ids(telegram_id: int, n: int) -> list[str]:
    pool = await _candidate_pool(telegram_id, "word")
    progress = await db.get_vocab_progress_map(telegram_id)
    return _order_pool(pool, progress, n)


async def pick_verb_ids(telegram_id: int, n: int) -> list[str]:
    pool = await _candidate_pool(telegram_id, "verb")
    progress = await db.get_verb_progress_map(telegram_id)
    return _order_pool(pool, progress, n)


async def _candidate_pool(telegram_id: int, item_type: str) -> list[str]:
    assigned = await db.get_assigned_ids(telegram_id, item_type)
    if assigned:
        return assigned
    # No assignments yet — either sync hasn't run, or teacher set
    # spreadsheet_id to one that has no rows. Fall back to global so
    # the student isn't blocked.
    if item_type == "word":
        return await db.fetch_active_vocabulary_ids()
    return await db.fetch_active_verb_ids()


def _order_pool(pool: list[str], progress: dict, n: int) -> list[str]:
    if not pool:
        return []
    repeat, learning, seen_old, new = [], [], [], []
    for i in pool:
        p = progress.get(i)
        if p is None:
            new.append(i)
        elif p.status == "repeat":
            repeat.append((p.last_seen_at or "", i))
        elif p.status == "learning":
            learning.append((p.last_seen_at or "", i))
        elif p.status == "known":
            seen_old.append((p.last_seen_at or "", i))
    repeat.sort()
    learning.sort()
    seen_old.sort()
    ordered = (
        [i for _, i in repeat]
        + [i for _, i in learning]
        + new
        + [i for _, i in seen_old]
    )
    return ordered[:n]
