"""Phase 2: look up definitions and write new Anki notes.

Reads every ``pending`` entry from ``pending_words.jsonl``, looks each word
up via :mod:`vocab_pipeline.lookup`, and writes a new note directly into the
Anki collection via the ``anki`` pylib. Anki.app is guaranteed closed first
(see :mod:`vocab_pipeline.anki_guard`) so the pylib and the GUI never touch
the collection concurrently. Each word's outcome (done/failed, with error
detail on failure) is persisted back to the local store so a crash mid-run
loses nothing — unprocessed words are simply retried on the next call.
"""

import html
import json
from dataclasses import dataclass
from pathlib import Path

from anki.collection import Collection

from vocab_pipeline import anki_guard, config, lookup
from vocab_pipeline.drain import PendingWord


class ProcessError(RuntimeError):
    """Raised when the Anki collection can't be prepared for writing."""


@dataclass
class ProcessSummary:
    """Outcome counts for one :func:`process_pending` run.

    Attributes:
        processed: Number of pending words attempted.
        succeeded: Number written to Anki successfully.
        failed: Number that raised an error (lookup or write).
    """

    processed: int
    succeeded: int
    failed: int


def format_definition_html(entry: lookup.WordDefinition) -> str:
    """Render a looked-up definition as the HTML for a note's Back field.

    Args:
        entry: The definition to format.

    Returns:
        An HTML fragment combining part of speech, definition, IPA, and
        example, suitable for an Anki field (which renders HTML).
    """
    parts: list[str] = []
    if entry["pos"]:
        parts.append(f"<i>{html.escape(entry['pos'])}</i>")
    parts.append(html.escape(entry["definition"]))
    if entry["ipa"]:
        parts.append(f"<div>/{html.escape(entry['ipa'])}/</div>")
    if entry["example"]:
        parts.append(f"<div><i>{html.escape(entry['example'])}</i></div>")
    return "<br>".join(parts)


def read_all_pending(pending_words_path: Path) -> list[PendingWord]:
    """Read every entry ever recorded in the local store.

    Args:
        pending_words_path: Path to ``pending_words.jsonl``.

    Returns:
        All entries in file order, regardless of status. Empty if the file
        doesn't exist yet.
    """
    if not pending_words_path.exists():
        return []
    entries: list[PendingWord] = []
    with pending_words_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entries.append(PendingWord.from_dict(json.loads(line)))
    return entries


def write_all_pending(pending_words_path: Path, entries: list[PendingWord]) -> None:
    """Atomically overwrite the local store with the given entries.

    Writes to a temporary file in the same directory and renames it into
    place, so a crash mid-write never leaves ``pending_words.jsonl``
    truncated or half-updated.

    Args:
        pending_words_path: Path to ``pending_words.jsonl``.
        entries: The complete set of entries to persist, in order.
    """
    tmp_path = pending_words_path.with_suffix(".jsonl.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(entry.to_json_line() + "\n")
    tmp_path.replace(pending_words_path)


def _open_collection() -> Collection:
    """Open the configured Anki collection, guarding against a running GUI.

    Returns:
        An open :class:`anki.collection.Collection`.
    """
    anki_guard.ensure_anki_closed()
    return Collection(str(config.ANKI_COLLECTION_PATH))


def process_pending() -> ProcessSummary:
    """Run Phase 2 once: define and write every pending word to Anki.

    Returns:
        A summary of how many words were attempted, written, and failed.
    """
    entries = read_all_pending(config.PENDING_WORDS_PATH)
    pending = [e for e in entries if e.status == "pending"]
    if not pending:
        return ProcessSummary(processed=0, succeeded=0, failed=0)

    collection = _open_collection()
    try:
        deck_id = collection.decks.id(config.ANKI_DECK_NAME)
        if deck_id is None:
            raise ProcessError(f"Could not create or find deck '{config.ANKI_DECK_NAME}'.")
        notetype = collection.models.by_name(config.ANKI_NOTE_TYPE)
        if notetype is None:
            raise ProcessError(
                f"Note type '{config.ANKI_NOTE_TYPE}' not found in the collection "
                f"at {config.ANKI_COLLECTION_PATH}."
            )

        succeeded = 0
        failed = 0
        for entry in pending:
            try:
                definition = lookup.lookup_word(entry.word)
                if definition is None:
                    raise ProcessError("No definition found in any configured source.")
                note = collection.new_note(notetype)
                note["Front"] = definition["word"]
                note["Back"] = format_definition_html(definition)
                collection.add_note(note, deck_id)
            except Exception as exc:
                entry.status = "failed"
                entry.error = str(exc)
                failed += 1
            else:
                entry.status = "done"
                entry.error = None
                succeeded += 1
    finally:
        collection.close()

    write_all_pending(config.PENDING_WORDS_PATH, entries)
    return ProcessSummary(processed=len(pending), succeeded=succeeded, failed=failed)


def main() -> None:
    """CLI entry point for the ``vocab-process`` command."""
    summary = process_pending()
    if summary.processed == 0:
        print("No pending words to process.")
        return
    print(
        f"Processed {summary.processed} word(s): "
        f"{summary.succeeded} succeeded, {summary.failed} failed."
    )


if __name__ == "__main__":
    main()
