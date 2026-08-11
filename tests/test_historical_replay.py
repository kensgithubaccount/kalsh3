from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.historical_replay.archive import ArchiveManifest
from services.historical_replay.dataset import (
    DatasetManifest,
    GapSeverity,
    ReplayCapability,
    ReplayGap,
    SettlementLabel,
    SettlementState,
    VintageValue,
)
from services.historical_replay.domain import (
    Availability,
    AvailabilityBasis,
    AvailabilityQuality,
    ReplayError,
    ReplayEvent,
    partition_for,
    point_in_time,
)
from services.historical_replay.replay import ReplayAccessor, ReplayClock, stream_available
from services.historical_replay.routing import Candle, CutoffHistory, HistoricalRouter, RecordKind

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def availability(index: int = 0) -> Availability:
    moment = NOW + timedelta(seconds=index)
    return Availability.observed_live(moment, moment, moment, moment)


def event(index: int, *, available: Availability | None = None) -> ReplayEvent:
    return ReplayEvent(
        str(index),
        "trade",
        "kalshi",
        available or availability(index),
        index,
        None,
        index,
        ("M",),
        "r",
        "n",
        "p1",
        "s1",
        {"index": index},
    )


def test_live_and_reconstructed_availability_cannot_falsify_latency() -> None:
    live = availability()
    assert live.measured_latency == timedelta(0)
    reconstructed = Availability.reconstructed(
        AvailabilityBasis.RECONSTRUCTED_EXCHANGE,
        source_event_at=NOW,
        source_publish_at=None,
        actual_bot_ingest_at=NOW + timedelta(days=700),
        assumed_latency=timedelta(seconds=3),
        quality=AvailabilityQuality.AUTHORITATIVE_RECONSTRUCTION,
    )
    assert reconstructed.replay_available_at == NOW + timedelta(seconds=3)
    assert reconstructed.actual_bot_ingest_at.year == 2027
    assert reconstructed.measured_latency is None
    with pytest.raises(ReplayError):
        Availability(
            NOW,
            NOW,
            NOW,
            NOW,
            NOW - timedelta(seconds=1),
            AvailabilityBasis.OBSERVED_LIVE,
            AvailabilityQuality.MEASURED,
            None,
        )


def test_unknown_and_future_information_are_not_visible() -> None:
    unknown = Availability.unknown(
        source_event_at=NOW, source_publish_at=NOW, actual_bot_ingest_at=NOW + timedelta(days=1)
    )
    selected = point_in_time(
        [event(2), event(0), event(1), event(9, available=unknown)], NOW + timedelta(seconds=1)
    )
    assert [item.event_id for item in selected] == ["0", "1"]
    assert [item.event_id for item in ReplayAccessor(tuple(selected)).at(NOW)] == ["0"]


def test_cutoff_history_seam_overlap_and_backward_warning() -> None:
    payload = {
        "market_settled_ts": NOW.isoformat(),
        "trades_created_ts": NOW.isoformat(),
        "orders_updated_ts": NOW.isoformat(),
    }
    history = CutoffHistory()
    cutoff = history.add(payload, NOW, "official-2026")
    assert partition_for(NOW - timedelta(microseconds=1), NOW).value == "HISTORICAL"
    assert partition_for(NOW, NOW).value == "LIVE"
    rows = [{"id": "a", "created": NOW.isoformat()}]
    merged = HistoricalRouter().merge(
        RecordKind.TRADE, cutoff, rows, rows, id_field="id", time_field="created"
    )
    assert merged.duplicates_removed == 1 and merged.seam_ambiguity
    older = {key: (NOW - timedelta(days=1)).isoformat() for key in payload}
    assert history.add(older, NOW + timedelta(hours=1), "official-2026").backward_warning


def test_candle_only_appears_after_close_and_has_no_intrabar_path() -> None:
    candle = Candle(
        "M",
        NOW,
        1,
        Decimal(".4"),
        Decimal(".6"),
        Decimal(".3"),
        Decimal(".5"),
        Decimal("2.25"),
        timedelta(seconds=2),
    )
    assert not candle.visible(NOW + timedelta(seconds=61))
    assert candle.visible(NOW + timedelta(seconds=62))
    with pytest.raises(ValueError):
        candle.intrabar_path()


def test_archive_dataset_gap_fidelity_and_label_finality() -> None:
    archive = ArchiveManifest.create(
        provider="kalshi",
        endpoint="/historical/trades",
        parameters={},
        retrieved_at=NOW,
        page_number=1,
        cursor_in=None,
        cursor_out=None,
        raw=b'{"trades":[]}',
        normalized=[],
        parser_version="1",
        spec_version="1",
    )
    assert len(archive.raw_content_hash) == 64
    gap = ReplayGap("g", "pagination", NOW, None, GapSeverity.CRITICAL, "page failed")

    def build(created_at: datetime) -> DatasetManifest:
        return DatasetManifest.build(
            created_at=created_at,
            start_at=NOW,
            end_at=NOW + timedelta(days=1),
            markets=("M",),
            providers=("kalshi",),
            archive_manifests=(archive.manifest_id,),
            cutoff_observations=("c",),
            rules_quality={"FINAL_RULES_ONLY": 1},
            fee_quality={"UNKNOWN": 1},
            availability_basis={"RECONSTRUCTED_EXCHANGE": 1},
            capabilities={ReplayCapability.ONE_MINUTE_CANDLES},
            gaps=(gap,),
            excluded_records=0,
            parser_versions=("1",),
            code_git_sha="abc",
        )

    first = build(NOW)
    second = build(NOW + timedelta(hours=1))
    assert first.content_hash == second.content_hash
    with pytest.raises(ReplayError):
        first.validate(ReplayCapability.TRADE_EVENTS)
    label = SettlementLabel(
        "l",
        "M",
        "r",
        "yes",
        Decimal("1.00"),
        NOW,
        None,
        SettlementState.DETERMINED,
        archive.manifest_id,
        NOW,
    )
    amended = SettlementLabel(
        "l2",
        "M",
        "r",
        "no",
        Decimal("0.00"),
        NOW,
        NOW + timedelta(days=1),
        SettlementState.FINALIZED,
        archive.manifest_id,
        NOW,
        label.label_id,
    )
    assert not label.training_eligible and amended.training_eligible


def test_correction_and_economic_revision_do_not_leak() -> None:
    original = VintageValue("CPI", Decimal("3.2"), NOW, availability())
    revision = VintageValue(
        "CPI", Decimal("3.1"), NOW + timedelta(days=30), availability(30), "CPI@0"
    )
    assert original.visible(NOW) and not revision.visible(NOW)
    assert revision.visible(NOW + timedelta(seconds=30))


def test_clock_checkpoint_and_100k_stream_are_deterministic() -> None:
    items = (event(index) for index in range(100_000))
    assert sum(1 for _ in stream_available(items, NOW + timedelta(seconds=99_999))) == 100_000
    clock = ReplayClock(NOW, iter((event(0), event(1), event(2))))
    assert [item.event_id for item in clock.run_until(NOW + timedelta(seconds=1))] == ["0", "1"]
    checkpoint = clock.checkpoint({"position": "exact"})
    assert checkpoint.last_event_id == "1" and checkpoint.applied_count == 2
    assert clock.step().event_id == "2"  # type: ignore[union-attr]


def test_replay_substrate_has_no_risk_or_execution_reachability() -> None:
    root = Path("services/historical_replay")
    replay_code = "\n".join(
        path.read_text() for path in root.glob("*.py") if path.name != "client.py"
    )
    assert "risk_engine" not in replay_code
    assert "submit_order" not in replay_code
    assert "RequestSigner" not in replay_code
