"""Tests for vocab_pipeline.generate_launchagent."""

import plistlib
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from vocab_pipeline import generate_launchagent as gla
from vocab_pipeline.config import ConfigError


def test_parse_wake_time_valid() -> None:
    assert gla._parse_wake_time("08:05") == (8, 5)


def test_parse_wake_time_rejects_bad_format() -> None:
    with pytest.raises(ConfigError):
        gla._parse_wake_time("8am")
    with pytest.raises(ConfigError):
        gla._parse_wake_time("25:00")


def test_next_occurrence_later_today() -> None:
    now = datetime(2026, 8, 30, 6, 0, 0)
    assert gla._next_occurrence(8, 0, now) == datetime(2026, 8, 30, 8, 0, 0)


def test_next_occurrence_rolls_to_tomorrow_when_passed() -> None:
    now = datetime(2026, 8, 30, 9, 0, 0)
    assert gla._next_occurrence(8, 0, now) == datetime(2026, 8, 31, 8, 0, 0)


def test_next_occurrence_rolls_to_tomorrow_within_buffer() -> None:
    # Re-arming from inside the run a time itself triggered: "now" is only
    # seconds after that time, which must still count as already passed.
    now = datetime(2026, 8, 30, 8, 0, 5)
    assert gla._next_occurrence(8, 0, now) == datetime(2026, 8, 31, 8, 0, 0)


def test_schedule_wake_events_arms_every_time_in_one_command() -> None:
    now = datetime(2026, 8, 30, 6, 0, 0)
    with patch("vocab_pipeline.generate_launchagent.subprocess.run") as mock_run:
        occurrences = gla.schedule_wake_events(["20:00", "08:00"], now=now)

    assert occurrences == [
        datetime(2026, 8, 30, 20, 0, 0),
        datetime(2026, 8, 30, 8, 0, 0),
    ]
    mock_run.assert_called_once_with(
        [
            "sudo",
            "pmset",
            "schedule",
            "wake",
            "08/30/26 20:00:00",
            "wake",
            "08/30/26 08:00:00",
        ],
        check=True,
    )


def test_schedule_wake_events_rejects_empty() -> None:
    with pytest.raises(ConfigError):
        gla.schedule_wake_events([])


def test_find_vocab_batch_executable_missing_raises() -> None:
    with patch("vocab_pipeline.generate_launchagent.shutil.which", return_value=None):
        with pytest.raises(ConfigError):
            gla.find_vocab_batch_executable()


def test_build_launch_agent_plist_structure(configured_env: Any) -> None:
    plist = gla.build_launch_agent_plist("/usr/local/bin/vocab-batch", ["08:00", "20:15"])
    assert plist["Label"] == gla.LAUNCH_AGENT_LABEL
    assert plist["ProgramArguments"] == ["/usr/local/bin/vocab-batch"]
    assert plist["StartCalendarInterval"] == [
        {"Hour": 8, "Minute": 0},
        {"Hour": 20, "Minute": 15},
    ]


def test_write_launch_agent_plist_roundtrip(tmp_path: Path) -> None:
    plist = {"Label": "x", "ProgramArguments": ["/bin/true"]}
    dest = tmp_path / "sub" / "test.plist"
    gla.write_launch_agent_plist(plist, dest)
    assert dest.exists()
    with dest.open("rb") as f:
        loaded = plistlib.load(f)
    assert loaded == plist


def test_generate_launchagent_end_to_end(configured_env: Any, tmp_path: Path) -> None:
    fake_plist_dir = tmp_path / "LaunchAgents"
    with (
        patch(
            "vocab_pipeline.generate_launchagent.find_vocab_batch_executable",
            return_value="/usr/local/bin/vocab-batch",
        ),
        patch("vocab_pipeline.generate_launchagent.subprocess.run") as mock_run,
        patch("vocab_pipeline.generate_launchagent.LAUNCH_AGENTS_DIR", fake_plist_dir),
    ):
        plist_path = gla.generate_launchagent()

    assert plist_path == fake_plist_dir / f"{gla.LAUNCH_AGENT_LABEL}.plist"
    assert plist_path.exists()
    commands = [c.args[0][0] for c in mock_run.call_args_list]
    assert commands == ["sudo", "launchctl", "launchctl"]
