from datetime import UTC, datetime, timedelta

from nowcast_service.decision_engine import SourceState
from nowcast_service.runtime.models import SourceSnapshot


def test_live_snapshot_becomes_stale_then_failed() -> None:
    now = datetime(2026, 8, 5, 8, 0, tzinfo=UTC)
    snapshot = SourceSnapshot(
        source_id="DWD_RV",
        source_input_id="rv:1",
        product="DWD_RV",
        cycle_time=now,
        downloaded_at=now,
        parsed_at=now,
        valid_from=now,
        valid_until=now + timedelta(minutes=30),
        evaluated_at=now,
        state=SourceState.LIVE,
        content_sha256="a" * 64,
        is_complete=True,
        stale_after_seconds=900,
        invalid_after_seconds=1800,
    )
    assert snapshot.effective_state(now + timedelta(minutes=15)) is SourceState.LIVE
    assert snapshot.effective_state(now + timedelta(minutes=16)) is SourceState.STALE
    assert snapshot.effective_state(now + timedelta(minutes=31)) is SourceState.FAILED
