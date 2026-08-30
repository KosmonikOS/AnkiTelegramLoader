"""Ensure Anki.app is not running before touching the collection directly.

The ``anki`` pylib and the Anki.app GUI must never have the collection open
at the same time — doing so risks lock conflicts or corruption. Call
:func:`ensure_anki_closed` before opening a ``Collection`` headlessly.
"""

import subprocess
import time

from vocab_pipeline import config

ANKI_PROCESS_NAME = "Anki"


def _is_anki_running() -> bool:
    """Check whether the Anki.app process is currently running.

    Returns:
        True if a process named ``Anki`` is found via ``pgrep``.
    """
    result = subprocess.run(["pgrep", "-x", ANKI_PROCESS_NAME], capture_output=True, check=False)
    return result.returncode == 0


def ensure_anki_closed(timeout: int = config.ANKI_CLOSE_TIMEOUT_SECONDS) -> None:
    """Quit Anki.app if it's running, waiting up to ``timeout`` seconds.

    Attempts a polite quit via AppleScript first — force-killing immediately
    risks the same corruption this guard exists to prevent, if Anki happened
    to be mid-write. Only force-kills as a last resort once ``timeout`` is
    exceeded.

    Args:
        timeout: Seconds to wait for a graceful quit before force-killing.
            Defaults to ``config.ANKI_CLOSE_TIMEOUT_SECONDS``.
    """
    if not _is_anki_running():
        return

    subprocess.run(
        ["osascript", "-e", 'tell application "Anki" to quit'],
        capture_output=True,
        check=False,
    )

    waited = 0
    while _is_anki_running():
        if waited >= timeout:
            subprocess.run(["pkill", "-x", ANKI_PROCESS_NAME], capture_output=True, check=False)
            break
        time.sleep(1)
        waited += 1
