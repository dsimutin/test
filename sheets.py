import html
import json
import logging
import os
from datetime import date
from typing import List, Tuple

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
PROGRESS_SHEET_NAME = "Прогресс учеников"
PROGRESS_HEADERS = ["Дата", "Ученик", "Telegram ID", "Слово / Глагол", "Тип", "Правильных ответов"]

logger = logging.getLogger(__name__)


def _get_creds() -> Credentials:
    raw = (
        os.environ.get("GOOGLE_CREDENTIALS_JSON")
        or os.environ.get("GOOGLE_CREDENTIALS")
        or os.environ.get("GOOGLE_CREDENTIALS_FILE", "")
    )
    if not raw:
        raise ValueError("No Google credentials. Set GOOGLE_CREDENTIALS env var.")
    if raw.strip().startswith("{"):
        return Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    return Credentials.from_service_account_file(raw, scopes=SCOPES)


def _open_spreadsheet():
    client = gspread.authorize(_get_creds())
    sid = os.environ.get("SPREADSHEET_ID")
    return client.open_by_key(sid) if sid else client.open(os.environ.get("SHEET_NAME", "vocabulary"))


def _col(header: List[str], *names) -> int | None:
    for name in names:
        for i, h in enumerate(header):
            if h == name:
                return i
    return None


def _val(row: List[str], idx: int | None) -> str | None:
    if idx is None or idx >= len(row):
        return None
    v = row[idx].strip()
    return v or None


def _is_verb_sheet(ws, header: List[str], idx: int) -> bool:
    title = ws.title.lower()
    # By title
    if any(k in title for k in ("глагол", "verb", "irregular")):
        return True
    # By presence of past-simple column (many possible names)
    if _col(header,
            "past simple", "past_simple", "прошедшее", "v2",
            "форма 2", "2 форма", "форма2") is not None:
        return True
    # Fallback: non-first sheet that has no standard word column
    if idx >= 1 and _col(header, "word", "слово", "english") is None:
        return True
    return False


def get_sheets_info() -> str:
    """Return debug info about sheets and their headers."""
    try:
        ss = _open_spreadsheet()
        lines = [f"📋 <b>Таблица:</b> {html.escape(ss.title)}\n"]
        for i, ws in enumerate(ss.worksheets()):
            rows = ws.get_all_values()
            header = rows[0] if rows else []
            is_verb = _is_verb_sheet(ws, [h.strip().lower() for h in header], i)
            lines.append(
                f"<b>Лист {i+1}:</b> «{html.escape(ws.title)}» "
                f"({'глаголы' if is_verb else 'слова'})\n"
                f"Заголовки: <code>{html.escape(str(header[:6]))}</code>\n"
                f"Строк данных: {len(rows)-1}"
            )
        return "\n\n".join(lines)
    except Exception as e:
        return f"Ошибка: {html.escape(str(e))}"


def fetch_vocabulary() -> List[Tuple]:
    """
    Returns list of:
      (english, russian, transcription, example, card_type, past_simple, past_participle)
    """
    spreadsheet = _open_spreadsheet()
    result = []
    seen_words: set = set()
    seen_verbs: set = set()

    for idx, ws in enumerate(spreadsheet.worksheets()):
        if ws.title == PROGRESS_SHEET_NAME:
            continue
        rows = ws.get_all_values()
        if not rows:
            continue
        header = [h.strip().lower() for h in rows[0]]

        if _is_verb_sheet(ws, header, idx):
            inf_col = _col(header,
                "infinitive", "инфинитив", "глагол", "word", "слово", "v1", "english",
                "форма 1", "1 форма", "форма1", "base form", "начальная форма")
            rus_col = _col(header,
                "translation", "перевод", "russian", "значение")
            ps_col  = _col(header,
                "past simple", "past_simple", "прошедшее", "v2",
                "форма 2", "2 форма", "форма2")
            pp_col  = _col(header,
                "past participle", "past_participle", "причастие", "v3",
                "форма 3", "3 форма", "форма3")

            logger.info("Verb sheet «%s»: inf=%s rus=%s ps=%s pp=%s",
                        ws.title, inf_col, rus_col, ps_col, pp_col)

            if inf_col is None or rus_col is None:
                logger.warning("Verb sheet «%s» skipped — cant find required columns", ws.title)
                continue

            for row in rows[1:]:
                infinitive = _val(row, inf_col)
                russian    = _val(row, rus_col)
                if not infinitive or not russian or infinitive in seen_verbs:
                    continue
                seen_verbs.add(infinitive)
                result.append((
                    infinitive, russian, None, None, "verb",
                    _val(row, ps_col), _val(row, pp_col),
                ))
        else:
            eng_col = _col(header, "word", "слово", "english")
            rus_col = _col(header, "translation", "перевод", "russian", "значение")
            tr_col  = _col(header, "transcription", "транскрипция", "ipa")
            ex_col  = _col(header, "example", "пример")

            logger.info("Word sheet «%s»: eng=%s rus=%s tr=%s ex=%s",
                        ws.title, eng_col, rus_col, tr_col, ex_col)

            if eng_col is None or rus_col is None:
                logger.warning("Word sheet «%s» skipped — cant find required columns", ws.title)
                continue

            for row in rows[1:]:
                english = _val(row, eng_col)
                russian = _val(row, rus_col)
                if not english or not russian or english in seen_words:
                    continue
                seen_words.add(english)
                result.append((
                    english, russian,
                    _val(row, tr_col),
                    _val(row, ex_col),
                    "word", None, None,
                ))

    logger.info("fetch_vocabulary: %d words, %d verbs",
                sum(1 for r in result if r[4] == "word"),
                sum(1 for r in result if r[4] == "verb"))
    return result


def write_learned_word(user_name: str, user_id: int, word: str, card_type: str, correct: int):
    try:
        ss = _open_spreadsheet()
        titles = [ws.title for ws in ss.worksheets()]
        if PROGRESS_SHEET_NAME not in titles:
            ws = ss.add_worksheet(title=PROGRESS_SHEET_NAME, rows=1000, cols=len(PROGRESS_HEADERS))
            ws.append_row(PROGRESS_HEADERS)
        else:
            ws = ss.worksheet(PROGRESS_SHEET_NAME)
        ws.append_row([
            date.today().strftime("%d.%m.%Y"),
            user_name, str(user_id), word,
            "Слово" if card_type == "word" else "Глагол",
            str(correct),
        ])
    except Exception:
        logger.warning("Failed to write progress to sheets", exc_info=True)
