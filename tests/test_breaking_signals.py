from datetime import UTC, datetime, timedelta

import pytest

from services.breaking_signals.domain import (
    AdapterKind,
    ExternalSignal,
    SignalRegistry,
    SignalStage,
    SourceClass,
    content_hash,
)

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def signal(stage: SignalStage = SignalStage.LEAD, production: bool = False) -> ExternalSignal:
    return ExternalSignal(
        "id",
        "official-feed",
        SourceClass.PRIMARY,
        NOW,
        NOW,
        NOW,
        NOW,
        content_hash("official-feed", "item-1", "release"),
        "item-1",
        "release",
        stage,
        ("M",),
        production,
    )


def test_signals_are_shadow_only_and_deduplicated() -> None:
    registry = SignalRegistry()
    assert (
        registry.ingest(signal()).stage == SignalStage.LEAD
        and registry.ingest(signal()).stage == SignalStage.DUPLICATE
    )
    with pytest.raises(ValueError):
        signal(production=True)


def test_source_health_and_authorized_adapter_inventory() -> None:
    registry = SignalRegistry()
    registry.ingest(signal())
    assert (
        registry.health("official-feed", NOW + timedelta(minutes=1)).healthy
        and not registry.health("official-feed", NOW + timedelta(hours=1)).healthy
    )
    assert {
        AdapterKind.POLYMARKET,
        AdapterKind.PREDICTBUDDY,
        AdapterKind.RSS,
        AdapterKind.REDDIT,
    }.issubset(set(AdapterKind))
