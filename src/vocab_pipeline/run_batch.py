"""Master orchestrator: rearm, drain, process, and sync in one scheduled run.

This is what the LaunchAgent (see :mod:`vocab_pipeline.generate_launchagent`)
invokes on each wake. Each phase is attempted independently — a failure in
one (e.g. Telegram unreachable) never skips the others, since there may
still be earlier-queued words worth processing, or a sync worth running,
regardless. Every run appends one line to ``logs/run_log.txt`` summarizing
what happened, so unattended runs stay inspectable after the fact.

The wake-schedule rearm runs first and is tried independently of everything
else: it's what keeps macOS actually waking the Mac for future runs (see
:mod:`vocab_pipeline.generate_launchagent`), so it matters more than any
single word getting processed, and running it first minimizes the chance an
unrelated crash elsewhere in this run stops it from happening.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from vocab_pipeline import config, drain, generate_launchagent, process, sync_trigger


@dataclass
class BatchResult:
    """Outcome of one full rearm → drain → process → sync run.

    Attributes:
        started_at: ISO 8601 UTC timestamp of when the run began.
        rearmed: Whether the next wake events were successfully re-armed.
        drained: Number of new words pulled from Telegram.
        processed: Number of pending words attempted against Anki.
        succeeded: Number of those written successfully.
        failed: Number of those that errored.
        synced: Whether the sync-trigger phase ran without raising.
        errors: Human-readable errors from any phase, in phase order.
    """

    started_at: str
    rearmed: bool
    drained: int
    processed: int
    succeeded: int
    failed: int
    synced: bool
    errors: list[str]


def run_batch() -> BatchResult:
    """Run wake-rearm, drain, process, and sync-trigger once, in sequence.

    Returns:
        A summary of what each phase did, including any errors.
    """
    started_at = datetime.now(UTC).isoformat()
    errors: list[str] = []

    rearmed = False
    try:
        generate_launchagent.schedule_wake_events(config.WAKE_TIMES)
        rearmed = True
    except Exception as exc:
        errors.append(f"rearm: {exc}")

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
        rearmed=rearmed,
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
        f"{result.started_at} rearmed={result.rearmed} drained={result.drained} "
        f"processed={result.processed} succeeded={result.succeeded} failed={result.failed} "
        f"synced={result.synced} {status}\n"
    )


def main() -> None:
    """CLI entry point for the ``vocab-batch`` command."""
    result = run_batch()
    with config.RUN_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(_format_log_line(result))

    print(
        f"Rearmed={result.rearmed}. Drained {result.drained}, processed {result.processed} "
        f"({result.succeeded} succeeded, {result.failed} failed), "
        f"synced={result.synced}."
    )
    if result.errors:
        for error in result.errors:
            print(f"  error: {error}")


if __name__ == "__main__":
    main()
