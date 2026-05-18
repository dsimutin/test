"""Centralised config — read once from env."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    bot_token: str
    spreadsheet_id: str
    db_path: Path
    card_cache_dir: Path
    google_credentials_file: str | None
    google_credentials_json: str | None

    # Pedagogy
    vocab_batch_size: int = 10
    verb_batch_size: int = 5
    quiz_questions_vocab: int = 10
    quiz_questions_verbs: int = 5


def load_config() -> Config:
    db_path = Path(os.getenv("DB_PATH", str(ROOT / "data" / "english_bot.db")))
    cache = Path(os.getenv("CARD_CACHE_DIR", str(ROOT / "data" / "cache")))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    return Config(
        bot_token=os.environ["BOT_TOKEN"],
        spreadsheet_id=os.environ["SPREADSHEET_ID"],
        db_path=db_path,
        card_cache_dir=cache,
        google_credentials_file=os.getenv("GOOGLE_CREDENTIALS_FILE"),
        google_credentials_json=os.getenv("GOOGLE_CREDENTIALS_JSON"),
    )
