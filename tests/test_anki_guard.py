"""Tests for vocab_pipeline.anki_guard."""

from unittest.mock import MagicMock, call, patch

from vocab_pipeline import anki_guard


def _run_result(returncode: int) -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    return result


def test_ensure_anki_closed_noop_when_not_running() -> None:
    with patch("vocab_pipeline.anki_guard.subprocess.run", return_value=_run_result(1)) as run:
        anki_guard.ensure_anki_closed(timeout=5)
    # Only the pgrep check should have happened — no quit, no kill.
    run.assert_called_once()
    assert run.call_args.args[0][0] == "pgrep"


def test_ensure_anki_closed_quits_gracefully() -> None:
    # pgrep: running, then not running after the polite quit.
    with patch(
        "vocab_pipeline.anki_guard.subprocess.run",
        side_effect=[_run_result(0), _run_result(0), _run_result(1)],
    ) as run:
        anki_guard.ensure_anki_closed(timeout=5)

    commands = [c.args[0][0] for c in run.call_args_list]
    assert commands == ["pgrep", "osascript", "pgrep"]


def test_ensure_anki_closed_force_kills_after_timeout() -> None:
    # pgrep always reports running until pkill is issued.
    responses = [_run_result(0)] * 5  # initial + osascript + 3 pgrep polls
    with (
        patch(
            "vocab_pipeline.anki_guard.subprocess.run",
            side_effect=[*responses, _run_result(0)],
        ) as run,
        patch("vocab_pipeline.anki_guard.time.sleep") as mock_sleep,
    ):
        anki_guard.ensure_anki_closed(timeout=2)

    commands = [c.args[0][0] for c in run.call_args_list]
    assert commands[0] == "pgrep"
    assert commands[1] == "osascript"
    assert "pkill" in commands
    assert mock_sleep.call_args_list == [call(1), call(1)]
