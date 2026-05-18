"""Quiz builders. Each call returns a list of `Question` objects.

A Question has:
  * prompt       — visible text shown to user
  * options      — 4 strings (shuffled)
  * correct_idx  — index of the correct option
  * item_id      — id of the underlying word/verb
  * meta         — small dict with hint info (kind, original word/translation)
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Literal

from ..storage import db
from ..storage.models import VerbItem, VocabularyItem

QuizKind = Literal[
    "en_to_ru", "ru_to_en", "sentence_gap",      # vocab
    "past_simple", "past_participle", "verb_gap",  # verbs
]


@dataclass
class Question:
    prompt: str
    options: list[str]
    correct_idx: int
    item_id: str
    kind: QuizKind
    meta: dict = field(default_factory=dict)


# ── vocabulary quiz ───────────────────────────────────────────────────────

async def build_vocabulary_quiz(items: list[VocabularyItem]) -> list[Question]:
    qs: list[Question] = []
    kinds: list[QuizKind] = ["en_to_ru", "ru_to_en", "sentence_gap"]
    for i, it in enumerate(items):
        kind = kinds[i % len(kinds)]
        if kind == "en_to_ru":
            wrong = await db.random_other_translations(it.id, 3)
            opts = (wrong + [it.translation])[:4]
            random.shuffle(opts)
            qs.append(Question(
                prompt=it.word,
                options=opts,
                correct_idx=opts.index(it.translation),
                item_id=it.id, kind=kind,
                meta={"word": it.word, "translation": it.translation},
            ))
        elif kind == "ru_to_en":
            wrong = await db.random_other_words(it.id, 3)
            opts = (wrong + [it.word])[:4]
            random.shuffle(opts)
            qs.append(Question(
                prompt=it.translation,
                options=opts,
                correct_idx=opts.index(it.word),
                item_id=it.id, kind=kind,
                meta={"word": it.word, "translation": it.translation},
            ))
        else:  # sentence_gap
            if not it.example:
                # fallback to en_to_ru if no example
                wrong = await db.random_other_translations(it.id, 3)
                opts = (wrong + [it.translation])[:4]
                random.shuffle(opts)
                qs.append(Question(
                    prompt=it.word, options=opts,
                    correct_idx=opts.index(it.translation),
                    item_id=it.id, kind="en_to_ru",
                    meta={"word": it.word, "translation": it.translation},
                ))
                continue
            sentence = _replace_word(it.example, it.highlight or it.word)
            wrong = await db.random_other_words(it.id, 3)
            opts = (wrong + [it.word])[:4]
            random.shuffle(opts)
            qs.append(Question(
                prompt=sentence,
                options=opts,
                correct_idx=opts.index(it.word),
                item_id=it.id, kind=kind,
                meta={"word": it.word, "translation": it.translation},
            ))
    random.shuffle(qs)
    return qs


# ── verb quiz ─────────────────────────────────────────────────────────────

async def build_verb_quiz(items: list[VerbItem]) -> list[Question]:
    qs: list[Question] = []
    kinds: list[QuizKind] = ["past_simple", "past_participle", "verb_gap"]
    for i, it in enumerate(items):
        kind = kinds[i % len(kinds)]
        if kind == "past_simple":
            wrong = await db.random_other_past_simple(it.id, 3)
            opts = (wrong + [it.past_simple])[:4]
            random.shuffle(opts)
            qs.append(Question(
                prompt=f"{it.infinitive} → ?",
                options=opts,
                correct_idx=opts.index(it.past_simple),
                item_id=it.id, kind=kind,
                meta={"infinitive": it.infinitive,
                      "past_simple": it.past_simple,
                      "past_participle": it.past_participle},
            ))
        elif kind == "past_participle":
            wrong = await db.random_other_past_participle(it.id, 3)
            opts = (wrong + [it.past_participle])[:4]
            random.shuffle(opts)
            qs.append(Question(
                prompt=f"{it.infinitive} — {it.past_simple} — ?",
                options=opts,
                correct_idx=opts.index(it.past_participle),
                item_id=it.id, kind=kind,
                meta={"infinitive": it.infinitive,
                      "past_simple": it.past_simple,
                      "past_participle": it.past_participle},
            ))
        else:  # verb_gap
            answer = it.highlight or it.past_simple
            sentence = _replace_word(it.example, answer) if it.example else None
            if not sentence:
                # fallback
                wrong = await db.random_other_past_simple(it.id, 3)
                opts = (wrong + [it.past_simple])[:4]
                random.shuffle(opts)
                qs.append(Question(
                    prompt=f"{it.infinitive} → ?", options=opts,
                    correct_idx=opts.index(it.past_simple),
                    item_id=it.id, kind="past_simple",
                    meta={"infinitive": it.infinitive,
                          "past_simple": it.past_simple,
                          "past_participle": it.past_participle},
                ))
                continue
            others = {it.infinitive, it.past_simple,
                      it.past_participle} - {answer}
            wrong = list(others)[:3]
            while len(wrong) < 3:
                extra = await db.random_other_past_simple(it.id, 1)
                if extra and extra[0] not in wrong and extra[0] != answer:
                    wrong.append(extra[0])
                else:
                    break
            opts = (wrong + [answer])[:4]
            random.shuffle(opts)
            qs.append(Question(
                prompt=sentence,
                options=opts,
                correct_idx=opts.index(answer),
                item_id=it.id, kind=kind,
                meta={"infinitive": it.infinitive,
                      "past_simple": it.past_simple,
                      "past_participle": it.past_participle},
            ))
    random.shuffle(qs)
    return qs


# ── utils ─────────────────────────────────────────────────────────────────

def _replace_word(sentence: str, word: str) -> str:
    """Replace the highlighted word in a sentence with `_____`."""
    import re
    if not word:
        return sentence
    pat = re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
    return pat.sub("_____", sentence, count=1)
