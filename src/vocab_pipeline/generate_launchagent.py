"""Set up unattended scheduling: a pmset wake schedule and a LaunchAgent.

Reads ``config.WAKE_TIMES`` and, in one step, registers a macOS wake-from-
sleep schedule and a LaunchAgent that runs ``vocab-batch`` at each
configured time. Re-run this after editing ``WAKE_TIMES`` in ``.env`` —
never hand-edit the generated plist or `pmset` state directly.

Important caveat: macOS's ``pmset repeat`` mechanism supports only a single
recurring wake event, not one per ``WAKE_TIMES`` entry (see ``man pmset``).
Only the earliest configured time is registered with `pmset`, so only that
time reliably wakes the Mac from sleep. Every configured time still gets a
LaunchAgent trigger, but a time other than the earliest only fires if the
Mac happens to already be awake at that moment.
"""

import plistlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

from vocab_pipeline import config
from vocab_pipeline.config import ConfigError

PMSET_DAYS = "MTWRFSU"
LAUNCH_AGENT_LABEL = "com.user.vocabpipeline"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


def _parse_wake_time(value: str) -> tuple[int, int]:
    """Parse an ``HH:MM`` string into an (hour, minute) pair.

    Args:
        value: A time string, e.g. "08:00".

    Returns:
        The hour and minute as integers.

    Raises:
        ConfigError: If ``value`` isn't a valid ``HH:MM`` time.
    """
    parts = value.split(":")
    if len(parts) != 2:
        raise ConfigError(f"Invalid WAKE_TIMES entry '{value}', expected HH:MM.")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ConfigError(f"Invalid WAKE_TIMES entry '{value}', expected HH:MM.") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ConfigError(f"Invalid WAKE_TIMES entry '{value}', hour/minute out of range.")
    return hour, minute


def update_pmset_wake_schedule(wake_times: list[str]) -> str:
    """Register the earliest configured time as the macOS recurring wake time.

    Args:
        wake_times: Configured "HH:MM" wake times.

    Returns:
        The "HH:MM" time that was actually registered with `pmset`.

    Raises:
        ConfigError: If ``wake_times`` is empty or contains an invalid entry.
    """
    if not wake_times:
        raise ConfigError("WAKE_TIMES is empty; nothing to schedule.")
    for value in wake_times:
        _parse_wake_time(value)  # validate every entry up front

    chosen = min(wake_times, key=_parse_wake_time)
    subprocess.run(
        ["sudo", "pmset", "repeat", "wake", PMSET_DAYS, f"{chosen}:00"],
        check=True,
    )
    return chosen


def find_vocab_batch_executable() -> str:
    """Locate the installed ``vocab-batch`` console script.

    Returns:
        The absolute path to the ``vocab-batch`` executable.

    Raises:
        ConfigError: If it can't be found on PATH — usually means the
            project wasn't installed with ``pip install -e '.[dev]'`` in
            the active environment.
    """
    path = shutil.which("vocab-batch")
    if path is None:
        raise ConfigError(
            "Could not find the 'vocab-batch' executable on PATH. "
            "Run 'pip install -e .[dev]' first."
        )
    return path


def build_launch_agent_plist(vocab_batch_path: str, wake_times: list[str]) -> dict[str, Any]:
    """Build the LaunchAgent plist contents as a plain dict.

    Args:
        vocab_batch_path: Absolute path to the ``vocab-batch`` executable.
        wake_times: Configured "HH:MM" wake times, one
            ``StartCalendarInterval`` entry is produced per time.

    Returns:
        A dict suitable for :func:`plistlib.dump`.
    """
    intervals = []
    for value in wake_times:
        hour, minute = _parse_wake_time(value)
        intervals.append({"Hour": hour, "Minute": minute})

    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [vocab_batch_path],
        "WorkingDirectory": str(config.PROJECT_DIR),
        "StartCalendarInterval": intervals,
        "StandardOutPath": str(config.LOG_DIR / "launchd.out.log"),
        "StandardErrorPath": str(config.LOG_DIR / "launchd.err.log"),
    }


def write_launch_agent_plist(plist: dict[str, Any], path: Path) -> None:
    """Write a plist dict to disk in Apple's binary-safe XML format.

    Args:
        plist: The plist contents, as built by :func:`build_launch_agent_plist`.
        path: Destination path for the ``.plist`` file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        plistlib.dump(plist, f)


def load_launch_agent(path: Path) -> None:
    """(Re)load the LaunchAgent with `launchctl`.

    Unloads any previously loaded copy first (ignoring errors, since it may
    not have been loaded yet) so re-running this after an edit takes effect.

    Args:
        path: Path to the ``.plist`` file to load.
    """
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True, check=False)
    subprocess.run(["launchctl", "load", str(path)], check=True)


def generate_launchagent() -> Path:
    """Run the full scheduling setup: pmset wake time + LaunchAgent.

    Returns:
        The path the LaunchAgent plist was written to.
    """
    vocab_batch_path = find_vocab_batch_executable()
    update_pmset_wake_schedule(config.WAKE_TIMES)

    plist = build_launch_agent_plist(vocab_batch_path, config.WAKE_TIMES)
    plist_path = LAUNCH_AGENTS_DIR / f"{LAUNCH_AGENT_LABEL}.plist"
    write_launch_agent_plist(plist, plist_path)
    load_launch_agent(plist_path)
    return plist_path


def main() -> None:
    """CLI entry point for the ``vocab-setup-schedule`` command."""
    plist_path = generate_launchagent()
    chosen = min(config.WAKE_TIMES, key=_parse_wake_time)
    print(f"LaunchAgent written and loaded: {plist_path}")
    print(f"Triggers vocab-batch at: {', '.join(config.WAKE_TIMES)}")
    print(
        f"macOS wake-from-sleep is registered for {chosen} only (pmset supports a single "
        "recurring wake time) — other times only fire if the Mac is already awake."
    )


if __name__ == "__main__":
    main()
