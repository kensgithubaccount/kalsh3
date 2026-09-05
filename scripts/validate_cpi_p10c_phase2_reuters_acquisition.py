#!/usr/bin/env python3
"""Read-only validator for the CPI-E1-P10C Phase 2 Reuters acquisition bundle.

Verifies, from committed artifacts only:

- every manifest-listed artifact hash matches the file on disk;
- the coverage ledger reconciles to exactly 42 terminal states, matching the
  frozen Phase 1 42-event cohort exactly;
- no new Phase 2 PASS receipt carries an event-level decision_cutoff;
- every new Phase 2 PASS receipt's sibling_temporal_eligibility has exactly
  one record per frozen accepted sibling, with exact market-ticker and
  sibling_cutoff equality against the frozen manifest, and an independently
  recomputed available_before_cutoff comparison;
- Decimal precision is exactly as published, never promoted;
- no event outside the frozen cohort appears in the ledger.

This script performs no network access, no model fit or score, and no
market comparison. It reads only files under
docs/reviews/artifacts/cpi-p10c-reuters-phase2/ and
docs/reviews/artifacts/cpi-p10c-manifest-freeze/.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "docs/reviews/artifacts/cpi-p10c-reuters-phase2"
FREEZE = ROOT / "docs/reviews/artifacts/cpi-p10c-manifest-freeze/manifest.json"
P10B_BUNDLE = ROOT / "docs/reviews/artifacts/cpi-p10b-reuters"

# New Phase 2 PASS events only. P10B's reused PASS events (KXCPI-25JUL,
# KXCPI-26JAN, KXCPI-25DEC) are separate, already-reviewed evidence from a
# merged PR and are out of scope for the per-sibling-eligibility repair.
EXPECTED_VALUE = {
    "CPI-23AUG": Decimal("0.6"),
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


def check_coverage(coverage: dict[str, Any], freeze: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    events = coverage["events"]

    freeze_tickers = {e["event_ticker"] for e in freeze["events"]}
    ledger_tickers = {e["event_ticker"] for e in events}
    findings.append(
        Finding(
            len(events) == 42 == len(ledger_tickers),
            f"coverage ledger has {len(events)} rows, {len(ledger_tickers)} unique (want 42)",
        )
    )
    findings.append(
        Finding(
            ledger_tickers == freeze_tickers,
            "coverage ledger event set matches frozen Phase 1 42-event cohort exactly",
        )
    )

    pass_rows = [e for e in events if e["terminal_state"] == "PASS"]
    unknown_rows = [e for e in events if e["terminal_state"] == "UNKNOWN"]
    failure_rows = [e for e in events if e["terminal_state"] == "ACQUISITION_FAILURE"]
    arith = coverage["coverage_arithmetic"]
    proven = arith["positively_proven_observations"]
    searched = arith["searched_no_qualifying_observation_found"]
    failed = arith["acquisition_authority_failures"]
    findings.append(
        Finding(
            len(pass_rows) == proven,
            f"positively_proven_observations recomputes to {len(pass_rows)} (declared {proven})",
        )
    )
    findings.append(
        Finding(
            len(unknown_rows) == searched,
            f"searched_no_qualifying recomputes to {len(unknown_rows)} (declared {searched})",
        )
    )
    findings.append(
        Finding(
            len(failure_rows) == failed,
            f"acquisition_authority_failures recomputes to {len(failure_rows)} (declared {failed})",
        )
    )
    total = len(pass_rows) + len(unknown_rows) + len(failure_rows)
    findings.append(
        Finding(
            total == 42 == arith["total_terminal_states"],
            f"PASS + UNKNOWN + FAILURE reconciles to {total} (must equal 42)",
        )
    )
    findings.append(
        Finding(
            "CPI-24JAN" not in {e["event_ticker"] for e in pass_rows},
            "CPI-24JAN is not a PASS row (R1 repair: reclassified to UNKNOWN)",
        )
    )

    return findings


def check_no_event_level_cutoff(event_ticker: str, receipt: dict[str, Any]) -> list[Finding]:
    return [
        Finding(
            "decision_cutoff" not in receipt,
            f"{event_ticker} receipt has no event-level decision_cutoff field",
        ),
        Finding(
            "temporal_comparison" not in receipt,
            f"{event_ticker} receipt has no event-level temporal_comparison field",
        ),
    ]


def check_sibling_eligibility(
    event_ticker: str, receipt: dict[str, Any], freeze: dict[str, Any]
) -> list[Finding]:
    findings: list[Finding] = []
    event = next(e for e in freeze["events"] if e["event_ticker"] == event_ticker)
    frozen_siblings = event["accepted_siblings"]
    frozen_tickers = [s["market_ticker"] for s in frozen_siblings]
    frozen_cutoffs = {s["market_ticker"]: s["sibling_cutoff"] for s in frozen_siblings}

    eligibility = receipt.get("sibling_temporal_eligibility", [])
    eligibility_tickers = [r["market_ticker"] for r in eligibility]

    findings.append(
        Finding(
            len(eligibility) == len(frozen_siblings),
            f"{event_ticker} has {len(eligibility)} eligibility records "
            f"(frozen manifest has {len(frozen_siblings)} accepted siblings)",
        )
    )
    findings.append(
        Finding(
            set(eligibility_tickers) == set(frozen_tickers)
            and len(eligibility_tickers) == len(frozen_tickers),
            f"{event_ticker} eligibility market_ticker set exactly equals frozen accepted siblings "
            "(no collapsing of repeated cutoffs)",
        )
    )

    published_at = _parse_ts(receipt["published_at"])
    all_cutoffs_match = True
    all_flags_correct = True
    for record in eligibility:
        ticker = record["market_ticker"]
        frozen_cutoff = frozen_cutoffs.get(ticker)
        if record.get("sibling_cutoff") != frozen_cutoff:
            all_cutoffs_match = False
        recomputed = published_at < _parse_ts(record["sibling_cutoff"])
        if record.get("available_before_cutoff") != recomputed:
            all_flags_correct = False

    findings.append(
        Finding(
            all_cutoffs_match,
            f"{event_ticker} every eligibility record's sibling_cutoff exactly equals "
            "the frozen manifest value for that market_ticker",
        )
    )
    findings.append(
        Finding(
            all_flags_correct,
            f"{event_ticker} every available_before_cutoff flag independently recomputed "
            "(published_at vs. that record's sibling_cutoff) matches the declared value",
        )
    )

    return findings


def check_receipt(
    event_ticker: str, receipt: dict[str, Any], freeze: dict[str, Any]
) -> list[Finding]:
    findings: list[Finding] = []

    value = Decimal(receipt["value"])
    expected = EXPECTED_VALUE[event_ticker]
    findings.append(
        Finding(
            value == expected and str(value) == str(expected),
            f"{event_ticker} value {value} matches expected {expected} at exact precision",
        )
    )

    findings.append(
        Finding(receipt.get("vintage_status") == "PASS", f"{event_ticker} vintage_status is PASS")
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

    findings += check_no_event_level_cutoff(event_ticker, receipt)
    findings += check_sibling_eligibility(event_ticker, receipt, freeze)

    return findings


def main() -> int:
    manifest = load_json(BUNDLE / "manifest.json")
    coverage = load_json(BUNDLE / "coverage.json")
    freeze = load_json(FREEZE)

    all_findings: list[Finding] = []
    all_findings += check_manifest_hashes(manifest)
    all_findings += check_coverage(coverage, freeze)

    for event_ticker in EXPECTED_VALUE:
        receipt = load_json(BUNDLE / event_ticker / "receipt.json")
        all_findings += check_receipt(event_ticker, receipt, freeze)

    findings_flags = [
        Finding(coverage["production_influence"] == 0, "production_influence == 0"),
        Finding(coverage["kalshi_scoring_performed"] is False, "kalshi_scoring_performed is False"),
        Finding(coverage["edge_pnl_fees_computed"] is False, "edge_pnl_fees_computed is False"),
        Finding(
            coverage["cutoff_semantics"] == "per_sibling_market",
            "cutoff_semantics is per_sibling_market",
        ),
    ]
    all_findings += findings_flags

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
