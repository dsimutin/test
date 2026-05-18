"""Pick next N items by priority:
  1. repeat
  2. learning
  3. new (no progress yet)
  4. oldest last_seen_at
"""
from __future__ import annotations

from ..storage import db


async def pick_vocabulary_ids(telegram_id: int, n: int) -> list[str]:
    progress = await db.get_vocab_progress_map(telegram_id)
    all_ids = await db.fetch_active_vocabulary_ids()
    return _pick(all_ids, progress, n)


async def pick_verb_ids(telegram_id: int, n: int) -> list[str]:
    progress = await db.get_verb_progress_map(telegram_id)
    all_ids = await db.fetch_active_verb_ids()
    return _pick(all_ids, progress, n)


def _pick(all_ids: list[str], progress: dict, n: int) -> list[str]:
    repeat, learning, seen_old, new = [], [], [], []
    for i in all_ids:
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
