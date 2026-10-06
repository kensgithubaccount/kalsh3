from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import services.forecasting.cpi_p11_runtime as runtime
from services.forecasting.cpi_p11_prospective_protocol import ProspectiveDecision


def _market(index: int, threshold: str) -> dict[str, object]:
    return {
        "ticker": f"KXCPI-26SEP-T{threshold}",
        "event_ticker": "KXCPI-26SEP",
        "market_type": "binary",
        "status": "active",
        "rules_primary": (
            "If the Consumer Price Index (CPI) increases by more than "
            f"{threshold}% (single-decimal) in September 2026, then the market resolves to Yes."
        ),
        "rules_secondary": "",
        "title": f"Will CPI rise more than {threshold}% in September 2026?",
        "subtitle": f"{threshold}%",
        "yes_sub_title": f"Above {threshold}%",
        "no_sub_title": f"Not above {threshold}%",
        "price_level_structure": "linear_cent",
        "floor_strike": threshold,
        "strike_type": "greater",
        "is_provisional": False,
        "mve_collection_ticker": None,
        "open_time": "2026-09-01T00:00:00Z",
        "close_time": "2026-10-14T12:25:00Z",
        "updated_time": f"2026-10-14T11:{index:02d}:00Z",
        "volume_fp": "10.00",
        "open_interest_fp": "0.00",
    }


def _markets_payload() -> dict[str, object]:
    thresholds = [f"{value / 10:.1f}" for value in range(-4, 10)]
    return {
        "markets": [_market(index, threshold) for index, threshold in enumerate(thresholds)],
        "cursor": "",
    }


def _preflight_file(paths: runtime.RunPaths) -> None:
    runtime.ensure_protocol(paths)
    runtime._write_json_exclusive(
        paths.preflight,
        {
            "protocol_sha256": runtime.PROTOCOL_SHA256,
            "market_signature": [
                {
                    "ticker": market.ticker,
                    "threshold": str(market.threshold),
                    "close_time_utc": runtime._iso(market.close_time),
                }
                for market in runtime._validate_markets(_markets_payload())
            ],
        },
    )


def test_frozen_protocol_is_copied_byte_exactly_and_never_rewritten(tmp_path: Path) -> None:
    paths = runtime.RunPaths(tmp_path / runtime.RUN_ROOT_NAME)
    runtime.ensure_protocol(paths)
    source = runtime._repo_root() / runtime.FROZEN_SPEC
    assert paths.protocol.read_bytes() == source.read_bytes()

    paths.protocol.write_text("{}")
    with pytest.raises(runtime.P11AuthorityError, match="differs"):
        runtime.ensure_protocol(paths)


def test_bls_schedule_parser_accepts_only_frozen_target_row() -> None:
    body = b"""
    <html><body><table>
    <tr><td>Consumer Price Index</td><td>September 2026</td>
    <td>Oct. 14, 2026</td><td>08:30 AM</td></tr>
    </table></body></html>
    """
    runtime._validate_bls_schedule(body)

    with pytest.raises(runtime.P11AuthorityError, match="date"):
        runtime._validate_bls_schedule(body.replace(b"Oct. 14", b"Oct. 15"))


def test_market_preflight_requires_complete_active_strict_gt_cohort() -> None:
    identities = runtime._validate_markets(_markets_payload())
    assert len(identities) == 14
    assert identities[0].threshold == Decimal("-0.4")
    assert identities[-1].threshold == Decimal("0.9")

    incomplete = _markets_payload()
    incomplete["markets"] = incomplete["markets"][:-1]  # type: ignore[index]
    with pytest.raises(runtime.P11AuthorityError, match="expected 14"):
        runtime._validate_markets(incomplete)

    bad = _markets_payload()
    bad["markets"][0]["status"] = "closed"  # type: ignore[index]
    with pytest.raises(runtime.P11AuthorityError, match="not admissible"):
        runtime._validate_markets(bad)


def test_live_candle_request_preserves_frozen_preclose_selector() -> None:
    market = runtime._validate_markets(_markets_payload())[0]
    path, start_ts, end_ts = runtime._live_candle_path(market)
    assert path.startswith(f"{runtime.public_read.BASE}/markets/")
    assert "/historical/" not in path
    assert "period_interval=60" in path
    assert end_ts == int(runtime.MARKET_CLOSE.timestamp())
    assert start_ts < end_ts


def test_reuters_pass_requires_two_independent_hosts_before_cutoff(tmp_path: Path) -> None:
    paths = runtime.RunPaths(tmp_path / runtime.RUN_ROOT_NAME)
    _preflight_file(paths)
    sentence_hash = "a" * 64
    record = {
        "event_ticker": "KXCPI-26SEP",
        "reference_month": "2026-09",
        "provider_organization": "Reuters",
        "measure": "headline CPI, month-over-month, seasonally adjusted, nonannualized",
        "value": "0.3",
        "reuters_attribution_verified": True,
        "reference_month_verified_in_body": True,
        "prospective_headline_mom_verified": True,
        "retrospective_language_found": False,
        "load_bearing_sentence_sha256": sentence_hash,
        "hosts": [
            {
                "host": "finance.yahoo.com",
                "url": "https://finance.yahoo.com/example",
                "http_status": 200,
                "retrieved_at": "2026-10-14T12:10:00Z",
                "published_at": "2026-10-14T10:00:00Z",
                "raw_response_sha256": "b" * 64,
                "load_bearing_sentence_sha256": sentence_hash,
            },
            {
                "host": "tradingview.com",
                "url": "https://tradingview.com/example",
                "http_status": 200,
                "retrieved_at": "2026-10-14T12:11:00Z",
                "published_at": "2026-10-14T10:01:00Z",
                "raw_response_sha256": "c" * 64,
                "load_bearing_sentence_sha256": sentence_hash,
            },
        ],
    }
    coverage = runtime.record_reuters_pass(
        paths,
        record,
        clock=lambda: datetime(2026, 10, 14, 12, 12, tzinfo=UTC),
    )
    assert coverage["terminal_state"] == "PASS"
    assert paths.reuters_receipt.is_file()
    assert paths.reuters_extract.is_file()

    second = runtime.RunPaths(tmp_path / "same-operator" / runtime.RUN_ROOT_NAME)
    _preflight_file(second)
    record["hosts"][1] = {  # type: ignore[index]
        **record["hosts"][0],  # type: ignore[index]
        "host": "ca.finance.yahoo.com",
        "url": "https://ca.finance.yahoo.com/example",
        "raw_response_sha256": "d" * 64,
    }
    with pytest.raises(runtime.P11AuthorityError, match="independently operated"):
        runtime.record_reuters_pass(
            second,
            record,
            clock=lambda: datetime(2026, 10, 14, 12, 12, tzinfo=UTC),
        )


def test_reuters_unknown_requires_completed_search_ladder(tmp_path: Path) -> None:
    paths = runtime.RunPaths(tmp_path / runtime.RUN_ROOT_NAME)
    _preflight_file(paths)
    record = {
        "event_ticker": runtime.TARGET_EVENT,
        "reference_month": runtime.TARGET_REFERENCE_MONTH,
        "terminal_state": "UNKNOWN_SEARCHED_NO_QUALIFYING_OBSERVATION",
        "reason": "bounded search completed without an admissible candidate",
        "all_three_search_rungs_completed": False,
        "attempted_host_groups": ["reuters.com", "yahoo.com"],
    }
    with pytest.raises(runtime.P11AuthorityError, match="all three frozen search rungs"):
        runtime.record_reuters_nonpass(
            paths,
            record,
            clock=lambda: datetime(2026, 10, 14, 12, 12, tzinfo=UTC),
        )

    record["all_three_search_rungs_completed"] = True
    coverage = runtime.record_reuters_nonpass(
        paths,
        record,
        clock=lambda: datetime(2026, 10, 14, 12, 12, tzinfo=UTC),
    )
    assert coverage["terminal_state"] == "UNKNOWN_SEARCHED_NO_QUALIFYING_OBSERVATION"
    assert coverage["all_three_search_rungs_completed"] is True


def test_preclose_without_reuters_receipt_is_failure_not_searched_unknown(
    tmp_path: Path,
) -> None:
    paths = runtime.RunPaths(tmp_path / runtime.RUN_ROOT_NAME)
    _preflight_file(paths)
    runtime._write_json_exclusive(
        paths.market_capture,
        {
            "protocol_sha256": runtime.PROTOCOL_SHA256,
            "event_ticker": runtime.TARGET_EVENT,
            "markets": [],
        },
    )
    coverage = runtime.close_pre_release(
        paths,
        clock=lambda: datetime(2026, 10, 14, 12, 20, 1, tzinfo=UTC),
    )
    assert coverage["terminal_state"] == "FAILURE_ACQUISITION_OR_AUTHORITY"
    assert coverage["all_three_search_rungs_completed"] is False


def test_run_root_name_is_frozen(tmp_path: Path) -> None:
    with pytest.raises(runtime.P11AuthorityError, match="logical run root"):
        runtime.ensure_protocol(runtime.RunPaths(tmp_path / "wrong-root"))


def test_reuters_pass_cannot_be_backfilled_after_cutoff(tmp_path: Path) -> None:
    paths = runtime.RunPaths(tmp_path / runtime.RUN_ROOT_NAME)
    _preflight_file(paths)
    with pytest.raises(runtime.P11TimingError, match="after 12:20Z"):
        runtime.record_reuters_pass(
            paths,
            {},
            clock=lambda: datetime(2026, 10, 14, 12, 20, tzinfo=UTC),
        )


def test_preclose_watchdog_terminalizes_missed_market_capture(tmp_path: Path) -> None:
    paths = runtime.RunPaths(tmp_path / runtime.RUN_ROOT_NAME)
    runtime.ensure_protocol(paths)
    result = runtime.close_pre_release(
        paths,
        clock=lambda: datetime(2026, 10, 14, 12, 20, 1, tzinfo=UTC),
    )
    assert result["decision"] == ProspectiveDecision.INSUFFICIENT.value
    assert result["no_backfill"] is True
    assert paths.failure.is_file()
    runtime.verify_terminal_manifest(paths)


def test_score_uses_common_denominator_and_seals_result(tmp_path: Path) -> None:
    paths = runtime.RunPaths(tmp_path / runtime.RUN_ROOT_NAME)
    _preflight_file(paths)

    markets = runtime._validate_markets(_markets_payload())
    rows: list[dict[str, object]] = []
    asks = ["0.6", "0.4", "0.4", "0.6"] + [None] * 10
    for market, ask in zip(markets, asks, strict=True):
        evidence_id = f"evidence-{market.ticker}"
        row = {
            "market_ticker": market.ticker,
            "threshold": str(market.threshold),
            "yes_ask": ask,
            "candle_end_period_ts": 1791979200,
            "missing_side_reason": None if ask is not None else "INCOMPLETE_YES_QUOTE",
            "evidence_id": evidence_id,
            "raw_body_sha256": "f" * 64,
        }
        rows.append(row)
        runtime._write_json_exclusive(
            paths.root / "kalshi" / "candles" / f"{market.ticker}.json",
            {
                "body_sha256": "f" * 64,
                "raw_body_b64": "e30=",
                "selected": {
                    "evidence_id": evidence_id,
                    "yes_ask": ask,
                },
            },
        )

    runtime._write_json_exclusive(
        paths.market_capture,
        {
            "protocol_sha256": runtime.PROTOCOL_SHA256,
            "event_ticker": runtime.TARGET_EVENT,
            "markets": rows,
        },
    )
    runtime._write_json_exclusive(
        paths.reuters_receipt,
        {
            "protocol_sha256": runtime.PROTOCOL_SHA256,
            "event_ticker": runtime.TARGET_EVENT,
            "value": "0.3",
        },
    )
    runtime._write_json_exclusive(
        paths.reuters_coverage,
        {
            "protocol_sha256": runtime.PROTOCOL_SHA256,
            "event_ticker": runtime.TARGET_EVENT,
            "terminal_state": "PASS",
        },
    )
    runtime._write_json_exclusive(
        paths.bls_initial_release,
        {
            "protocol_sha256": runtime.PROTOCOL_SHA256,
            "event_ticker": runtime.TARGET_EVENT,
            "value": "0.3",
        },
    )

    result = runtime.score_and_seal(
        paths,
        clock=lambda: datetime(2026, 10, 14, 13, 0, tzinfo=UTC),
    )
    assert result["decision"] == ProspectiveDecision.SUPPORT.value
    assert result["eligible_siblings"] == 4
    assert result["reuters_correct"] == 4
    assert result["kalshi_correct"] == 2
    assert result["promotion_authority"] == "NONE"
    assert result["phase1_economic_test_authorized"] is False
    runtime.verify_terminal_manifest(paths)


def test_terminal_manifest_detects_mutation(tmp_path: Path) -> None:
    paths = runtime.RunPaths(tmp_path / runtime.RUN_ROOT_NAME)
    runtime.ensure_protocol(paths)
    runtime.write_failure(
        paths,
        stage="TEST",
        reason="TEST_FAILURE",
        now=datetime(2026, 10, 14, 12, 20, tzinfo=UTC),
    )
    runtime.verify_terminal_manifest(paths)
    paths.failure.write_text("{}")
    with pytest.raises(runtime.P11AuthorityError, match="hash mismatch"):
        runtime.verify_terminal_manifest(paths)
