"""SQLite layer: schema, CRUD, queries.

Single async connection module — every call opens its own connection via
the `connect()` helper so handlers stay simple. SQLite handles its own
locking; for this load it's fine.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import aiosqlite

from .models import (
    VerbItem,
    VerbProgress,
    VocabularyItem,
    VocabProgress,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vocabulary (
    id TEXT PRIMARY KEY,
    lesson INTEGER,
    word TEXT NOT NULL,
    transcription TEXT,
    translation TEXT NOT NULL,
    example TEXT,
    highlight TEXT,
    accent TEXT,
    level TEXT,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS irregular_verbs (
    id TEXT PRIMARY KEY,
    lesson INTEGER,
    infinitive TEXT NOT NULL,
    past_simple TEXT NOT NULL,
    past_participle TEXT NOT NULL,
    translation TEXT NOT NULL,
    example TEXT,
    highlight TEXT,
    accent TEXT,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS vocabulary_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    word_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    correct_count INTEGER NOT NULL DEFAULT 0,
    wrong_count INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT,
    next_review_at TEXT,
    UNIQUE(telegram_id, word_id)
);

CREATE TABLE IF NOT EXISTS verb_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    verb_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    correct_count INTEGER NOT NULL DEFAULT 0,
    wrong_count INTEGER NOT NULL DEFAULT 0,
    past_simple_errors INTEGER NOT NULL DEFAULT 0,
    past_participle_errors INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT,
    next_review_at TEXT,
    UNIQUE(telegram_id, verb_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    total_items INTEGER DEFAULT 0,
    correct_answers INTEGER DEFAULT 0,
    wrong_answers INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_vocab_progress_user
    ON vocabulary_progress(telegram_id);
CREATE INDEX IF NOT EXISTS idx_verb_progress_user
    ON verb_progress(telegram_id);

-- Per-student content assignments. If a student has rows here, they see
-- ONLY these items. If they have no rows AND the _students sheet is
-- empty, they see all active content (default / single-tenant mode).
CREATE TABLE IF NOT EXISTS student_assignments (
    telegram_id INTEGER NOT NULL,
    item_type   TEXT    NOT NULL,   -- 'word' | 'verb'
    item_id     TEXT    NOT NULL,
    PRIMARY KEY (telegram_id, item_type, item_id)
);
CREATE INDEX IF NOT EXISTS idx_assignments_user
    ON student_assignments(telegram_id, item_type);

-- Tiny mirror of the _students sheet (so selection logic can ask
-- "is multi-tenant mode active?" without round-tripping to Google).
CREATE TABLE IF NOT EXISTS students_config (
    telegram_id     INTEGER PRIMARY KEY,
    name            TEXT,
    spreadsheet_id  TEXT,
    active_lessons  TEXT,
    updated_at      TEXT NOT NULL
);
"""

_DB_PATH: Path | None = None


def configure(db_path: Path) -> None:
    global _DB_PATH
    _DB_PATH = db_path


def connect() -> aiosqlite.Connection:
    if _DB_PATH is None:
        raise RuntimeError("Call db.configure(path) first")
    return aiosqlite.connect(_DB_PATH)


async def init() -> None:
    async with connect() as conn:
        await conn.executescript(SCHEMA)
        await conn.commit()


# ── users ─────────────────────────────────────────────────────────────────

async def ensure_user(telegram_id: int, username: str | None,
                      first_name: str | None) -> None:
    async with connect() as conn:
        await conn.execute(
            """INSERT OR IGNORE INTO users
               (telegram_id, username, first_name, created_at)
               VALUES (?, ?, ?, ?)""",
            (telegram_id, username, first_name, datetime.utcnow().isoformat()),
        )
        await conn.commit()


# ── content (upsert from sheets) ──────────────────────────────────────────

async def upsert_vocabulary(items: Iterable[VocabularyItem]) -> int:
    async with connect() as conn:
        n = 0
        for it in items:
            await conn.execute(
                """INSERT INTO vocabulary
                       (id, lesson, word, transcription, translation,
                        example, highlight, accent, level, active)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       lesson=excluded.lesson,
                       word=excluded.word,
                       transcription=excluded.transcription,
                       translation=excluded.translation,
                       example=excluded.example,
                       highlight=excluded.highlight,
                       accent=excluded.accent,
                       level=excluded.level,
                       active=excluded.active""",
                (it.id, it.lesson, it.word, it.transcription, it.translation,
                 it.example, it.highlight, it.accent, it.level, int(it.active)),
            )
            n += 1
        await conn.commit()
        return n


async def upsert_verbs(items: Iterable[VerbItem]) -> int:
    async with connect() as conn:
        n = 0
        for it in items:
            await conn.execute(
                """INSERT INTO irregular_verbs
                       (id, lesson, infinitive, past_simple, past_participle,
                        translation, example, highlight, accent, active)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       lesson=excluded.lesson,
                       infinitive=excluded.infinitive,
                       past_simple=excluded.past_simple,
                       past_participle=excluded.past_participle,
                       translation=excluded.translation,
                       example=excluded.example,
                       highlight=excluded.highlight,
                       accent=excluded.accent,
                       active=excluded.active""",
                (it.id, it.lesson, it.infinitive, it.past_simple,
                 it.past_participle, it.translation, it.example,
                 it.highlight, it.accent, int(it.active)),
            )
            n += 1
        await conn.commit()
        return n


# ── content fetch helpers ─────────────────────────────────────────────────

def _vocab_row(row) -> VocabularyItem:
    return VocabularyItem(
        id=row[0], lesson=row[1] or 0, word=row[2], transcription=row[3] or "",
        translation=row[4], example=row[5] or "", highlight=row[6] or row[2],
        accent=row[7] or "#2F7D5B", level=row[8] or "", active=bool(row[9]),
    )


def _verb_row(row) -> VerbItem:
    return VerbItem(
        id=row[0], lesson=row[1] or 0, infinitive=row[2], past_simple=row[3],
        past_participle=row[4], translation=row[5], example=row[6] or "",
        highlight=row[7] or row[3], accent=row[8] or "#D77A22",
        active=bool(row[9]),
    )


async def get_vocabulary_by_ids(ids: list[str]) -> list[VocabularyItem]:
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    async with connect() as conn:
        cur = await conn.execute(
            f"SELECT id, lesson, word, transcription, translation, example, "
            f"highlight, accent, level, active FROM vocabulary "
            f"WHERE id IN ({placeholders})",
            ids,
        )
        rows = await cur.fetchall()
    by_id = {r[0]: _vocab_row(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


async def get_verbs_by_ids(ids: list[str]) -> list[VerbItem]:
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    async with connect() as conn:
        cur = await conn.execute(
            f"SELECT id, lesson, infinitive, past_simple, past_participle, "
            f"translation, example, highlight, accent, active "
            f"FROM irregular_verbs WHERE id IN ({placeholders})",
            ids,
        )
        rows = await cur.fetchall()
    by_id = {r[0]: _verb_row(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


# ── progress / status getters ─────────────────────────────────────────────

async def get_vocab_progress_map(telegram_id: int) -> dict[str, VocabProgress]:
    async with connect() as conn:
        cur = await conn.execute(
            "SELECT word_id, status, correct_count, wrong_count, "
            "last_seen_at, next_review_at FROM vocabulary_progress "
            "WHERE telegram_id = ?",
            (telegram_id,),
        )
        rows = await cur.fetchall()
    return {r[0]: VocabProgress(*r) for r in rows}


async def get_verb_progress_map(telegram_id: int) -> dict[str, VerbProgress]:
    async with connect() as conn:
        cur = await conn.execute(
            "SELECT verb_id, status, correct_count, wrong_count, "
            "past_simple_errors, past_participle_errors, "
            "last_seen_at, next_review_at FROM verb_progress "
            "WHERE telegram_id = ?",
            (telegram_id,),
        )
        rows = await cur.fetchall()
    return {r[0]: VerbProgress(*r) for r in rows}


# ── pools for selection (filtered: active=1) ──────────────────────────────

async def fetch_active_vocabulary_ids() -> list[str]:
    async with connect() as conn:
        cur = await conn.execute(
            "SELECT id FROM vocabulary WHERE active = 1 ORDER BY lesson, id"
        )
        return [r[0] for r in await cur.fetchall()]


async def fetch_active_verb_ids() -> list[str]:
    async with connect() as conn:
        cur = await conn.execute(
            "SELECT id FROM irregular_verbs WHERE active = 1 ORDER BY lesson, id"
        )
        return [r[0] for r in await cur.fetchall()]


# ── distractors (other words/verbs to use as wrong answers) ───────────────

async def random_other_translations(exclude_id: str, n: int) -> list[str]:
    async with connect() as conn:
        cur = await conn.execute(
            "SELECT translation FROM vocabulary WHERE id != ? AND active = 1 "
            "ORDER BY RANDOM() LIMIT ?", (exclude_id, n),
        )
        return [r[0] for r in await cur.fetchall()]


async def random_other_words(exclude_id: str, n: int) -> list[str]:
    async with connect() as conn:
        cur = await conn.execute(
            "SELECT word FROM vocabulary WHERE id != ? AND active = 1 "
            "ORDER BY RANDOM() LIMIT ?", (exclude_id, n),
        )
        return [r[0] for r in await cur.fetchall()]


async def random_other_past_simple(exclude_id: str, n: int) -> list[str]:
    async with connect() as conn:
        cur = await conn.execute(
            "SELECT past_simple FROM irregular_verbs WHERE id != ? "
            "AND active = 1 ORDER BY RANDOM() LIMIT ?", (exclude_id, n),
        )
        return [r[0] for r in await cur.fetchall()]


async def random_other_past_participle(exclude_id: str, n: int) -> list[str]:
    async with connect() as conn:
        cur = await conn.execute(
            "SELECT past_participle FROM irregular_verbs WHERE id != ? "
            "AND active = 1 ORDER BY RANDOM() LIMIT ?", (exclude_id, n),
        )
        return [r[0] for r in await cur.fetchall()]


# ── progress mutations ────────────────────────────────────────────────────

def _next_review(status: str) -> str:
    today = date.today()
    delta = {"new": 0, "learning": 1, "known": 3, "repeat": 0}.get(status, 1)
    return (today + timedelta(days=delta)).isoformat()


async def upsert_vocab_progress(telegram_id: int, word_id: str,
                                status: str, correct_delta: int = 0,
                                wrong_delta: int = 0) -> None:
    now = datetime.utcnow().isoformat()
    nxt = _next_review(status)
    async with connect() as conn:
        await conn.execute(
            """INSERT INTO vocabulary_progress
                   (telegram_id, word_id, status, correct_count, wrong_count,
                    last_seen_at, next_review_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(telegram_id, word_id) DO UPDATE SET
                   status = excluded.status,
                   correct_count = vocabulary_progress.correct_count + ?,
                   wrong_count = vocabulary_progress.wrong_count + ?,
                   last_seen_at = excluded.last_seen_at,
                   next_review_at = excluded.next_review_at""",
            (telegram_id, word_id, status, correct_delta, wrong_delta,
             now, nxt, correct_delta, wrong_delta),
        )
        await conn.commit()


async def upsert_verb_progress(telegram_id: int, verb_id: str,
                               status: str, correct_delta: int = 0,
                               wrong_delta: int = 0,
                               ps_err_delta: int = 0,
                               pp_err_delta: int = 0) -> None:
    now = datetime.utcnow().isoformat()
    nxt = _next_review(status)
    async with connect() as conn:
        await conn.execute(
            """INSERT INTO verb_progress
                   (telegram_id, verb_id, status, correct_count, wrong_count,
                    past_simple_errors, past_participle_errors,
                    last_seen_at, next_review_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(telegram_id, verb_id) DO UPDATE SET
                   status = excluded.status,
                   correct_count = verb_progress.correct_count + ?,
                   wrong_count = verb_progress.wrong_count + ?,
                   past_simple_errors = verb_progress.past_simple_errors + ?,
                   past_participle_errors = verb_progress.past_participle_errors + ?,
                   last_seen_at = excluded.last_seen_at,
                   next_review_at = excluded.next_review_at""",
            (telegram_id, verb_id, status, correct_delta, wrong_delta,
             ps_err_delta, pp_err_delta, now, nxt,
             correct_delta, wrong_delta, ps_err_delta, pp_err_delta),
        )
        await conn.commit()


# ── sessions ──────────────────────────────────────────────────────────────

async def start_session(telegram_id: int, mode: str) -> int:
    async with connect() as conn:
        cur = await conn.execute(
            "INSERT INTO sessions (telegram_id, mode, started_at) VALUES (?, ?, ?)",
            (telegram_id, mode, datetime.utcnow().isoformat()),
        )
        await conn.commit()
        return cur.lastrowid


async def finish_session(session_id: int, total: int,
                         correct: int, wrong: int) -> None:
    async with connect() as conn:
        await conn.execute(
            "UPDATE sessions SET finished_at=?, total_items=?, "
            "correct_answers=?, wrong_answers=? WHERE id=?",
            (datetime.utcnow().isoformat(), total, correct, wrong, session_id),
        )
        await conn.commit()


async def last_session_date(telegram_id: int) -> str | None:
    async with connect() as conn:
        cur = await conn.execute(
            "SELECT finished_at FROM sessions WHERE telegram_id = ? "
            "AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1",
            (telegram_id,),
        )
        row = await cur.fetchone()
    return row[0] if row else None


# ── stats counts ──────────────────────────────────────────────────────────

async def list_all_students() -> list[dict]:
    """Returns one row per known user with summary stats."""
    async with connect() as conn:
        cur = await conn.execute(
            """SELECT u.telegram_id, u.username, u.first_name, u.created_at,
                  (SELECT COUNT(*) FROM vocabulary_progress
                       WHERE telegram_id=u.telegram_id AND status='known'),
                  (SELECT COUNT(*) FROM vocabulary_progress
                       WHERE telegram_id=u.telegram_id
                         AND status IN ('repeat','learning')),
                  (SELECT COUNT(*) FROM verb_progress
                       WHERE telegram_id=u.telegram_id AND status='known'),
                  (SELECT COUNT(*) FROM verb_progress
                       WHERE telegram_id=u.telegram_id
                         AND status IN ('repeat','learning')),
                  (SELECT MAX(finished_at) FROM sessions
                       WHERE telegram_id=u.telegram_id AND finished_at IS NOT NULL)
               FROM users u
               ORDER BY u.first_name COLLATE NOCASE"""
        )
        rows = await cur.fetchall()
    return [
        {
            "telegram_id": r[0], "username": r[1], "first_name": r[2],
            "created_at": r[3],
            "words_known": r[4], "words_review": r[5],
            "verbs_known": r[6], "verbs_review": r[7],
            "last_session": r[8],
        }
        for r in rows
    ]


async def student_detail(telegram_id: int) -> dict | None:
    """Detailed view: counts + recent sessions + worst-known items."""
    async with connect() as conn:
        cur = await conn.execute(
            "SELECT username, first_name, created_at FROM users "
            "WHERE telegram_id = ?", (telegram_id,)
        )
        user = await cur.fetchone()
        if not user:
            return None
        cur = await conn.execute(
            "SELECT mode, started_at, finished_at, total_items, "
            "correct_answers, wrong_answers FROM sessions "
            "WHERE telegram_id = ? ORDER BY id DESC LIMIT 5",
            (telegram_id,),
        )
        sessions = await cur.fetchall()
        cur = await conn.execute(
            """SELECT v.word, v.translation, vp.wrong_count, vp.correct_count
               FROM vocabulary_progress vp
               JOIN vocabulary v ON v.id = vp.word_id
               WHERE vp.telegram_id = ? AND vp.wrong_count > 0
               ORDER BY vp.wrong_count DESC LIMIT 10""",
            (telegram_id,),
        )
        worst_words = await cur.fetchall()
        cur = await conn.execute(
            """SELECT iv.infinitive, iv.translation,
                      vp.wrong_count, vp.correct_count,
                      vp.past_simple_errors, vp.past_participle_errors
               FROM verb_progress vp
               JOIN irregular_verbs iv ON iv.id = vp.verb_id
               WHERE vp.telegram_id = ? AND vp.wrong_count > 0
               ORDER BY vp.wrong_count DESC LIMIT 10""",
            (telegram_id,),
        )
        worst_verbs = await cur.fetchall()
    return {
        "telegram_id": telegram_id,
        "username": user[0],
        "first_name": user[1],
        "created_at": user[2],
        "sessions": [
            {"mode": s[0], "started_at": s[1], "finished_at": s[2],
             "total": s[3], "correct": s[4], "wrong": s[5]}
            for s in sessions
        ],
        "worst_words": [
            {"word": w[0], "translation": w[1],
             "wrong": w[2], "correct": w[3]}
            for w in worst_words
        ],
        "worst_verbs": [
            {"infinitive": v[0], "translation": v[1],
             "wrong": v[2], "correct": v[3],
             "ps_errors": v[4], "pp_errors": v[5]}
            for v in worst_verbs
        ],
    }


# ── multi-tenant: students_config + student_assignments ──────────────────

async def replace_students_config(rows: list[dict]) -> None:
    """rows: [{telegram_id, name, spreadsheet_id, active_lessons}, ...]"""
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    async with connect() as conn:
        await conn.execute("DELETE FROM students_config")
        if rows:
            await conn.executemany(
                """INSERT INTO students_config
                       (telegram_id, name, spreadsheet_id,
                        active_lessons, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                [(r["telegram_id"], r.get("name") or "",
                  r.get("spreadsheet_id") or "",
                  r.get("active_lessons") or "*",
                  now) for r in rows],
            )
        await conn.commit()


async def list_students_config() -> list[dict]:
    async with connect() as conn:
        cur = await conn.execute(
            "SELECT telegram_id, name, spreadsheet_id, active_lessons "
            "FROM students_config"
        )
        return [
            {"telegram_id": r[0], "name": r[1],
             "spreadsheet_id": r[2], "active_lessons": r[3]}
            for r in await cur.fetchall()
        ]


async def has_students_config() -> bool:
    """True if teacher has set up multi-tenant mode (any _students rows)."""
    async with connect() as conn:
        cur = await conn.execute("SELECT 1 FROM students_config LIMIT 1")
        return (await cur.fetchone()) is not None


async def replace_student_assignments(telegram_id: int, item_type: str,
                                       ids: list[str]) -> None:
    async with connect() as conn:
        await conn.execute(
            "DELETE FROM student_assignments "
            "WHERE telegram_id = ? AND item_type = ?",
            (telegram_id, item_type),
        )
        if ids:
            await conn.executemany(
                "INSERT OR IGNORE INTO student_assignments "
                "(telegram_id, item_type, item_id) VALUES (?, ?, ?)",
                [(telegram_id, item_type, i) for i in ids],
            )
        await conn.commit()


async def get_assigned_ids(telegram_id: int, item_type: str) -> list[str]:
    async with connect() as conn:
        cur = await conn.execute(
            "SELECT item_id FROM student_assignments "
            "WHERE telegram_id = ? AND item_type = ?",
            (telegram_id, item_type),
        )
        return [r[0] for r in await cur.fetchall()]


async def is_progress_empty() -> bool:
    """True if there is no per-user data at all (fresh container)."""
    async with connect() as conn:
        cur = await conn.execute(
            "SELECT EXISTS(SELECT 1 FROM users LIMIT 1) "
            "+ EXISTS(SELECT 1 FROM vocabulary_progress LIMIT 1) "
            "+ EXISTS(SELECT 1 FROM verb_progress LIMIT 1)"
        )
        row = await cur.fetchone()
    return (row[0] or 0) == 0


async def dump_users() -> list[tuple]:
    async with connect() as conn:
        cur = await conn.execute(
            "SELECT telegram_id, username, first_name, created_at FROM users"
        )
        return await cur.fetchall()


async def dump_vocab_progress() -> list[tuple]:
    async with connect() as conn:
        cur = await conn.execute(
            "SELECT telegram_id, word_id, status, correct_count, wrong_count, "
            "last_seen_at, next_review_at FROM vocabulary_progress"
        )
        return await cur.fetchall()


async def dump_verb_progress() -> list[tuple]:
    async with connect() as conn:
        cur = await conn.execute(
            "SELECT telegram_id, verb_id, status, correct_count, wrong_count, "
            "past_simple_errors, past_participle_errors, "
            "last_seen_at, next_review_at FROM verb_progress"
        )
        return await cur.fetchall()


async def dump_sessions() -> list[tuple]:
    async with connect() as conn:
        cur = await conn.execute(
            "SELECT telegram_id, mode, started_at, finished_at, total_items, "
            "correct_answers, wrong_answers FROM sessions"
        )
        return await cur.fetchall()


async def restore_users(rows: list[tuple]) -> None:
    if not rows:
        return
    async with connect() as conn:
        await conn.executemany(
            "INSERT OR IGNORE INTO users "
            "(telegram_id, username, first_name, created_at) "
            "VALUES (?, ?, ?, ?)", rows,
        )
        await conn.commit()


async def restore_vocab_progress(rows: list[tuple]) -> None:
    if not rows:
        return
    async with connect() as conn:
        await conn.executemany(
            "INSERT OR REPLACE INTO vocabulary_progress "
            "(telegram_id, word_id, status, correct_count, wrong_count, "
            "last_seen_at, next_review_at) VALUES (?,?,?,?,?,?,?)", rows,
        )
        await conn.commit()


async def restore_verb_progress(rows: list[tuple]) -> None:
    if not rows:
        return
    async with connect() as conn:
        await conn.executemany(
            "INSERT OR REPLACE INTO verb_progress "
            "(telegram_id, verb_id, status, correct_count, wrong_count, "
            "past_simple_errors, past_participle_errors, "
            "last_seen_at, next_review_at) VALUES (?,?,?,?,?,?,?,?,?)", rows,
        )
        await conn.commit()


async def restore_sessions(rows: list[tuple]) -> None:
    if not rows:
        return
    async with connect() as conn:
        await conn.executemany(
            "INSERT INTO sessions "
            "(telegram_id, mode, started_at, finished_at, total_items, "
            "correct_answers, wrong_answers) VALUES (?,?,?,?,?,?,?)", rows,
        )
        await conn.commit()


async def export_all_progress() -> tuple[list[dict], list[dict]]:
    """Returns (vocab_rows, verb_rows) for CSV export."""
    async with connect() as conn:
        cur = await conn.execute(
            """SELECT u.telegram_id, u.first_name, u.username,
                      v.word, v.translation, v.lesson,
                      vp.status, vp.correct_count, vp.wrong_count,
                      vp.last_seen_at, vp.next_review_at
               FROM vocabulary_progress vp
               JOIN users u       ON u.telegram_id = vp.telegram_id
               JOIN vocabulary v  ON v.id = vp.word_id
               ORDER BY u.first_name, v.lesson, v.word"""
        )
        vocab_rows = [
            {
                "telegram_id": r[0], "student": r[1], "username": r[2],
                "word": r[3], "translation": r[4], "lesson": r[5],
                "status": r[6], "correct": r[7], "wrong": r[8],
                "last_seen": r[9], "next_review": r[10],
            }
            for r in await cur.fetchall()
        ]
        cur = await conn.execute(
            """SELECT u.telegram_id, u.first_name, u.username,
                      iv.infinitive, iv.past_simple, iv.past_participle,
                      iv.translation, iv.lesson,
                      vp.status, vp.correct_count, vp.wrong_count,
                      vp.past_simple_errors, vp.past_participle_errors,
                      vp.last_seen_at, vp.next_review_at
               FROM verb_progress vp
               JOIN users u            ON u.telegram_id = vp.telegram_id
               JOIN irregular_verbs iv ON iv.id = vp.verb_id
               ORDER BY u.first_name, iv.lesson, iv.infinitive"""
        )
        verb_rows = [
            {
                "telegram_id": r[0], "student": r[1], "username": r[2],
                "infinitive": r[3], "past_simple": r[4], "past_participle": r[5],
                "translation": r[6], "lesson": r[7],
                "status": r[8], "correct": r[9], "wrong": r[10],
                "ps_errors": r[11], "pp_errors": r[12],
                "last_seen": r[13], "next_review": r[14],
            }
            for r in await cur.fetchall()
        ]
    return vocab_rows, verb_rows


async def vocab_stats(telegram_id: int) -> dict:
    async with connect() as conn:
        cur = await conn.execute(
            """SELECT
                   (SELECT COUNT(*) FROM vocabulary_progress
                       WHERE telegram_id=? AND status='known'),
                   (SELECT COUNT(*) FROM vocabulary_progress
                       WHERE telegram_id=? AND status IN ('repeat','learning')),
                   (SELECT COUNT(*) FROM vocabulary WHERE active=1)
                   - (SELECT COUNT(*) FROM vocabulary_progress
                       WHERE telegram_id=?)""",
            (telegram_id, telegram_id, telegram_id),
        )
        row = await cur.fetchone()
    return {"known": row[0], "review": row[1], "new": max(row[2], 0)}


async def verb_stats(telegram_id: int) -> dict:
    async with connect() as conn:
        cur = await conn.execute(
            """SELECT
                   (SELECT COUNT(*) FROM verb_progress
                       WHERE telegram_id=? AND status='known'),
                   (SELECT COUNT(*) FROM verb_progress
                       WHERE telegram_id=? AND status IN ('repeat','learning')),
                   (SELECT COUNT(*) FROM irregular_verbs WHERE active=1)
                   - (SELECT COUNT(*) FROM verb_progress
                       WHERE telegram_id=?)""",
            (telegram_id, telegram_id, telegram_id),
        )
        row = await cur.fetchone()
    return {"known": row[0], "review": row[1], "new": max(row[2], 0)}
