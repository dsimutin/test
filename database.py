import os
import sqlite3
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

DB_PATH = os.environ.get("DB_PATH", "flashcards.db")


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
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            english TEXT    NOT NULL UNIQUE,
            russian TEXT    NOT NULL,
            example TEXT
        )
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
            UNIQUE(user_id, word_id)
        )
    """)
    conn.commit()
    conn.close()


def sync_words(words: List[Tuple[str, str, Optional[str]]]) -> int:
    conn = _conn()
    c = conn.cursor()
    new_count = 0
    for english, russian, example in words:
        c.execute(
            "INSERT OR IGNORE INTO words (english, russian, example) VALUES (?, ?, ?)",
            (english.strip(), russian.strip(), example.strip() if example else None),
        )
        if c.rowcount:
            new_count += 1
    conn.commit()
    conn.close()
    return new_count


def get_due_cards(user_id: int, limit: int = 10) -> List[Tuple]:
    today = date.today().isoformat()
    conn = _conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT w.id, w.english, w.russian, w.example
        FROM words w
        LEFT JOIN user_progress up ON w.id = up.word_id AND up.user_id = ?
        WHERE up.id IS NULL OR up.next_review <= ?
        ORDER BY COALESCE(up.next_review, '0000-00-00'), RANDOM()
        LIMIT ?
        """,
        (user_id, today, limit),
    )
    result = c.fetchall()
    conn.close()
    return result


def get_or_create_progress(user_id: int, word_id: int) -> Dict:
    conn = _conn()
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO user_progress (user_id, word_id) VALUES (?, ?)",
        (user_id, word_id),
    )
    conn.commit()
    c.execute(
        "SELECT ease_factor, interval, repetitions FROM user_progress "
        "WHERE user_id = ? AND word_id = ?",
        (user_id, word_id),
    )
    row = c.fetchone()
    conn.close()
    return {"ease_factor": row[0], "interval": row[1], "repetitions": row[2]}


def update_progress(
    user_id: int,
    word_id: int,
    ease_factor: float,
    interval: int,
    repetitions: int,
    next_review: str,
    correct: int,
):
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
        (
            ease_factor,
            interval,
            repetitions,
            next_review,
            datetime.now().isoformat(),
            correct,
            user_id,
            word_id,
        ),
    )
    conn.commit()
    conn.close()


def get_user_stats(user_id: int) -> Dict:
    conn = _conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT
            COUNT(CASE WHEN repetitions > 0 THEN 1 END),
            COALESCE(SUM(total_reviews), 0),
            COALESCE(SUM(correct_reviews), 0)
        FROM user_progress WHERE user_id = ?
        """,
        (user_id,),
    )
    learned, total, correct = c.fetchone()
    conn.close()
    return {"learned": learned or 0, "total_reviews": total, "correct_reviews": correct}


def get_total_words() -> int:
    conn = _conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM words")
    count = c.fetchone()[0]
    conn.close()
    return count
