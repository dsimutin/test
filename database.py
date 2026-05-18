import os
import random
import sqlite3
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

DB_PATH = os.environ.get("DB_PATH", "flashcards.db")
LEARNED_THRESHOLD = 5  # correct answers to mark word as learned


def _conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    parent = os.path.dirname(DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = _conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS words (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            english         TEXT    NOT NULL,
            russian         TEXT    NOT NULL,
            transcription   TEXT,
            example         TEXT,
            card_type       TEXT    NOT NULL DEFAULT 'word',
            past_simple     TEXT,
            past_participle TEXT
        )
    """)
    # migrations
    existing = {row[1] for row in c.execute("PRAGMA table_info(words)")}
    for col, typedef in [
        ("transcription",   "TEXT"),
        ("past_simple",     "TEXT"),
        ("past_participle", "TEXT"),
        ("card_type",       "TEXT NOT NULL DEFAULT 'word'"),
    ]:
        if col not in existing:
            c.execute(f"ALTER TABLE words ADD COLUMN {col} {typedef}")

    c.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_words_unique
        ON words(english, card_type)
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_progress (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            word_id         INTEGER NOT NULL REFERENCES words(id),
            ease_factor     REAL    DEFAULT 2.5,
            interval        INTEGER DEFAULT 0,
            repetitions     INTEGER DEFAULT 0,
            next_review     TEXT    DEFAULT (date('now')),
            last_review     TEXT,
            total_reviews   INTEGER DEFAULT 0,
            correct_reviews INTEGER DEFAULT 0,
            learned_written INTEGER DEFAULT 0,
            UNIQUE(user_id, word_id)
        )
    """)
    # migration: add learned_written if missing
    existing_up = {row[1] for row in c.execute("PRAGMA table_info(user_progress)")}
    if "learned_written" not in existing_up:
        c.execute("ALTER TABLE user_progress ADD COLUMN learned_written INTEGER DEFAULT 0")

    conn.commit()
    conn.close()


def sync_words(words: List[Tuple]) -> int:
    """Each item: (english, russian, transcription, example, card_type, past_simple, past_participle)"""
    conn = _conn()
    c = conn.cursor()
    new_count = 0
    for english, russian, transcription, example, card_type, past_simple, past_participle in words:
        c.execute(
            """
            INSERT OR IGNORE INTO words
                (english, russian, transcription, example, card_type, past_simple, past_participle)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                english.strip(),
                russian.strip(),
                transcription.strip() if transcription else None,
                example.strip() if example else None,
                card_type,
                past_simple.strip() if past_simple else None,
                past_participle.strip() if past_participle else None,
            ),
        )
        if c.rowcount:
            new_count += 1
    conn.commit()
    conn.close()
    return new_count


def get_due_cards(user_id: int, card_type: str, limit: int = 1) -> List[Tuple]:
    today = date.today().isoformat()
    conn = _conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT w.id, w.english, w.russian, w.transcription, w.example,
               w.past_simple, w.past_participle
        FROM words w
        LEFT JOIN user_progress up ON w.id = up.word_id AND up.user_id = ?
        WHERE w.card_type = ?
          AND (
              up.id IS NULL
              OR (up.next_review <= ? AND COALESCE(up.correct_reviews, 0) < ?)
          )
        ORDER BY COALESCE(up.next_review, '0000-00-00'), RANDOM()
        LIMIT ?
        """,
        (user_id, card_type, today, LEARNED_THRESHOLD, limit),
    )
    result = c.fetchall()
    conn.close()
    return result


def get_words_by_ids(word_ids: List[int]) -> List[Tuple]:
    if not word_ids:
        return []
    conn = _conn()
    c = conn.cursor()
    placeholders = ",".join("?" * len(word_ids))
    c.execute(
        f"SELECT id, english, russian, transcription, past_simple, card_type "
        f"FROM words WHERE id IN ({placeholders})",
        word_ids,
    )
    result = c.fetchall()
    conn.close()
    return result


def get_random_options(word_id: int, card_type: str, field: str, count: int = 2) -> List[str]:
    conn = _conn()
    c = conn.cursor()
    c.execute(
        f"SELECT {field} FROM words WHERE card_type = ? AND id != ? AND {field} IS NOT NULL",
        (card_type, word_id),
    )
    rows = [r[0] for r in c.fetchall()]
    conn.close()
    return random.sample(rows, min(count, len(rows)))


def get_or_create_progress(user_id: int, word_id: int) -> Dict:
    conn = _conn()
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO user_progress (user_id, word_id) VALUES (?, ?)",
        (user_id, word_id),
    )
    conn.commit()
    c.execute(
        "SELECT ease_factor, interval, repetitions, correct_reviews, learned_written "
        "FROM user_progress WHERE user_id = ? AND word_id = ?",
        (user_id, word_id),
    )
    row = c.fetchone()
    conn.close()
    return {
        "ease_factor": row[0],
        "interval": row[1],
        "repetitions": row[2],
        "correct_reviews": row[3],
        "learned_written": row[4],
    }


def update_progress(
    user_id: int, word_id: int,
    ease_factor: float, interval: int, repetitions: int,
    next_review: str, correct: int,
) -> bool:
    """Returns True if this update caused the word to become learned for the first time."""
    conn = _conn()
    c = conn.cursor()
    c.execute(
        """
        UPDATE user_progress SET
            ease_factor     = ?,
            interval        = ?,
            repetitions     = ?,
            next_review     = ?,
            last_review     = ?,
            total_reviews   = total_reviews + 1,
            correct_reviews = correct_reviews + ?
        WHERE user_id = ? AND word_id = ?
        """,
        (ease_factor, interval, repetitions, next_review,
         datetime.now().isoformat(), correct, user_id, word_id),
    )
    # Check if just crossed learned threshold for the first time
    c.execute(
        "SELECT correct_reviews, learned_written FROM user_progress "
        "WHERE user_id = ? AND word_id = ?",
        (user_id, word_id),
    )
    row = c.fetchone()
    just_learned = (
        row is not None
        and row[0] >= LEARNED_THRESHOLD
        and row[1] == 0
    )
    if just_learned:
        c.execute(
            "UPDATE user_progress SET learned_written = 1 WHERE user_id = ? AND word_id = ?",
            (user_id, word_id),
        )
    conn.commit()
    conn.close()
    return just_learned


def get_card_counts(user_id: int) -> Dict:
    today = date.today().isoformat()
    conn = _conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT w.card_type, COUNT(*)
        FROM words w
        LEFT JOIN user_progress up ON w.id = up.word_id AND up.user_id = ?
        WHERE up.id IS NULL
           OR (up.next_review <= ? AND COALESCE(up.correct_reviews, 0) < ?)
        GROUP BY w.card_type
        """,
        (user_id, today, LEARNED_THRESHOLD),
    )
    rows = c.fetchall()
    conn.close()
    return {ct: n for ct, n in rows}


def get_user_stats(user_id: int) -> Dict:
    conn = _conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT w.card_type,
               COUNT(DISTINCT w.id),
               COUNT(CASE WHEN COALESCE(up.correct_reviews,0) >= ? THEN 1 END),
               COALESCE(SUM(up.total_reviews), 0),
               COALESCE(SUM(up.correct_reviews), 0)
        FROM words w
        LEFT JOIN user_progress up ON w.id = up.word_id AND up.user_id = ?
        GROUP BY w.card_type
        """,
        (LEARNED_THRESHOLD, user_id),
    )
    rows = c.fetchall()
    conn.close()
    return {
        ct: {"total": total, "learned": learned,
             "total_reviews": tr, "correct_reviews": cr}
        for ct, total, learned, tr, cr in rows
    }
