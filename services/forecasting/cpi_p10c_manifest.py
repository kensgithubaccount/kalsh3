"""CPI-E1-P10C Phase 1 durable acquisition-manifest freeze.

This module is a reader of the immutable, already-reviewed P10A binding
output. It has no network, account, execution, fee, or production influence
capability, and it does not acquire, score, or evaluate any Reuters or other
predictor evidence.

Cutoff policy (adopted, final for this checkpoint): decision-cutoff authority
for this cohort is per sibling market, not per event. Each accepted sibling
row carries its own `sibling_cutoff`, taken verbatim from canonical P10A's
`EventRow.cutoff_at` (itself `_time(p9a["market_close"])`). No event-level
cutoff is computed, inferred, or stored anywhere in this manifest.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from services.forecasting.cpi_p10a_binding import P9A_ROOT, _event_month, _time, build_binding

CANONICAL_MAIN_SHA = "5e2d88fb08dd49aa916bf4152f5c599b26ba81b4"
CANONICAL_MAIN_TREE = "724376b760ffad81e77ddbef75700c176688c785"
P10A_AUTHORITY_BRANCH_HEAD_SHA = "2d8d485b5b4fd533331a489d4a2b91248157e632"
P10A_BINDER_BLOB_SHA = "f790657cf5f8fe4a627335839e47b6dfb090eefe"
FROZEN_ACCEPTED_THRESHOLD_IDENTITY_DIGEST = (
    "11bc2723d0b75d0ab059f5c677ef061456f54d264592016b9e67402adffedec9"
)
FROZEN_MANIFEST_DIGEST_SHA256 = "ff1e54a47cddac3a44e987ea3e099e8a8a339f8d2749c25ddbd961f0c4a6b1be"


class CPIP10CManifestError(ValueError):
    """Raised when the recovered P10A cohort no longer matches frozen identity."""


def _accepted_threshold_digest(identities: list[dict[str, Any]]) -> str:
    sorted_identity_tuples = sorted(
        (
            item["event_ticker"],
            item["market_ticker"],
            item["threshold"],
            item["comparator"],
            item["predicate_identity"],
        )
        for item in identities
    )
    return hashlib.sha256(
        json.dumps(sorted_identity_tuples, separators=(",", ":")).encode()
    ).hexdigest()


def build_phase1_manifest(repository_root: str | Path) -> dict[str, Any]:
    root = Path(repository_root)
    report = build_binding(root)

    if report["bound_events"] != 42 or report["usable_events"] != 42:
        raise CPIP10CManifestError(
            f"P10A accepted event count is not 42 (bound={report['bound_events']}, "
            f"usable={report['usable_events']})"
        )

    identities = report["accepted_threshold_identity"]
    if len(identities) != 341:
        raise CPIP10CManifestError(
            f"P10A accepted sibling row count is not 341 (got {len(identities)})"
        )

    threshold_digest = _accepted_threshold_digest(identities)
    if threshold_digest != FROZEN_ACCEPTED_THRESHOLD_IDENTITY_DIGEST:
        raise CPIP10CManifestError(
            f"accepted-threshold identity digest mismatch: got {threshold_digest}"
        )

    manifest_json = json.loads((root / P9A_ROOT / "manifest.json").read_bytes())
    p9a_by_ticker = {row["market_ticker"]: row for row in manifest_json["markets"]}

    by_event: dict[str, dict[str, Any]] = {}
    for item in identities:
        event_ticker = item["event_ticker"]
        market_ticker = item["market_ticker"]
        p9a = p9a_by_ticker[market_ticker]
        cutoff = _time(p9a["market_close"])
        sibling = {
            "market_ticker": market_ticker,
            "threshold": item["threshold"],
            "comparator": item["comparator"],
            "predicate_identity": item["predicate_identity"],
            "sibling_cutoff": cutoff.isoformat(),
            "p9a_evidence_id": item["p9a_evidence_id"],
            "request_identity": item["request_identity"],
        }
        bucket = by_event.setdefault(event_ticker, {"reference_month": set(), "siblings": []})
        bucket["reference_month"].add(_event_month(event_ticker))
        bucket["siblings"].append(sibling)

    if len(by_event) != 42:
        raise CPIP10CManifestError(f"distinct accepted event count is not 42 (got {len(by_event)})")

    events = []
    for event_ticker in sorted(by_event):
        bucket = by_event[event_ticker]
        reference_months = sorted(bucket["reference_month"])
        if len(reference_months) != 1:
            raise CPIP10CManifestError(f"{event_ticker} has ambiguous reference_month")
        year, month = reference_months[0]
        siblings = sorted(bucket["siblings"], key=lambda s: s["market_ticker"])
        events.append(
            {
                "event_ticker": event_ticker,
                "reference_month": f"{year:04d}-{month:02d}",
                "accepted_sibling_count": len(siblings),
                "accepted_siblings": siblings,
            }
        )

    manifest: dict[str, Any] = {
        "schema": "cpi-e1-p10c-phase1b-per-sibling-manifest/v1",
        "phase": "CPI-E1-P10C Phase 1B - per-sibling cutoff manifest freeze",
        "cutoff_semantics": "per_sibling_market",
        "canonical_main_sha": CANONICAL_MAIN_SHA,
        "canonical_main_tree": CANONICAL_MAIN_TREE,
        "p10a_authority_branch_head_sha": P10A_AUTHORITY_BRANCH_HEAD_SHA,
        "p10a_authority_merged_into_main": True,
        "p10a_binder_module": "services/forecasting/cpi_p10a_binding.py",
        "p10a_binder_blob_sha": P10A_BINDER_BLOB_SHA,
        "frozen_accepted_threshold_identity_digest": threshold_digest,
        "accepted_event_count": 42,
        "accepted_sibling_row_count": 341,
        "reuters_acquisition_performed": False,
        "kalshi_scoring_performed": False,
        "edge_pnl_fees_computed": False,
        "outcome_blind": True,
        "events": events,
    }
    canonical_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest["manifest_digest_sha256"] = hashlib.sha256(canonical_bytes).hexdigest()
    return manifest


def canonical_bytes(manifest: dict[str, Any]) -> bytes:
    without_digest = {k: v for k, v in manifest.items() if k != "manifest_digest_sha256"}
    return json.dumps(without_digest, sort_keys=True, separators=(",", ":")).encode()
