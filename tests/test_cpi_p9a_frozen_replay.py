import json
import shutil
from pathlib import Path

import pytest

from services.historical_replay.cpi_price_evidence import (
    strict_json_loads,
    validate_frozen_cohort,
)

ROOT = Path("evidence/cpi_p9a_historical_price")


def _copy(tmp_path: Path) -> Path:
    target = tmp_path / "cohort"
    target.mkdir(parents=True)
    shutil.copy2(ROOT / "manifest.json", target / "manifest.json")
    (target / "market_inventory.json").symlink_to((ROOT / "market_inventory.json").resolve())
    raw_target = target / "raw"
    raw_target.mkdir()
    for source in (ROOT / "raw").glob("*.json"):
        (raw_target / source.name).symlink_to(source.resolve())
    return target


def _rewrite(path: Path, mutate) -> None:
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("final_manifest_sha256")
    mutate(manifest)
    from services.historical_replay.archive import stable_hash

    manifest["final_manifest_sha256"] = stable_hash(manifest)
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")


def test_replays_exact_frozen_cohort_offline() -> None:
    assert validate_frozen_cohort(ROOT) == {
        "events": 60,
        "siblings": 474,
        "both_usable": 267,
        "fresh": 148,
    }


def test_mutated_raw_artifact_fails(tmp_path: Path) -> None:
    target = _copy(tmp_path)
    raw = next((target / "raw").glob("*.json"))
    original = raw.read_bytes()
    raw.unlink()
    raw.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_frozen_cohort(target)


def test_deleted_artifact_fails_completeness(tmp_path: Path) -> None:
    target = _copy(tmp_path)
    next((target / "raw").glob("*.json")).unlink()
    with pytest.raises(ValueError, match="missing raw artifact"):
        validate_frozen_cohort(target)


def test_inventory_mutation_and_deletion_fail(tmp_path: Path) -> None:
    target = _copy(tmp_path)
    inventory = target / "market_inventory.json"
    original = inventory.read_bytes()
    inventory.unlink()
    inventory.write_bytes(original.replace(b'"KXCPI-25JUL"', b'"KXCPI-25XXX"', 1))
    with pytest.raises(ValueError, match="market inventory hash"):
        validate_frozen_cohort(target)
    target = _copy(tmp_path / "deleted")
    (target / "market_inventory.json").unlink()
    with pytest.raises(ValueError, match="missing frozen market inventory"):
        validate_frozen_cohort(target)


def test_inventory_field_and_semantics_mutations_fail(tmp_path: Path) -> None:
    for field, value, message in (
        ("market_close", "2000-01-01T00:00:00Z", "close timestamp"),
        ("threshold", "999", "canonical contract semantics"),
        ("comparator", "GTE", "canonical contract semantics"),
    ):
        target = _copy(tmp_path / field)
        _rewrite(
            target,
            lambda manifest, field=field, value=value: manifest["markets"][0].__setitem__(
                field, value
            ),
        )
        with pytest.raises(ValueError, match=message):
            validate_frozen_cohort(target)


def test_duplicate_ticker_fails(tmp_path: Path) -> None:
    target = _copy(tmp_path)
    _rewrite(
        target,
        lambda manifest: manifest["markets"].__setitem__(1, dict(manifest["markets"][0])),
    )
    with pytest.raises(ValueError, match="duplicated"):
        validate_frozen_cohort(target)


def test_wrong_event_binding_fails(tmp_path: Path) -> None:
    target = _copy(tmp_path)
    _rewrite(
        target,
        lambda manifest: manifest["markets"][0].__setitem__("underlying_event_id", "kalshi:WRONG"),
    )
    with pytest.raises(ValueError, match="wrong underlying"):
        validate_frozen_cohort(target)


def test_postclose_selected_candle_fails(tmp_path: Path) -> None:
    target = _copy(tmp_path)
    _rewrite(
        target,
        lambda manifest: manifest["markets"][0].update({"candle_end_period_ts": 9_999_999_999}),
    )
    with pytest.raises(ValueError, match="strictly before"):
        validate_frozen_cohort(target)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("yes_ask", "0.99", "derived field mismatch"),
        ("yes_bid", "0.01", "derived field mismatch"),
        ("no_bid", "0.99", "derived field mismatch"),
        ("quote_age_seconds", 1, "derived field mismatch"),
        ("staleness_state", "FRESH", "derived field mismatch"),
        ("candle_volume", "999", "derived field mismatch"),
        ("selected_candle_hash", "bad", "latest admissible"),
        ("candle_end_period_ts", 1, "latest admissible"),
    ],
)
def test_manifest_derived_field_mutations_fail(
    tmp_path: Path, field: str, value: object, match: str
) -> None:
    target = _copy(tmp_path / field)
    index = 1 if field == "staleness_state" else 0
    _rewrite(target, lambda manifest: manifest["markets"][index].__setitem__(field, value))
    with pytest.raises(ValueError, match=match):
        validate_frozen_cohort(target)


def test_strict_json_rejects_duplicates_and_nonfinite_numbers() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        strict_json_loads(b'{"x":1,"x":2}')
    with pytest.raises(ValueError, match="non-standard"):
        strict_json_loads(b'{"x":NaN}')
    with pytest.raises(ValueError, match="non-standard"):
        strict_json_loads(b'{"x":Infinity}')


def test_retrospective_volume_cannot_become_pit_feature(tmp_path: Path) -> None:
    target = _copy(tmp_path)
    _rewrite(
        target,
        lambda manifest: manifest["markets"][0].__setitem__("point_in_time_feature_eligible", True),
    )
    with pytest.raises(ValueError, match="PIT-eligible"):
        validate_frozen_cohort(target)
