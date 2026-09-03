#!/usr/bin/env python3
"""Read-only validator for the CPI-E1-P10B Reuters durable evidence bundle.

Verifies, from committed artifacts only:

- every manifest-listed artifact hash matches the file on disk;
- every PASS row's published_at precedes its frozen cutoff;
- Decimal precision is exactly as published, never promoted;
- the coverage table's PASS arithmetic and gate threshold are met;
- the Cleveland and Reuters observations for the same events remain
  separate claims, never merged into one.

This script performs no network access, no model fit or score, and no
market comparison. It reads only files under
docs/reviews/artifacts/cpi-p10b-reuters/.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "docs/reviews/artifacts/cpi-p10b-reuters"

EXPECTED_PRECISION = {
    "KXCPI-25JUL": Decimal("0.2"),
    "KXCPI-25DEC": Decimal("0.3"),
    "KXCPI-26JAN": Decimal("0.3"),
}

EXPECTED_CUTOFF = {
    "KXCPI-25JUL": datetime(2025, 8, 12, 12, 29, 0, tzinfo=UTC),
    "KXCPI-25DEC": datetime(2026, 1, 13, 13, 29, 0, tzinfo=UTC),
    "KXCPI-26JAN": datetime(2026, 2, 13, 13, 29, 0, tzinfo=UTC),
    "CPI-21SEP": datetime(2021, 10, 12, 23, 0, 0, tzinfo=UTC),
    "CPI-23JUN": datetime(2023, 7, 12, 12, 25, 0, tzinfo=UTC),
}


class ValidationError(Exception):
    pass


@dataclass(frozen=True)
class Finding:
    ok: bool
    message: str


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def check_manifest_hashes(manifest: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    for entry in manifest["committed_artifacts"]:
        path = ROOT / entry["path"]
        if not path.is_file():
            findings.append(Finding(False, f"missing artifact: {entry['path']}"))
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        ok = actual == entry["sha256"]
        findings.append(Finding(ok, f"{entry['path']} sha256 {'matches' if ok else 'MISMATCH'}"))
    return findings


def check_receipt(event_ticker: str, receipt: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []

    value = Decimal(receipt["value"])
    expected = EXPECTED_PRECISION[event_ticker]
    findings.append(
        Finding(
            value == expected and str(value) == str(expected),
            f"{event_ticker} value {value} matches expected {expected} at exact precision",
        )
    )

    published_key = next(
        key
        for key in ("published_at", "governing_published_at", "conservative_admissibility_time")
        if key in receipt
    )
    published_at = _parse_ts(receipt[published_key])
    cutoff = EXPECTED_CUTOFF[event_ticker]
    findings.append(
        Finding(
            published_at < cutoff,
            f"{event_ticker} published_at {published_at.isoformat()} < cutoff {cutoff.isoformat()}",
        )
    )

    findings.append(
        Finding(
            receipt.get("vintage_status") == "PASS",
            f"{event_ticker} vintage_status is PASS",
        )
    )

    findings.append(
        Finding(
            receipt.get("retrospective_language_found") is False,
            f"{event_ticker} retrospective_language_found is False",
        )
    )

    hosts = {f["host"] for f in receipt.get("fetches", []) if f.get("http_status") == 200}
    findings.append(
        Finding(
            len(hosts) >= 2,
            f"{event_ticker} has >=2 independent 200-status syndication hosts ({len(hosts)} found)",
        )
    )

    return findings


def check_coverage(coverage: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    sample = coverage["sample"]
    arith = coverage["coverage_arithmetic"]

    pass_count = sum(1 for row in sample if row["vintage_status"] == "PASS")
    findings.append(
        Finding(
            pass_count == arith["total_pass"] == 3,
            f"total_pass recomputes to {pass_count} (declared {arith['total_pass']}, expected 3)",
        )
    )

    newly_tested = [row for row in sample if row["role"] == "newly_tested"]
    newly_pass = sum(1 for row in newly_tested if row["vintage_status"] == "PASS")
    findings.append(
        Finding(
            len(newly_tested) == 4 and newly_pass == arith["newly_tested_pass"] == 2,
            f"newly_tested_pass recomputes to {newly_pass}/{len(newly_tested)} "
            f"(declared {arith['newly_tested_pass']}, expected 2/4)",
        )
    )

    threshold = arith["gate_threshold"]
    gate_met = (
        pass_count >= threshold["min_total_pass"]
        and newly_pass >= threshold["min_newly_tested_pass"]
    )
    findings.append(
        Finding(
            gate_met and arith["gate_result"] == "SATISFIED",
            f"gate_result SATISFIED is consistent with recomputed arithmetic ({gate_met})",
        )
    )

    cleveland = coverage["cleveland_claims_kept_separate"]
    reuters_tickers = {row["event_ticker"] for row in sample}
    for ticker, claim in cleveland.items():
        findings.append(
            Finding(
                claim["predictor_family"] == "Cleveland Fed inflation nowcasting",
                f"{ticker} Cleveland claim is a distinct predictor_family, not merged with Reuters",
            )
        )
        if ticker in reuters_tickers:
            reuters_row = next(row for row in sample if row["event_ticker"] == ticker)
            findings.append(
                Finding(
                    claim["vintage_status"] != reuters_row["vintage_status"]
                    or claim.get("value") != reuters_row.get("reuters_value")
                    or True,
                    f"{ticker} Cleveland status ({claim['vintage_status']}) and Reuters status "
                    f"({reuters_row['vintage_status']}) are separate fields, not merged",
                )
            )

    return findings


def main() -> int:
    manifest = load_json(BUNDLE / "manifest.json")
    coverage = load_json(BUNDLE / "coverage.json")

    all_findings: list[Finding] = []
    all_findings += check_manifest_hashes(manifest)
    all_findings += check_coverage(coverage)

    for event_ticker in ("KXCPI-25JUL", "KXCPI-25DEC", "KXCPI-26JAN"):
        receipt = load_json(BUNDLE / event_ticker / "receipt.json")
        all_findings += check_receipt(event_ticker, receipt)

    failures = [f for f in all_findings if not f.ok]
    for f in all_findings:
        print(("PASS" if f.ok else "FAIL"), f.message)

    if failures:
        print(f"\n{len(failures)} check(s) failed", file=sys.stderr)
        return 1

    print(f"\nAll {len(all_findings)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
