import json
import shutil
from pathlib import Path

import pytest

from scripts.validate_cpi_p9b_fee_authority import load, validate

PACKAGE = Path("evidence/cpi_p9b_fee_authority")


def clone(tmp_path: Path) -> Path:
    target = tmp_path / "authority"
    shutil.copytree(PACKAGE, target)
    return target


def rewrite(target: Path, change) -> None:
    path = target / "manifest.json"
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def test_freeze_derives_expected_disposition() -> None:
    result = validate(PACKAGE)
    assert result["counts"] == {
        "exact": 272,
        "locator_only": 110,
        "unknown": 92,
        "mixed_authority": 0,
    }
    assert result["event_counts"] == {
        "exact": 31,
        "locator_only": 14,
        "unknown": 15,
        "mixed_authority": 0,
    }
    assert result["intersection_count"] == 28


def test_artifact_mutation_fails(tmp_path: Path) -> None:
    target = clone(tmp_path)
    raw = target / "raw/cftc-49335-final-schedule.pdf"
    raw.write_bytes(raw.read_bytes() + b"mutation")
    with pytest.raises(ValueError, match=r"approved set|hash mismatch"):
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
def test_formula_rounding_role_and_scope_substitutions_fail(tmp_path: Path, change) -> None:
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
        timeline.read_text().replace('"status": "exact"', '"status": "locator_only"', 1)
    )
    with pytest.raises(ValueError, match="timeline"):
        validate(target)
    target = clone(tmp_path / "coverage")
    coverage = target / "event_coverage.json"
    coverage.write_text(coverage.read_text().replace('"exact": 272', '"exact": 271', 1))
    with pytest.raises(ValueError, match="coverage"):
        validate(target)


def test_p8_event_list_is_not_a_mutable_authority_source(tmp_path: Path) -> None:
    target = clone(tmp_path)
    rewrite(target, lambda m: m["p8_reference_events"].pop())
    with pytest.raises(ValueError, match="P8 event list"):
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
