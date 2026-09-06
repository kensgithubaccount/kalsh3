import hashlib
import json
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "docs/reviews/artifacts/cpi-p10e-phase0-after-cost-spec/spec.json"


def _spec() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(SPEC_PATH.read_bytes()))


def test_phase0_artifact_is_deterministically_sealed_without_results() -> None:
    spec = _spec()
    digest = spec["spec_digest_sha256"]
    unsigned = dict(spec)
    del unsigned["spec_digest_sha256"]
    payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert hashlib.sha256(payload.encode()).hexdigest() == digest
    assert spec["phase1_sealed_run_plan"]["no_real_run_in_phase0"] is True
    assert spec["aggregation_and_metrics"]["real_strategy_pnl_computed"] is False
    assert spec["aggregation_and_metrics"]["after_fee_performance_computed"] is False


def test_phase0_freezes_the_four_event_post_p10d_hypothesis() -> None:
    spec = _spec()
    assert spec["canonical_main_sha"] == "27f100f5da7e5a49044f2e4a68f6a6fecfff5216"
    assert [row["event_ticker"] for row in spec["event_roster"]] == [
        "CPI-23AUG",
        "KXCPI-25JUL",
        "KXCPI-25DEC",
        "KXCPI-26JAN",
    ]
    assert spec["strategy_hypothesis"]["name"] == "REUTERS-vs-MARKET DISAGREEMENT CROSS"
    assert spec["exploratory_disclosure"].startswith("This strategy rule is POST-P10D")
    assert spec["safety"] == {
        "research_only": True,
        "production_influence": "0",
        "a03_checkout": "DO NOT TOUCH; frozen at 3d31eb182d12b9ceea96709db1063ecd14d29289",
        "trading": "NONE",
    }


def test_phase1_is_blocked_by_exact_historical_fee_authority_gate() -> None:
    spec = _spec()
    assert spec["fee_authority"]["exact_historical_rows"] == 0
    assert spec["fee_authority"]["phase1_status"] == (
        "BLOCKED — HISTORICAL FEE AUTHORITY INCOMPLETE"
    )
    assert spec["classification"] == "BLOCKED — HISTORICAL FEE AUTHORITY INCOMPLETE"
    assert spec["execution_price_authority"]["no_execution"].endswith(
        "and labels DERIVED_COMPLEMENT"
    )
    assert spec["quote_depth_slippage_authority"]["one_contract_displayed_size"] == "NOT PROVEN"
