"""Tests for vocab_pipeline.lookup."""

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from vocab_pipeline import lookup


def _response(status_code: int, json_body: Any) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body
    if status_code >= 400 and status_code != 404:
        response.raise_for_status.side_effect = requests.HTTPError(f"{status_code}")
    else:
        response.raise_for_status.return_value = None
    return response


WIKTIONARY_OK = {
    "en": [
        {
            "partOfSpeech": "noun",
            "definitions": [
                {
                    "definition": "The occurrence of events by chance in a "
                    "happy or beneficial way.",
                    "parsedExamples": [{"example": "A <b>serendipity</b> of sorts."}],
                }
            ],
        }
    ]
}

FREE_DICT_OK = [
    {
        "word": "quokka",
        "phonetic": "/ˈkwɒkə/",
        "meanings": [
            {
                "partOfSpeech": "noun",
                "definitions": [
                    {
                        "definition": "A small wallaby found in Australia.",
                        "example": "We saw a quokka on Rottnest Island.",
                    }
                ],
            }
        ],
    }
]


def test_lookup_wiktionary_success() -> None:
    with patch(
        "vocab_pipeline.lookup.requests.get",
        return_value=_response(200, WIKTIONARY_OK),
    ):
        result = lookup.lookup_wiktionary("serendipity")
    assert result is not None
    assert result["word"] == "serendipity"
    assert result["pos"] == "noun"
    assert result["source"] == "wiktionary"
    assert "chance" in result["definition"]
    assert result["example"] == "A serendipity of sorts."


def test_lookup_wiktionary_not_found_returns_none() -> None:
    with patch(
        "vocab_pipeline.lookup.requests.get",
        return_value=_response(404, {"title": "Not Found"}),
    ):
        assert lookup.lookup_wiktionary("asdfghjkl") is None


def test_lookup_wiktionary_server_error_raises() -> None:
    with patch(
        "vocab_pipeline.lookup.requests.get",
        return_value=_response(500, {}),
    ):
        with pytest.raises(requests.HTTPError):
            lookup.lookup_wiktionary("word")


def test_lookup_free_dictionary_success() -> None:
    with patch(
        "vocab_pipeline.lookup.requests.get",
        return_value=_response(200, FREE_DICT_OK),
    ):
        result = lookup.lookup_free_dictionary("quokka")
    assert result is not None
    assert result["ipa"] == "/ˈkwɒkə/"
    assert result["source"] == "free_dictionary"
    assert result["example"] == "We saw a quokka on Rottnest Island."


def test_lookup_word_falls_back_to_free_dictionary() -> None:
    """A word Wiktionary doesn't have but the fallback does resolves via it."""
    not_found = _response(404, {"title": "Not Found"})
    found = _response(200, FREE_DICT_OK)
    with patch(
        "vocab_pipeline.lookup.requests.get",
        side_effect=[not_found, found],
    ) as mock_get:
        result = lookup.lookup_word("quokka")
    assert result is not None
    assert result["source"] == "free_dictionary"
    assert mock_get.call_count == 2


def test_lookup_word_returns_none_when_llm_disabled(configured_env: Any) -> None:
    not_found = _response(404, {"title": "Not Found"})
    with patch("vocab_pipeline.lookup.requests.get", return_value=not_found):
        assert lookup.lookup_word("zzznotaword") is None


def test_lookup_llm_parses_json_response() -> None:
    llm_response = MagicMock()
    llm_response.raise_for_status.return_value = None
    llm_response.json.return_value = {
        "content": [
            {
                "text": json.dumps(
                    {
                        "pos": "adjective",
                        "definition": "Extremely tired.",
                        "ipa": None,
                        "example": "I was knackered after the hike.",
                    }
                )
            }
        ]
    }
    with patch("vocab_pipeline.lookup.requests.post", return_value=llm_response):
        result = lookup.lookup_llm("knackered")
    assert result is not None
    assert result["source"] == "llm"
    assert result["definition"] == "Extremely tired."


def test_lookup_llm_not_found_returns_none() -> None:
    llm_response = MagicMock()
    llm_response.raise_for_status.return_value = None
    llm_response.json.return_value = {"content": [{"text": json.dumps({"not_found": True})}]}
    with patch("vocab_pipeline.lookup.requests.post", return_value=llm_response):
        assert lookup.lookup_llm("asdkjhaskjdh") is None
