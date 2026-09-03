from decimal import Decimal
from pathlib import Path

from scripts.validate_cpi_p10b_reuters_authority import (
    check_coverage,
    check_manifest_hashes,
    check_receipt,
    load_json,
    main,
)

BUNDLE = Path("docs/reviews/artifacts/cpi-p10b-reuters")


def test_committed_manifest_hashes_match_disk() -> None:
    manifest = load_json(BUNDLE / "manifest.json")
    findings = check_manifest_hashes(manifest)
    assert findings
    assert all(f.ok for f in findings)


def test_coverage_arithmetic_and_gate() -> None:
    coverage = load_json(BUNDLE / "coverage.json")
    findings = check_coverage(coverage)
    assert all(f.ok for f in findings)
    arith = coverage["coverage_arithmetic"]
    assert arith["total_pass"] == 3
    assert arith["newly_tested_pass"] == 2
    assert arith["gate_result"] == "SATISFIED"


def test_cleveland_and_reuters_claims_stay_separate() -> None:
    coverage = load_json(BUNDLE / "coverage.json")
    cleveland = coverage["cleveland_claims_kept_separate"]
    assert cleveland["KXCPI-26JAN"]["vintage_status"] == "PASS"
    assert cleveland["KXCPI-26JAN"]["value"] == "0.13"
    assert cleveland["KXCPI-25JUL"]["vintage_status"] == "UNKNOWN"
    reuters_row = next(row for row in coverage["sample"] if row["event_ticker"] == "KXCPI-25JUL")
    assert reuters_row["vintage_status"] == "PASS"
    assert reuters_row["reuters_value"] == "0.2"


def test_each_pass_receipt_precedes_its_cutoff() -> None:
    for event_ticker in ("KXCPI-25JUL", "KXCPI-25DEC", "KXCPI-26JAN"):
        receipt = load_json(BUNDLE / event_ticker / "receipt.json")
        findings = check_receipt(event_ticker, receipt)
        assert all(f.ok for f in findings), [f.message for f in findings if not f.ok]


def test_exact_decimal_precision_no_extra_digits() -> None:
    values = {
        "KXCPI-25JUL": "0.2",
        "KXCPI-25DEC": "0.3",
        "KXCPI-26JAN": "0.3",
    }
    for event_ticker, expected in values.items():
        receipt = load_json(BUNDLE / event_ticker / "receipt.json")
        assert receipt["value"] == expected
        assert Decimal(receipt["value"]) == Decimal(expected)


def test_unresolved_events_remain_unknown_not_silently_upgraded() -> None:
    coverage = load_json(BUNDLE / "coverage.json")
    for ticker in ("CPI-21SEP", "CPI-23JUN"):
        row = next(r for r in coverage["sample"] if r["event_ticker"] == ticker)
        assert row["vintage_status"] == "UNKNOWN"
        assert row["reuters_value"] is None


def test_validator_entrypoint_exits_zero() -> None:
    assert main() == 0
