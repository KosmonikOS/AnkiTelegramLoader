"""Tests for vocab_pipeline.process."""

from typing import Any
from unittest.mock import MagicMock, patch

from vocab_pipeline import process
from vocab_pipeline.drain import PendingWord
from vocab_pipeline.lookup import WordDefinition


def _definition(**overrides: Any) -> WordDefinition:
    base: WordDefinition = {
        "word": "serendipity",
        "pos": "noun",
        "definition": "A happy accident.",
        "ipa": "ˌsɛrənˈdɪpɪti",
        "example": "Meeting you was pure serendipity.",
        "source": "wiktionary",
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def test_format_definition_html_includes_all_parts() -> None:
    rendered = process.format_definition_html(_definition())
    assert "<i>noun</i>" in rendered
    assert "A happy accident." in rendered
    assert "/ˌsɛrənˈdɪpɪti/" in rendered
    assert "<i>Meeting you was pure serendipity.</i>" in rendered


def test_format_definition_html_omits_missing_fields() -> None:
    rendered = process.format_definition_html(_definition(ipa=None, example=None, pos=""))
    assert "noun" not in rendered
    assert rendered == "A happy accident."


def test_format_definition_html_escapes_html() -> None:
    rendered = process.format_definition_html(_definition(definition="<script>alert(1)</script>"))
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_pending_words_round_trip(configured_env: Any) -> None:
    config = configured_env
    entries = [
        PendingWord(update_id=1, word="foo", received_at="2026-01-01T00:00:00+00:00"),
        PendingWord(
            update_id=2,
            word="bar",
            received_at="2026-01-01T00:00:01+00:00",
            status="done",
        ),
    ]
    process.write_all_pending(config.PENDING_WORDS_PATH, entries)

    loaded = process.read_all_pending(config.PENDING_WORDS_PATH)
    assert [(e.update_id, e.status) for e in loaded] == [(1, "pending"), (2, "done")]
    assert not config.PENDING_WORDS_PATH.with_suffix(".jsonl.tmp").exists()


def test_read_all_pending_missing_file_returns_empty(configured_env: Any) -> None:
    config = configured_env
    assert process.read_all_pending(config.PENDING_WORDS_PATH) == []


def test_process_pending_no_words_skips_anki(configured_env: Any) -> None:
    with patch("vocab_pipeline.process._open_collection") as mock_open:
        summary = process.process_pending()
    mock_open.assert_not_called()
    assert summary.processed == 0


def test_process_pending_writes_notes_and_marks_status(configured_env: Any) -> None:
    config = configured_env
    entries = [
        PendingWord(update_id=1, word="found", received_at="2026-01-01T00:00:00+00:00"),
        PendingWord(update_id=2, word="missing", received_at="2026-01-01T00:00:01+00:00"),
    ]
    process.write_all_pending(config.PENDING_WORDS_PATH, entries)

    fake_collection = MagicMock()
    fake_collection.decks.id.return_value = 1
    fake_collection.models.by_name.return_value = {"id": 1, "name": "Basic"}
    fake_note: dict[str, str] = {}
    fake_collection.new_note.return_value = fake_note

    def fake_lookup(word: str) -> WordDefinition | None:
        if word == "found":
            return _definition(word="found")
        return None

    with (
        patch("vocab_pipeline.process._open_collection", return_value=fake_collection),
        patch("vocab_pipeline.lookup.lookup_word", side_effect=fake_lookup),
    ):
        summary = process.process_pending()

    assert summary == process.ProcessSummary(processed=2, succeeded=1, failed=1)
    fake_collection.add_note.assert_called_once()
    fake_collection.close.assert_called_once()

    updated = process.read_all_pending(config.PENDING_WORDS_PATH)
    by_id = {e.update_id: e for e in updated}
    assert by_id[1].status == "done"
    assert by_id[1].error is None
    assert by_id[2].status == "failed"
    assert by_id[2].error == "No definition found in any configured source."


def test_process_pending_closes_collection_on_error(configured_env: Any) -> None:
    config = configured_env
    entries = [PendingWord(update_id=1, word="x", received_at="2026-01-01T00:00:00+00:00")]
    process.write_all_pending(config.PENDING_WORDS_PATH, entries)

    fake_collection = MagicMock()
    fake_collection.decks.id.return_value = None

    with patch("vocab_pipeline.process._open_collection", return_value=fake_collection):
        try:
            process.process_pending()
            raised = False
        except process.ProcessError:
            raised = True
    assert raised
    fake_collection.close.assert_called_once()
