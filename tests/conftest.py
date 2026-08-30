"""Shared pytest fixtures for the vocab_pipeline test suite."""

import importlib
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

REQUIRED_ENV = {
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TELEGRAM_ALLOWED_CHAT_ID": "42",
    "ANKI_COLLECTION_PATH": "/tmp/does-not-matter.anki2",
    "DICTIONARY_PRIMARY_URL": "https://example.invalid/primary/",
    "DICTIONARY_FALLBACK_URL": "https://example.invalid/fallback/",
    "WAKE_TIMES": "08:00,20:00",
}


@pytest.fixture
def configured_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """Reload vocab_pipeline.config against an isolated, temp-dir environment.

    Args:
        tmp_path: Pytest-provided temporary directory.
        monkeypatch: Pytest's environment/attribute patching fixture.

    Yields:
        The freshly reloaded ``vocab_pipeline.config`` module.
    """
    prefixes = ("TELEGRAM_", "ANKI_", "DICTIONARY_", "LLM_", "WAKE_", "PROJECT_", "STATE_", "LOG_")
    for key in list(os.environ):
        if key.startswith(prefixes):
            monkeypatch.delenv(key, raising=False)
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PROJECT_DIR", str(tmp_path))

    from vocab_pipeline import config

    importlib.reload(config)
    yield config
