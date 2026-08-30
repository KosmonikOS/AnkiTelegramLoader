"""Trigger an AnkiWeb/mobile sync via Anki.app's own sync-on-open feature.

Reimplementing Anki's sync protocol headlessly is unnecessary complexity.
Instead this opens Anki.app (which — once "Automatically sync on profile
open and close" is enabled in Preferences → Sync, a one-time manual setup
step — syncs on open and on quit), waits a heuristic window for that sync to
finish, then quits it again.
"""

import subprocess
import time

from vocab_pipeline import config

ANKI_APP_NAME = "Anki"


def trigger_sync(wait_seconds: int = config.ANKI_SYNC_WAIT_SECONDS) -> None:
    """Open Anki.app, wait for sync-on-open to run, then quit it.

    Args:
        wait_seconds: How long to leave Anki.app open before quitting.
            Defaults to ``config.ANKI_SYNC_WAIT_SECONDS``. This is a
            heuristic, not a completion signal — raise it if logs show
            Anki quitting mid-sync.
    """
    subprocess.run(["open", "-a", ANKI_APP_NAME], check=False)
    time.sleep(wait_seconds)
    subprocess.run(
        ["osascript", "-e", f'tell application "{ANKI_APP_NAME}" to quit'],
        capture_output=True,
        check=False,
    )


def main() -> None:
    """CLI entry point for manually triggering a sync."""
    trigger_sync()
    print("Sync trigger complete.")


if __name__ == "__main__":
    main()
