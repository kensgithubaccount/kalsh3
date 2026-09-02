import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scripts.validate_cpi_p9b_fee_authority import load, validate
from services.forecasting.cpi_p9b_authority import (
    FeeAuthorityUnavailable,
    consume_taker_fee_authority,
)

PACKAGE = Path("evidence/cpi_p9b_fee_authority")


def clone(tmp_path: Path) -> Path:
    target = tmp_path / "authority"
    shutil.copytree(PACKAGE, target)
    return target


def rewrite(target: Path, change: Callable[[dict[str, Any]], Any]) -> None:
    path = target / "manifest.json"
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def test_freeze_derives_expected_disposition() -> None:
    result = validate(PACKAGE)
    assert result["counts"] == {
        "exact": 0,
        "continuity_supported": 0,
        "interval_unproven_between_matching_endpoints": 272,
        "locator_only": 110,
        "unknown": 92,
        "mixed_authority": 0,
    }
    assert result["event_counts"] == {
        "exact": 0,
        "continuity_supported": 0,
        "interval_unproven_between_matching_endpoints": 31,
        "locator_only": 14,
        "unknown": 15,
        "mixed_authority": 0,
    }
    assert result["p8_join"] == "deferred_to_downstream_p10_authority_binder"
    assert result["endpoint_bracketed_interval_rows"] == 272
    assert result["endpoint_bracketed_interval_events"] == 31


def test_artifact_mutation_fails(tmp_path: Path) -> None:
    target = clone(tmp_path)
    raw = target / "raw/cftc-49335-final-schedule.pdf"
    raw.write_bytes(raw.read_bytes() + b"mutation")
    with pytest.raises(ValueError, match=r"approved set|hash mismatch"):
        validate(target)


def test_rule_3_6_identity_and_wrong_historical_version_fail(tmp_path: Path) -> None:
    target = clone(tmp_path)
    rewrite(
        target,
        lambda m: m["authority_metadata"][3].update(
            {"identity": "CFTC-RULEBOOK-3.6-E", "rule": "3.6(e)"}
        ),
    )
    with pytest.raises(ValueError, match="metadata"):
        validate(target)


def test_inventory_interval_mutation_fails(tmp_path: Path) -> None:
    target = clone(tmp_path)
    inventory = target / "raw/cftc-kex-fee-filing-inventory.json"
    inventory.write_text(inventory.read_text().replace("2025-05-06", "2025-05-07", 1))
    with pytest.raises(ValueError, match="inventory"):
        validate(target)


def test_official_inventory_response_mutation_fails(tmp_path: Path) -> None:
    target = clone(tmp_path)
    response = target / "raw/cftc-kex-fee-index-response.html"
    response.write_text(response.read_text().replace("Show_All=1", "Show_All=0", 1))
    with pytest.raises(ValueError, match=r"approved set|hash mismatch|response"):
        validate(target)


def test_endpoint_snapshot_cannot_be_promoted_to_continuity(tmp_path: Path) -> None:
    target = clone(tmp_path)
    rewrite(
        target,
        lambda m: m["timelines"]["taker"][1].update({"status": "exact"}),
    )
    with pytest.raises(ValueError, match="timeline"):
        validate(target)


def test_rehash_without_approved_identity_fails(tmp_path: Path) -> None:
    target = clone(tmp_path)
    rewrite(
        target,
        lambda m: m["artifacts"][2].update({"sha256": "0" * 64, "authority_identity": "0" * 64}),
    )
    with pytest.raises(ValueError, match=r"approved set|hash mismatch"):
        validate(target)


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m["timelines"]["taker"][1].update({"formula": "round_up(0.035 * C * P * (1-P))"}),
        lambda m: m["timelines"]["taker"][1].update({"rounding": "nearest_cent"}),
        lambda m: m["timelines"]["taker"][1].update({"authority_type": "maker"}),
        lambda m: m["timelines"]["taker"][1].update({"kxcpi_applicability": "perpetual_futures"}),
    ],
)
def test_formula_rounding_role_and_scope_substitutions_fail(
    tmp_path: Path, change: Callable[[dict[str, Any]], Any]
) -> None:
    target = clone(tmp_path)
    rewrite(target, change)
    with pytest.raises(ValueError):
        validate(target)


def test_boundary_wrong_effective_date_creates_gap_or_changes_mapping(tmp_path: Path) -> None:
    target = clone(tmp_path)
    rewrite(target, lambda m: m["timelines"]["taker"][1].update({"start_date": "2022-09-23"}))
    with pytest.raises(ValueError, match="timeline"):
        validate(target)


def test_locator_cannot_be_presented_as_exact(tmp_path: Path) -> None:
    target = clone(tmp_path)
    rewrite(target, lambda m: m["timelines"]["taker"][2].update({"status": "exact"}))
    with pytest.raises(ValueError, match=r"timeline|formula|identity"):
        validate(target)


def test_duplicate_conflicting_regime_fails(tmp_path: Path) -> None:
    target = clone(tmp_path)
    rewrite(target, lambda m: m["timelines"]["taker"].insert(2, dict(m["timelines"]["taker"][1])))
    with pytest.raises(ValueError, match="timeline"):
        validate(target)


def test_coherent_pdf_replacement_cannot_redefine_reviewed_authority(tmp_path: Path) -> None:
    target = clone(tmp_path)
    pdf = target / "raw/cftc-49335-final-schedule.pdf"
    pdf.write_bytes(b"not the filing")
    rewrite(
        target,
        lambda m: m["artifacts"][2].update({"sha256": "0" * 64, "identity": "ATTACKER-IDENTITY"}),
    )
    with pytest.raises(ValueError, match="approved set"):
        validate(target)


def test_timeline_and_coverage_files_are_read_only_inputs(tmp_path: Path) -> None:
    target = clone(tmp_path)
    timeline = target / "authority_timeline.json"
    timeline.write_text(
        timeline.read_text().replace(
            '"status": "interval_unproven_between_matching_endpoints"',
            '"status": "locator_only"',
        )
    )
    with pytest.raises(ValueError, match="timeline"):
        validate(target)
    target = clone(tmp_path / "coverage")
    coverage = target / "event_coverage.json"
    coverage.write_text(
        coverage.read_text().replace(
            '"interval_unproven_between_matching_endpoints": 272',
            '"interval_unproven_between_matching_endpoints": 271',
            1,
        )
    )
    with pytest.raises(ValueError, match="coverage"):
        validate(target)


def test_p8_join_is_deferred_and_cannot_claim_intersection() -> None:
    result = validate(PACKAGE)
    assert result["p8_join"] == "deferred_to_downstream_p10_authority_binder"
    assert "intersection_count" not in result


def test_fee_covered_row_is_not_quote_usable() -> None:
    result = validate(PACKAGE)
    assert "exact_taker_p8_usable_quote_rows" not in result
    assert all("usable" not in row for row in result["market_rows"])
    assert all(not row["exact_fee_authority"] for row in result["market_rows"])
    assert all(not row["economics_usable"] for row in result["market_rows"])


def test_non_exact_rows_cannot_cross_fee_consumption_boundary() -> None:
    result = validate(PACKAGE)
    for row in result["market_rows"]:
        with pytest.raises(FeeAuthorityUnavailable):
            consume_taker_fee_authority(row)


def test_endpoint_formula_is_not_an_interval_regime() -> None:
    result = validate(PACKAGE)
    interval_rows = [
        row
        for row in result["market_rows"]
        if row["status"] == "interval_unproven_between_matching_endpoints"
    ]
    assert interval_rows
    assert all(row["formula"] is None and row["rounding"] is None for row in interval_rows)


def test_incorrect_2020_cfr_url_fails(tmp_path: Path) -> None:
    target = clone(tmp_path)
    rewrite(
        target,
        lambda m: m["artifacts"][8].update(
            {
                "url": "https://www.govinfo.gov/content/pkg/CFR-2020-title17-vol1/pdf/CFR-2020-title17-vol1-part40.pdf"
            }
        ),
    )
    with pytest.raises(ValueError, match="approved set"):
        validate(target)


def test_inventory_is_fixed_and_not_an_individual_filing_page(tmp_path: Path) -> None:
    target = clone(tmp_path)
    inventory = target / "raw/cftc-kex-fee-filing-inventory.json"
    inventory.write_text(inventory.read_text().replace('"filings": [', '"filings": [],'))
    with pytest.raises(ValueError, match="inventory"):
        validate(target)


def test_quote_timestamp_is_not_market_close() -> None:
    result = validate(PACKAGE)
    row = next(row for row in result["market_rows"] if row["market_ticker"] == "KXCPI-25APR-T-0.1")
    assert row["selected_quote_timestamp"] == "2025-05-13T12:00:00Z"
    assert row["status"] == "locator_only"


def test_validation_does_not_rewrite_frozen_coverage() -> None:
    path = PACKAGE / "event_coverage.json"
    before = path.read_bytes()
    validate(PACKAGE)
    assert path.read_bytes() == before


def test_json_loader_rejects_duplicate_keys_and_nonstandard_constants(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(b'{"x": 1, "x": 2}')
    with pytest.raises(ValueError, match="duplicate"):
        load(duplicate)
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_bytes(b'{"x": NaN}')
    with pytest.raises(ValueError, match="non-standard"):
        load(nonfinite)
