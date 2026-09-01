import json
import shutil
from pathlib import Path

import pytest

from services.historical_replay.cpi_price_evidence import validate_frozen_cohort

ROOT = Path("evidence/cpi_p9a_historical_price")


def _copy(tmp_path: Path) -> Path:
    target = tmp_path / "cohort"
    target.mkdir()
    shutil.copy2(ROOT / "manifest.json", target / "manifest.json")
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
