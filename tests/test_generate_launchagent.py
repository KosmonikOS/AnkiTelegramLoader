"""Tests for vocab_pipeline.generate_launchagent."""

import plistlib
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


def test_update_pmset_wake_schedule_picks_earliest() -> None:
    with patch("vocab_pipeline.generate_launchagent.subprocess.run") as mock_run:
        chosen = gla.update_pmset_wake_schedule(["20:00", "08:00", "14:30"])
    assert chosen == "08:00"
    mock_run.assert_called_once_with(
        ["sudo", "pmset", "repeat", "wake", gla.PMSET_DAYS, "08:00:00"],
        check=True,
    )


def test_update_pmset_wake_schedule_rejects_empty() -> None:
    with pytest.raises(ConfigError):
        gla.update_pmset_wake_schedule([])


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
