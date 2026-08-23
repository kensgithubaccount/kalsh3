"""M27R must never obtain exact-one selection by silently dropping an active market."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.market_universe.public_read import BASE
from services.supervised_canary.m27r_public_adapter import (
    GetOnlyPublicEvidenceProvider,
    M27RPublicAdapterError,
)
from tests.test_m27r_public_adapter import NOW, _empty_public, _provider, _response


def _one_active_public() -> dict[str, object]:
    public = _empty_public()
    markets = public["markets"]
    assert isinstance(markets, dict)
    markets["pages"] = [
        _response(
            BASE + "/markets?series_ticker=KXHIGHCHI&limit=1000",
            {
                "markets": [
                    {
                        "ticker": "KXHIGHCHI-TEST",
                        "event_ticker": "KXHIGHCHI-EVENT",
                        "status": "active",
                    }
                ],
                "cursor": "",
            },
        )
    ]
    markets["market_count"] = 1
    markets["total_returned"] = 1
    return public


def test_active_market_evaluation_failure_aborts_entire_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public = _one_active_public()
    provider = _provider(tmp_path, public_acceptance_acquirer=lambda **_: public)

    def fail_market_slice(
        self: GetOnlyPublicEvidenceProvider,
        **_: object,
    ) -> None:
        del self
        raise M27RPublicAdapterError("synthetic exact-market evidence failure")

    monkeypatch.setattr(GetOnlyPublicEvidenceProvider, "_build_market_slice", fail_market_slice)

    with pytest.raises(
        M27RPublicAdapterError,
        match="active market KXHIGHCHI-TEST could not be fully evaluated",
    ):
        provider.collect_public_evidence(clock=lambda: NOW)
