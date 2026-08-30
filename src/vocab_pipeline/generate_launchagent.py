"""Set up unattended scheduling: a self-renewing pmset wake schedule and a LaunchAgent.

Reads ``config.WAKE_TIMES`` and, in one step, registers a macOS wake-from-
sleep schedule and a LaunchAgent that runs ``vocab-batch`` at each
configured time. Re-run this after editing ``WAKE_TIMES`` in ``.env`` —
never hand-edit the generated plist or `pmset` state directly.

macOS's ``pmset repeat`` mechanism supports only a single recurring wake
event, not one per ``WAKE_TIMES`` entry (see ``man pmset``), so it can't
express "wake at 08:00 and 20:00 every day" directly. Instead this uses
``pmset schedule wake``, which accepts multiple one-time wake events in a
single command (one ``wake "<date>"`` pair per configured time). Because
those are one-shot — each is consumed once it fires — :func:`schedule_wake_events`
is also called as the first phase of every :mod:`vocab_pipeline.run_batch`
run, so each run re-arms the next occurrence of every configured time. As
long as runs keep completing, the schedule keeps renewing itself
indefinitely; see the README for what happens if that chain breaks.
"""

import plistlib
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from vocab_pipeline import config
from vocab_pipeline.config import ConfigError

LAUNCH_AGENT_LABEL = "com.user.vocabpipeline"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PMSET_DATE_FORMAT = "%m/%d/%y %H:%M:%S"
REARM_BUFFER = timedelta(minutes=1)
"""How close to "now" a wake time must be to count as already passed."""


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


def _next_occurrence(hour: int, minute: int, now: datetime) -> datetime:
    """Compute the next wall-clock occurrence of an ``HH:MM`` time.

    Args:
        hour: Target hour (0-23).
        minute: Target minute (0-59).
        now: The current local time.

    Returns:
        ``now``'s date at ``hour:minute``, or the following day's if that
        moment is within :data:`REARM_BUFFER` of ``now`` or already past —
        so re-arming a wake event from inside the run it just triggered
        always rolls over to tomorrow instead of re-scheduling a moment
        that's already gone.
    """
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now + REARM_BUFFER:
        candidate += timedelta(days=1)
    return candidate


def schedule_wake_events(wake_times: list[str], now: datetime | None = None) -> list[datetime]:
    """Arm a one-time `pmset` wake event for each configured time's next occurrence.

    macOS's `pmset schedule` holds a set of one-shot events and is not
    additive across separate invocations — a later call replaces whatever a
    previous call scheduled — so every configured time is (re-)armed
    together in a single `pmset schedule` command.

    Args:
        wake_times: Configured "HH:MM" wake times.
        now: The current local time. Defaults to :func:`datetime.now`.

    Returns:
        The datetime scheduled for each entry, in ``wake_times`` order.

    Raises:
        ConfigError: If ``wake_times`` is empty or contains an invalid entry.
    """
    if not wake_times:
        raise ConfigError("WAKE_TIMES is empty; nothing to schedule.")
    current = now if now is not None else datetime.now()

    occurrences = [_next_occurrence(*_parse_wake_time(value), current) for value in wake_times]

    command = ["sudo", "pmset", "schedule"]
    for occurrence in occurrences:
        command += ["wake", occurrence.strftime(PMSET_DATE_FORMAT)]
    subprocess.run(command, check=True)

    return occurrences


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
    """Run the full scheduling setup: pmset wake events + LaunchAgent.

    Returns:
        The path the LaunchAgent plist was written to.
    """
    vocab_batch_path = find_vocab_batch_executable()
    schedule_wake_events(config.WAKE_TIMES)

    plist = build_launch_agent_plist(vocab_batch_path, config.WAKE_TIMES)
    plist_path = LAUNCH_AGENTS_DIR / f"{LAUNCH_AGENT_LABEL}.plist"
    write_launch_agent_plist(plist, plist_path)
    load_launch_agent(plist_path)
    return plist_path


def main() -> None:
    """CLI entry point for the ``vocab-setup-schedule`` command."""
    plist_path = generate_launchagent()
    print(f"LaunchAgent written and loaded: {plist_path}")
    print(f"Triggers vocab-batch at: {', '.join(config.WAKE_TIMES)}")
    print(
        "Each time's next wake event is armed with `pmset schedule wake`. "
        "vocab-batch re-arms the following occurrence of every time on each "
        "run, so run `pmset -g sched` any time to confirm all of them are "
        "still pending."
    )


if __name__ == "__main__":
    main()
