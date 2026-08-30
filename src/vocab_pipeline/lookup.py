"""Dictionary lookup with a free-source fallback chain.

Tries Wiktionary's REST API first (broadest coverage, no key), falls back to
the Free Dictionary API (more consistently includes IPA/audio), and — only
when ``config.LLM_FALLBACK_ENABLED`` is true — finally asks an LLM to
generate a definition for words neither free source has. Each stage returns
``None`` on a clean "not found" so the next stage gets a chance; genuine
network/HTTP errors are left to propagate so the caller can record them.
"""

import html
import json
import re
from typing import Any, TypedDict
from urllib.parse import quote

import requests

from vocab_pipeline import config

LOOKUP_TIMEOUT_SECONDS = 10
LLM_TIMEOUT_SECONDS = 30
LLM_MODEL = "claude-haiku-4-5-20251001"
LLM_API_URL = "https://api.anthropic.com/v1/messages"
LLM_API_VERSION = "2023-06-01"

_TAG_RE = re.compile(r"<[^>]+>")


class WordDefinition(TypedDict):
    """A single looked-up word, ready to format into an Anki note.

    Attributes:
        word: The word or phrase that was looked up.
        pos: Part of speech (e.g. "noun"), or "" if unknown.
        definition: Plain-text definition (HTML tags stripped).
        ipa: IPA pronunciation, or None if unavailable.
        example: An example sentence, or None if unavailable.
        source: Which lookup stage produced this ("wiktionary",
            "free_dictionary", or "llm").
    """

    word: str
    pos: str
    definition: str
    ipa: str | None
    example: str | None
    source: str


def _strip_html(text: str) -> str:
    """Strip HTML tags and unescape entities from dictionary API text.

    Args:
        text: Raw text that may contain HTML markup.

    Returns:
        Plain text with tags removed and entities decoded, whitespace
        collapsed.
    """
    return " ".join(html.unescape(_TAG_RE.sub("", text)).split())


def _parse_wiktionary(word: str, payload: dict[str, Any]) -> WordDefinition | None:
    """Extract the first English sense from a Wiktionary REST response.

    Args:
        word: The word that was looked up.
        payload: Decoded JSON body from the Wiktionary definition endpoint.

    Returns:
        A :class:`WordDefinition`, or None if no English entry is present.
    """
    entries = payload.get("en")
    if not entries:
        return None
    for entry in entries:
        definitions = entry.get("definitions") or []
        for definition in definitions:
            text = definition.get("definition", "").strip()
            if not text:
                continue
            examples = definition.get("parsedExamples") or []
            example = _strip_html(examples[0]["example"]) if examples else None
            return WordDefinition(
                word=word,
                pos=entry.get("partOfSpeech", ""),
                definition=_strip_html(text),
                ipa=None,
                example=example,
                source="wiktionary",
            )
    return None


def lookup_wiktionary(word: str) -> WordDefinition | None:
    """Look up a word via the Wiktionary REST definition API.

    Args:
        word: The word or phrase to look up.

    Returns:
        A :class:`WordDefinition` if found, else None.

    Raises:
        requests.HTTPError: On a non-404 HTTP error response.
        requests.RequestException: On a network-level failure.
    """
    url = f"{config.DICTIONARY_PRIMARY_URL}{quote(word)}"
    response = requests.get(url, timeout=LOOKUP_TIMEOUT_SECONDS)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return _parse_wiktionary(word, response.json())


def _parse_free_dictionary(word: str, payload: list[dict[str, Any]]) -> WordDefinition | None:
    """Extract the first sense from a Free Dictionary API response.

    Args:
        word: The word that was looked up.
        payload: Decoded JSON body from the Free Dictionary API.

    Returns:
        A :class:`WordDefinition`, or None if the payload has no usable
        entry.
    """
    if not payload:
        return None
    entry = payload[0]
    ipa = entry.get("phonetic")
    if not ipa:
        for phonetic in entry.get("phonetics", []):
            if phonetic.get("text"):
                ipa = phonetic["text"]
                break
    for meaning in entry.get("meanings", []):
        for definition in meaning.get("definitions", []):
            text = definition.get("definition", "").strip()
            if not text:
                continue
            return WordDefinition(
                word=word,
                pos=meaning.get("partOfSpeech", ""),
                definition=_strip_html(text),
                ipa=ipa,
                example=definition.get("example") or None,
                source="free_dictionary",
            )
    return None


def lookup_free_dictionary(word: str) -> WordDefinition | None:
    """Look up a word via the Free Dictionary API.

    Args:
        word: The word or phrase to look up.

    Returns:
        A :class:`WordDefinition` if found, else None.

    Raises:
        requests.HTTPError: On a non-404 HTTP error response.
        requests.RequestException: On a network-level failure.
    """
    url = f"{config.DICTIONARY_FALLBACK_URL}{quote(word)}"
    response = requests.get(url, timeout=LOOKUP_TIMEOUT_SECONDS)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return _parse_free_dictionary(word, response.json())


def lookup_llm(word: str) -> WordDefinition | None:
    """Generate a definition with an LLM as a last-resort fallback.

    Only called when ``config.LLM_FALLBACK_ENABLED`` is true. Uses the
    Anthropic Messages API with ``config.LLM_API_KEY``, asking for a JSON
    object matching :class:`WordDefinition`'s shape.

    Args:
        word: The word or phrase to define.

    Returns:
        A :class:`WordDefinition`, or None if the model could not produce
        a usable definition (e.g. the word isn't real).

    Raises:
        requests.HTTPError: On a non-2xx HTTP error response.
        requests.RequestException: On a network-level failure.
    """
    prompt = (
        f'Define the English word or phrase "{word}" for a flashcard. '
        "Respond with ONLY a JSON object with keys "
        '"pos" (part of speech, or "" if not applicable), '
        '"definition" (one concise sentence), '
        '"ipa" (IPA pronunciation, or null if unsure), '
        '"example" (one short example sentence, or null). '
        'If this is not a real word or phrase, respond with {"not_found": true}.'
    )
    response = requests.post(
        LLM_API_URL,
        headers={
            "x-api-key": config.LLM_API_KEY,
            "anthropic-version": LLM_API_VERSION,
            "content-type": "application/json",
        },
        json={
            "model": LLM_MODEL,
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=LLM_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    content = payload.get("content", [])
    text = content[0]["text"].strip() if content else ""
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, IndexError, KeyError):
        return None
    if not isinstance(parsed, dict) or parsed.get("not_found") or not parsed.get("definition"):
        return None
    return WordDefinition(
        word=word,
        pos=str(parsed.get("pos") or ""),
        definition=str(parsed["definition"]),
        ipa=parsed.get("ipa") or None,
        example=parsed.get("example") or None,
        source="llm",
    )


def lookup_word(word: str) -> WordDefinition | None:
    """Look up a word, trying each configured source in order.

    Args:
        word: The word or phrase to define.

    Returns:
        The first :class:`WordDefinition` found, or None if no configured
        source has an entry for it.
    """
    result = lookup_wiktionary(word)
    if result is not None:
        return result
    result = lookup_free_dictionary(word)
    if result is not None:
        return result
    if config.LLM_FALLBACK_ENABLED:
        return lookup_llm(word)
    return None
