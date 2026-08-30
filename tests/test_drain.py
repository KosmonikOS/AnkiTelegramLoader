"""Tests for vocab_pipeline.drain."""

import json
from typing import Any
from unittest.mock import MagicMock, patch

from vocab_pipeline import drain


def _make_update(update_id: int, chat_id: int, text: str) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": chat_id}, "text": text},
    }


def test_extract_word_entries_filters_by_chat_id() -> None:
    updates = [
        _make_update(1, 42, "serendipity"),
        _make_update(2, 999, "intruder"),
        _make_update(3, 42, "  ubiquitous  "),
    ]
    entries = drain.extract_word_entries(updates, allowed_chat_id="42")
    assert [e.word for e in entries] == ["serendipity", "ubiquitous"]
    assert [e.update_id for e in entries] == [1, 3]


def test_extract_word_entries_skips_non_message_and_empty_text() -> None:
    updates: list[dict[str, Any]] = [
        {"update_id": 1, "edited_message": {"chat": {"id": 42}, "text": "x"}},
        _make_update(2, 42, "   "),
    ]
    assert drain.extract_word_entries(updates, allowed_chat_id="42") == []


def test_offset_round_trip(configured_env: Any) -> None:
    config = configured_env
    assert drain.read_offset(config.TELEGRAM_OFFSET_PATH) == 0
    drain.write_offset(config.TELEGRAM_OFFSET_PATH, 17)
    assert drain.read_offset(config.TELEGRAM_OFFSET_PATH) == 17


def test_append_and_load_known_update_ids(configured_env: Any) -> None:
    config = configured_env
    entries = [
        drain.PendingWord(update_id=1, word="foo", received_at="2026-01-01T00:00:00+00:00"),
        drain.PendingWord(update_id=2, word="bar", received_at="2026-01-01T00:00:01+00:00"),
    ]
    drain.append_pending_words(config.PENDING_WORDS_PATH, entries)

    known = drain.load_known_update_ids(config.PENDING_WORDS_PATH)
    assert known == {1, 2}

    lines = config.PENDING_WORDS_PATH.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["word"] == "foo"
    assert json.loads(lines[0])["status"] == "pending"


def test_drain_advances_offset_and_dedupes(configured_env: Any) -> None:
    config = configured_env
    response = MagicMock()
    response.json.return_value = {
        "result": [
            _make_update(10, 42, "aardvark"),
            _make_update(11, 999, "not-my-chat"),
        ]
    }
    response.raise_for_status.return_value = None

    with patch("vocab_pipeline.drain.requests.get", return_value=response) as mock_get:
        new_entries = drain.drain()

    assert [e.word for e in new_entries] == ["aardvark"]
    mock_get.assert_called_once()
    assert drain.read_offset(config.TELEGRAM_OFFSET_PATH) == 12

    # Re-running with the same updates (simulating a crash before the
    # offset advanced further) must not duplicate the already-stored word.
    with patch("vocab_pipeline.drain.requests.get", return_value=response):
        second_run = drain.drain()
    assert second_run == []
    known = drain.load_known_update_ids(config.PENDING_WORDS_PATH)
    assert known == {10}


def test_drain_no_updates_returns_empty(configured_env: Any) -> None:
    response = MagicMock()
    response.json.return_value = {"result": []}
    response.raise_for_status.return_value = None
    with patch("vocab_pipeline.drain.requests.get", return_value=response):
        assert drain.drain() == []
