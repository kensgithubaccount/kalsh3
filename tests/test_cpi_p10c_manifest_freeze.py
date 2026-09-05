import json
from pathlib import Path

from services.forecasting.cpi_p10a_binding import _time, build_binding
from services.forecasting.cpi_p10c_manifest import (
    FROZEN_ACCEPTED_THRESHOLD_IDENTITY_DIGEST,
    FROZEN_MANIFEST_DIGEST_SHA256,
    build_phase1_manifest,
    canonical_bytes,
)

ROOT = Path(__file__).resolve().parents[1]
FROZEN_MANIFEST_PATH = ROOT / "docs/reviews/artifacts/cpi-p10c-manifest-freeze/manifest.json"
RECEIPT_PATH = ROOT / "docs/reviews/artifacts/cpi-p10c-manifest-freeze/receipt.json"


def test_cohort_counts_and_threshold_identity_are_frozen() -> None:
    manifest = build_phase1_manifest(ROOT)
    assert manifest["accepted_event_count"] == 42
    assert manifest["accepted_sibling_row_count"] == 341
    assert len(manifest["events"]) == 42
    assert sum(e["accepted_sibling_count"] for e in manifest["events"]) == 341
    assert (
        manifest["frozen_accepted_threshold_identity_digest"]
        == FROZEN_ACCEPTED_THRESHOLD_IDENTITY_DIGEST
    )


def test_no_event_level_cutoff_is_present() -> None:
    manifest = build_phase1_manifest(ROOT)
    assert manifest["cutoff_semantics"] == "per_sibling_market"
    for event in manifest["events"]:
        assert set(event) == {
            "event_ticker",
            "reference_month",
            "accepted_sibling_count",
            "accepted_siblings",
        }
        assert "decision_cutoff" not in event
        for sibling in event["accepted_siblings"]:
            assert "sibling_cutoff" in sibling
            assert "decision_cutoff" not in sibling


def test_every_sibling_cutoff_matches_canonical_p10a_authority() -> None:
    manifest = build_phase1_manifest(ROOT)
    report = build_binding(ROOT)
    p9a_by_ticker = {item["market_ticker"]: item for item in report["accepted_threshold_identity"]}

    manifest_json = json.loads(
        (ROOT / "evidence/cpi_p9a_historical_price/manifest.json").read_bytes()
    )
    close_by_ticker = {
        row["market_ticker"]: row["market_close"] for row in manifest_json["markets"]
    }

    seen = 0
    for event in manifest["events"]:
        for sibling in event["accepted_siblings"]:
            market_ticker = sibling["market_ticker"]
            assert market_ticker in p9a_by_ticker
            expected_cutoff = _time(close_by_ticker[market_ticker]).isoformat()
            assert sibling["sibling_cutoff"] == expected_cutoff
            seen += 1
    assert seen == 341


def test_deterministic_regeneration_is_byte_identical() -> None:
    first = build_phase1_manifest(ROOT)
    second = build_phase1_manifest(ROOT)
    assert canonical_bytes(first) == canonical_bytes(second)
    assert first["manifest_digest_sha256"] == second["manifest_digest_sha256"]


def test_manifest_matches_committed_frozen_evidence() -> None:
    regenerated = build_phase1_manifest(ROOT)
    frozen_on_disk = json.loads(FROZEN_MANIFEST_PATH.read_bytes())
    assert regenerated == frozen_on_disk
    assert regenerated["manifest_digest_sha256"] == FROZEN_MANIFEST_DIGEST_SHA256


def test_receipt_matches_manifest() -> None:
    manifest = build_phase1_manifest(ROOT)
    receipt = json.loads(RECEIPT_PATH.read_bytes())
    assert receipt["manifest_digest_sha256"] == manifest["manifest_digest_sha256"]
    assert (
        receipt["accepted_threshold_identity_digest"]
        == (manifest["frozen_accepted_threshold_identity_digest"])
    )
    assert receipt["accepted_event_count"] == manifest["accepted_event_count"]
    assert receipt["accepted_sibling_row_count"] == manifest["accepted_sibling_row_count"]
    assert receipt["cutoff_semantics"] == manifest["cutoff_semantics"]
    assert receipt["event_level_decision_cutoff_present"] is False
    assert receipt["reuters_acquisition_performed"] is False
    assert receipt["kalshi_scoring_performed"] is False
    assert receipt["edge_pnl_fees_computed"] is False
    assert receipt["production_influence"] == "0"


def test_receipt_distinguishes_durable_freeze_phase_from_manifest_phase() -> None:
    manifest = build_phase1_manifest(ROOT)
    receipt = json.loads(RECEIPT_PATH.read_bytes())
    assert receipt["phase"] == "CPI-E1-P10C Phase 1C - durable manifest freeze"
    assert receipt["manifest_phase"] == manifest["phase"]
    assert receipt["manifest_phase"] == "CPI-E1-P10C Phase 1B - per-sibling cutoff manifest freeze"


def test_receipt_does_not_mislabel_git_blob_sha_as_sha256() -> None:
    receipt = json.loads(RECEIPT_PATH.read_bytes())
    assert "p10a_binder_blob_sha256" not in receipt
    assert receipt["p10a_binder_git_blob_sha"] == "f790657cf5f8fe4a627335839e47b6dfb090eefe"
    assert len(receipt["p10a_binder_git_blob_sha"]) == 40
