import json
import shutil
from pathlib import Path

import pytest

from scripts.validate_cpi_p9b_fee_authority import validate

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
    assert result["counts"] == {"exact": 31, "locator_only": 14, "unknown": 15}
    assert result["intersection_count"] == 28


def test_artifact_mutation_fails(tmp_path: Path) -> None:
    target = clone(tmp_path)
    raw = target / "raw/cftc-49335-final-schedule.pdf"
    raw.write_bytes(raw.read_bytes() + b"mutation")
    with pytest.raises(ValueError, match="hash mismatch"):
        validate(target)


def test_rehash_without_approved_identity_fails(tmp_path: Path) -> None:
    target = clone(tmp_path)
    rewrite(
        target,
        lambda m: m["artifacts"][2].update({"sha256": "0" * 64, "authority_identity": "0" * 64}),
    )
    with pytest.raises(ValueError, match="hash mismatch"):
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
    rewrite(target, lambda m: m["timelines"]["taker"][1].update({"start": "2022-09-23T00:00:00Z"}))
    with pytest.raises(ValueError, match="gap or overlap"):
        validate(target)


def test_locator_cannot_be_presented_as_exact(tmp_path: Path) -> None:
    target = clone(tmp_path)
    rewrite(target, lambda m: m["timelines"]["taker"][2].update({"status": "exact"}))
    with pytest.raises(ValueError, match=r"formula|identity"):
        validate(target)


def test_duplicate_conflicting_regime_fails(tmp_path: Path) -> None:
    target = clone(tmp_path)
    rewrite(target, lambda m: m["timelines"]["taker"].insert(2, dict(m["timelines"]["taker"][1])))
    with pytest.raises(ValueError, match="gap or overlap"):
        validate(target)
