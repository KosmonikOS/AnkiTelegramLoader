"""Configuration loading for the vocabulary pipeline.

Every machine-specific path, credential, and tunable value is read from a
single ``.env`` file at the project root (see ``.env.example`` for the full
list of keys). Every other module imports values from here rather than
reading ``os.environ`` directly, so this is the one place that knows how
configuration is sourced.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    """Raised when a required configuration value is missing or invalid."""


def _require(key: str) -> str:
    """Return a required environment variable's value.

    Args:
        key: Name of the environment variable.

    Returns:
        The variable's value, stripped of surrounding whitespace.

    Raises:
        ConfigError: If the variable is unset or blank.
    """
    value = os.environ.get(key, "").strip()
    if not value:
        raise ConfigError(
            f"Missing required setting '{key}'. Copy .env.example to .env "
            "and fill in a value, or export it in the environment."
        )
    return value


def _optional(key: str, default: str) -> str:
    """Return an optional environment variable's value, or a default.

    Args:
        key: Name of the environment variable.
        default: Value to use when the variable is unset or blank.

    Returns:
        The variable's stripped value, or ``default``.
    """
    value = os.environ.get(key, "").strip()
    return value or default


TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")
TELEGRAM_ALLOWED_CHAT_ID: str = _require("TELEGRAM_ALLOWED_CHAT_ID")

ANKI_COLLECTION_PATH: Path = Path(_require("ANKI_COLLECTION_PATH")).expanduser()
ANKI_DECK_NAME: str = _optional("ANKI_DECK_NAME", "Vocabulary")
ANKI_NOTE_TYPE: str = _optional("ANKI_NOTE_TYPE", "Basic")
ANKI_CLOSE_TIMEOUT_SECONDS: int = int(_optional("ANKI_CLOSE_TIMEOUT_SECONDS", "15"))
ANKI_SYNC_WAIT_SECONDS: int = int(_optional("ANKI_SYNC_WAIT_SECONDS", "45"))

DICTIONARY_PRIMARY_URL: str = _require("DICTIONARY_PRIMARY_URL")
DICTIONARY_FALLBACK_URL: str = _require("DICTIONARY_FALLBACK_URL")
LLM_FALLBACK_ENABLED: bool = _optional("LLM_FALLBACK_ENABLED", "false").lower() == "true"
LLM_API_KEY: str = os.environ.get("LLM_API_KEY", "").strip()

if LLM_FALLBACK_ENABLED and not LLM_API_KEY:
    raise ConfigError("LLM_FALLBACK_ENABLED is true but LLM_API_KEY is not set.")

WAKE_TIMES: list[str] = [t.strip() for t in _require("WAKE_TIMES").split(",") if t.strip()]

PROJECT_DIR: Path = Path(_require("PROJECT_DIR")).expanduser()
STATE_DIR: Path = Path(_optional("STATE_DIR", str(PROJECT_DIR / "state"))).expanduser()
LOG_DIR: Path = Path(_optional("LOG_DIR", str(PROJECT_DIR / "logs"))).expanduser()

STATE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

PENDING_WORDS_PATH: Path = STATE_DIR / "pending_words.jsonl"
TELEGRAM_OFFSET_PATH: Path = STATE_DIR / "telegram_offset.txt"
RUN_LOG_PATH: Path = LOG_DIR / "run_log.txt"
