"""Phase 1: drain queued Telegram messages into the local durable store.

Calls Telegram's ``getUpdates`` once, discards anything not sent from
``config.TELEGRAM_ALLOWED_CHAT_ID``, and appends each remaining message as a
new line in ``pending_words.jsonl``. The Telegram offset is only advanced
after that local write succeeds, so a crash between the two never loses a
word: at worst the same update is re-fetched and re-appended, which
:func:`load_known_update_ids` makes idempotent.
"""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import requests

from vocab_pipeline import config

TELEGRAM_API_BASE = "https://api.telegram.org"
GETUPDATES_TIMEOUT_SECONDS = 10
"""Seconds Telegram may hold the ``getUpdates`` request open long-polling."""

WordStatus = Literal["pending", "done", "failed"]


@dataclass
class PendingWord:
    """One word received from Telegram, tracked through the pipeline.

    Attributes:
        update_id: Telegram's unique id for the source update.
        word: The raw message text as sent by the user.
        received_at: ISO 8601 UTC timestamp of when this was drained.
        status: Processing state — pending, done, or failed.
        error: Error detail when status is "failed", else None.
    """

    update_id: int
    word: str
    received_at: str
    status: WordStatus = "pending"
    error: str | None = None

    def to_json_line(self) -> str:
        """Serialize this entry as a single JSON line.

        Returns:
            A JSON-encoded string with no trailing newline.
        """
        return json.dumps(asdict(self), ensure_ascii=False)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "PendingWord":
        """Reconstruct a :class:`PendingWord` from a decoded JSON object.

        Args:
            data: A dict previously produced by :meth:`to_json_line`.

        Returns:
            The reconstructed entry.
        """
        return PendingWord(
            update_id=data["update_id"],
            word=data["word"],
            received_at=data["received_at"],
            status=data.get("status", "pending"),
            error=data.get("error"),
        )


def fetch_updates(offset: int) -> list[dict[str, Any]]:
    """Fetch pending updates from the Telegram Bot API.

    Args:
        offset: The lowest update_id to return. Pass the last confirmed
            update_id plus one, so Telegram considers earlier updates safe
            to drop from its queue.

    Returns:
        The list of raw update objects Telegram returned (possibly empty).

    Raises:
        requests.HTTPError: If the Telegram API request fails.
    """
    url = f"{TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"offset": offset, "timeout": GETUPDATES_TIMEOUT_SECONDS}
    response = requests.get(url, params=params, timeout=GETUPDATES_TIMEOUT_SECONDS + 10)
    response.raise_for_status()
    payload = response.json()
    result: list[dict[str, Any]] = payload.get("result", [])
    return result


def load_known_update_ids(pending_words_path: Path) -> set[int]:
    """Return every update_id already recorded in the local store.

    Args:
        pending_words_path: Path to ``pending_words.jsonl``.

    Returns:
        The set of update_ids already present, empty if the file doesn't
        exist yet.
    """
    if not pending_words_path.exists():
        return set()
    known: set[int] = set()
    with pending_words_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            known.add(json.loads(line)["update_id"])
    return known


def read_offset(offset_path: Path) -> int:
    """Read the last confirmed Telegram offset.

    Args:
        offset_path: Path to ``telegram_offset.txt``.

    Returns:
        The stored offset, or 0 if the file doesn't exist yet.
    """
    if not offset_path.exists():
        return 0
    text = offset_path.read_text(encoding="utf-8").strip()
    return int(text) if text else 0


def write_offset(offset_path: Path, offset: int) -> None:
    """Persist the last confirmed Telegram offset.

    Args:
        offset_path: Path to ``telegram_offset.txt``.
        offset: The offset value to store.
    """
    offset_path.write_text(str(offset), encoding="utf-8")


def extract_word_entries(updates: list[dict[str, Any]], allowed_chat_id: str) -> list[PendingWord]:
    """Filter updates to allowed-chat text messages and convert them.

    Args:
        updates: Raw update objects from :func:`fetch_updates`.
        allowed_chat_id: The only ``chat.id`` (as a string) to accept
            messages from; everything else is silently discarded.

    Returns:
        One :class:`PendingWord` per accepted, non-empty text message.
    """
    entries: list[PendingWord] = []
    for update in updates:
        message = update.get("message")
        if not message:
            continue
        chat_id = str(message.get("chat", {}).get("id", ""))
        if chat_id != allowed_chat_id:
            continue
        text = message.get("text", "").strip()
        if not text:
            continue
        entries.append(
            PendingWord(
                update_id=update["update_id"],
                word=text,
                received_at=datetime.now(UTC).isoformat(),
            )
        )
    return entries


def append_pending_words(pending_words_path: Path, entries: list[PendingWord]) -> None:
    """Append new entries to the local durable store.

    Args:
        pending_words_path: Path to ``pending_words.jsonl``.
        entries: Entries to append, in order.
    """
    with pending_words_path.open("a", encoding="utf-8") as f:
        for entry in entries:
            f.write(entry.to_json_line() + "\n")


def drain() -> list[PendingWord]:
    """Run Phase 1 once: fetch, dedupe, persist, then advance the offset.

    Returns:
        The newly appended entries (empty if nothing new was queued).
    """
    offset = read_offset(config.TELEGRAM_OFFSET_PATH)
    updates = fetch_updates(offset)
    if not updates:
        return []

    known_ids = load_known_update_ids(config.PENDING_WORDS_PATH)
    candidates = extract_word_entries(updates, config.TELEGRAM_ALLOWED_CHAT_ID)
    new_entries = [e for e in candidates if e.update_id not in known_ids]

    if new_entries:
        append_pending_words(config.PENDING_WORDS_PATH, new_entries)

    max_update_id = max(u["update_id"] for u in updates)
    write_offset(config.TELEGRAM_OFFSET_PATH, max_update_id + 1)

    return new_entries


def main() -> None:
    """CLI entry point for the ``vocab-drain`` command."""
    entries = drain()
    if not entries:
        print("No new words queued.")
        return
    print(f"Drained {len(entries)} new word(s):")
    for entry in entries:
        print(f"  - {entry.word}")


if __name__ == "__main__":
    main()
