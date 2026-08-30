"""Tests for vocab_pipeline.run_batch."""

from typing import Any
from unittest.mock import patch

from vocab_pipeline import run_batch
from vocab_pipeline.process import ProcessSummary


def test_run_batch_happy_path(configured_env: Any) -> None:
    with (
        patch("vocab_pipeline.run_batch.generate_launchagent.schedule_wake_events") as mock_rearm,
        patch("vocab_pipeline.run_batch.drain.drain", return_value=["w1", "w2"]),
        patch(
            "vocab_pipeline.run_batch.process.process_pending",
            return_value=ProcessSummary(processed=2, succeeded=2, failed=0),
        ),
        patch("vocab_pipeline.run_batch.sync_trigger.trigger_sync") as mock_sync,
    ):
        result = run_batch.run_batch()

    mock_rearm.assert_called_once()
    mock_sync.assert_called_once()
    assert result.rearmed is True
    assert result.drained == 2
    assert result.processed == 2
    assert result.succeeded == 2
    assert result.synced is True
    assert result.errors == []


def test_run_batch_isolates_phase_failures(configured_env: Any) -> None:
    """A drain failure must not prevent rearm/process/sync from still running."""
    with (
        patch("vocab_pipeline.run_batch.generate_launchagent.schedule_wake_events"),
        patch("vocab_pipeline.run_batch.drain.drain", side_effect=RuntimeError("telegram down")),
        patch(
            "vocab_pipeline.run_batch.process.process_pending",
            return_value=ProcessSummary(processed=1, succeeded=1, failed=0),
        ),
        patch("vocab_pipeline.run_batch.sync_trigger.trigger_sync") as mock_sync,
    ):
        result = run_batch.run_batch()

    mock_sync.assert_called_once()
    assert result.rearmed is True
    assert result.drained == 0
    assert result.processed == 1
    assert len(result.errors) == 1
    assert "telegram down" in result.errors[0]


def test_run_batch_rearm_failure_does_not_block_other_phases(configured_env: Any) -> None:
    """A pmset failure (e.g. missing NOPASSWD sudo) must not skip drain/process/sync."""
    with (
        patch(
            "vocab_pipeline.run_batch.generate_launchagent.schedule_wake_events",
            side_effect=RuntimeError("sudo: a password is required"),
        ),
        patch("vocab_pipeline.run_batch.drain.drain", return_value=[]),
        patch(
            "vocab_pipeline.run_batch.process.process_pending",
            return_value=ProcessSummary(processed=0, succeeded=0, failed=0),
        ),
        patch("vocab_pipeline.run_batch.sync_trigger.trigger_sync") as mock_sync,
    ):
        result = run_batch.run_batch()

    mock_sync.assert_called_once()
    assert result.rearmed is False
    assert result.synced is True
    assert len(result.errors) == 1
    assert "rearm:" in result.errors[0]


def test_main_appends_run_log(configured_env: Any) -> None:
    config = configured_env
    with (
        patch("vocab_pipeline.run_batch.generate_launchagent.schedule_wake_events"),
        patch("vocab_pipeline.run_batch.drain.drain", return_value=[]),
        patch(
            "vocab_pipeline.run_batch.process.process_pending",
            return_value=ProcessSummary(processed=0, succeeded=0, failed=0),
        ),
        patch("vocab_pipeline.run_batch.sync_trigger.trigger_sync"),
    ):
        run_batch.main()

    log_contents = config.RUN_LOG_PATH.read_text(encoding="utf-8")
    assert "rearmed=True" in log_contents
    assert "drained=0" in log_contents
    assert "synced=True" in log_contents
