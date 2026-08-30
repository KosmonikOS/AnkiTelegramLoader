"""Master orchestrator: drain, process, and sync in one scheduled run.

This is what the LaunchAgent (see :mod:`vocab_pipeline.generate_launchagent`)
invokes on each wake. Each phase is attempted independently — a failure in
one (e.g. Telegram unreachable) never skips the others, since there may
still be earlier-queued words worth processing, or a sync worth running,
regardless. Every run appends one line to ``logs/run_log.txt`` summarizing
what happened, so unattended runs stay inspectable after the fact.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from vocab_pipeline import config, drain, process, sync_trigger


@dataclass
class BatchResult:
    """Outcome of one full drain → process → sync run.

    Attributes:
        started_at: ISO 8601 UTC timestamp of when the run began.
        drained: Number of new words pulled from Telegram.
        processed: Number of pending words attempted against Anki.
        succeeded: Number of those written successfully.
        failed: Number of those that errored.
        synced: Whether the sync-trigger phase ran without raising.
        errors: Human-readable errors from any phase, in phase order.
    """

    started_at: str
    drained: int
    processed: int
    succeeded: int
    failed: int
    synced: bool
    errors: list[str]


def run_batch() -> BatchResult:
    """Run drain, process, and sync-trigger once, in sequence.

    Returns:
        A summary of what each phase did, including any errors.
    """
    started_at = datetime.now(UTC).isoformat()
    errors: list[str] = []

    drained_count = 0
    try:
        drained_count = len(drain.drain())
    except Exception as exc:
        errors.append(f"drain: {exc}")

    processed = succeeded = failed = 0
    try:
        summary = process.process_pending()
        processed, succeeded, failed = summary.processed, summary.succeeded, summary.failed
    except Exception as exc:
        errors.append(f"process: {exc}")

    synced = False
    try:
        sync_trigger.trigger_sync()
        synced = True
    except Exception as exc:
        errors.append(f"sync: {exc}")

    return BatchResult(
        started_at=started_at,
        drained=drained_count,
        processed=processed,
        succeeded=succeeded,
        failed=failed,
        synced=synced,
        errors=errors,
    )


def _format_log_line(result: BatchResult) -> str:
    """Render a :class:`BatchResult` as one run_log.txt line.

    Args:
        result: The batch result to format.

    Returns:
        A single-line, newline-terminated log entry.
    """
    status = "ok" if not result.errors else f"errors={'; '.join(result.errors)}"
    return (
        f"{result.started_at} drained={result.drained} processed={result.processed} "
        f"succeeded={result.succeeded} failed={result.failed} synced={result.synced} "
        f"{status}\n"
    )


def main() -> None:
    """CLI entry point for the ``vocab-batch`` command."""
    result = run_batch()
    with config.RUN_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(_format_log_line(result))

    print(
        f"Drained {result.drained}, processed {result.processed} "
        f"({result.succeeded} succeeded, {result.failed} failed), "
        f"synced={result.synced}."
    )
    if result.errors:
        for error in result.errors:
            print(f"  error: {error}")


if __name__ == "__main__":
    main()
