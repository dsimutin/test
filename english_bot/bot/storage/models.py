"""Dataclass models shared across services. SQLite is the source of truth;
these are just typed views for safe passing."""
from dataclasses import dataclass


@dataclass(frozen=True)
class VocabularyItem:
    id: str
    lesson: int
    word: str
    transcription: str
    translation: str
    example: str
    highlight: str
    accent: str
    level: str
    active: bool


@dataclass(frozen=True)
class VerbItem:
    id: str
    lesson: int
    infinitive: str
    past_simple: str
    past_participle: str
    translation: str
    example: str
    highlight: str
    accent: str
    active: bool


@dataclass(frozen=True)
class VocabProgress:
    word_id: str
    status: str            # new | learning | known | repeat
    correct_count: int
    wrong_count: int
    last_seen_at: str | None
    next_review_at: str | None


@dataclass(frozen=True)
class VerbProgress:
    verb_id: str
    status: str
    correct_count: int
    wrong_count: int
    past_simple_errors: int
    past_participle_errors: int
    last_seen_at: str | None
    next_review_at: str | None
