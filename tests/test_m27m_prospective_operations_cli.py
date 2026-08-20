from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from argparse import Namespace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import scripts.register_m27m_prospective_capture as register_cli
import scripts.verify_m27m_prospective_collection as verify_cli
from services.forecasting.domain import ForecastError
from services.forecasting.weather_prospective_operations import (
    archive_layout,
    parse_and_validate_bundle,
    register_prospective_capture,
    verify_archive,
)
from tests.test_m27m_prospective_operations import (
    _SEPT1_CYCLE_KEY,
    _SEPT1_REFERENCE,
    _bundle_bytes,
)

_AS_OF = _SEPT1_REFERENCE + timedelta(hours=1)


def _write_bundle_file(tmp_path: Path, **kwargs: object) -> Path:
    path = tmp_path / "bundle.json"
    path.write_bytes(_bundle_bytes(**kwargs))  # type: ignore[arg-type]
    return path


# ---------------------------------------------------------------------------
# register CLI
# ---------------------------------------------------------------------------


def test_register_cli_function_archives_and_returns_summary(tmp_path: Path) -> None:
    bundle_path = _write_bundle_file(tmp_path)
    args = Namespace(bundle=bundle_path, archive_root=tmp_path / "archive")
    payload = register_cli.register(args)
    assert payload["cycle_key"] == _SEPT1_CYCLE_KEY
    assert payload["bundle_created"] is True
    layout = archive_layout(tmp_path / "archive")
    assert (layout.cycles_dir / f"{_SEPT1_CYCLE_KEY}.json").exists()


def test_register_cli_main_succeeds_and_prints_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle_path = _write_bundle_file(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "register_m27m_prospective_capture.py",
            "--bundle",
            str(bundle_path),
            "--archive-root",
            str(tmp_path / "archive"),
        ],
    )
    register_cli.main()
    out = json.loads(capsys.readouterr().out)
    assert out["cycle_key"] == _SEPT1_CYCLE_KEY


def test_register_cli_main_exits_nonzero_on_invalid_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle_path = tmp_path / "bad.json"
    bundle_path.write_text("{not valid json")
    monkeypatch.setattr(
        "sys.argv",
        [
            "register_m27m_prospective_capture.py",
            "--bundle",
            str(bundle_path),
            "--archive-root",
            str(tmp_path / "archive"),
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        register_cli.main()
    assert exc_info.value.code == 1
    err = json.loads(capsys.readouterr().err)
    assert "error" in err


def test_register_cli_main_idempotent_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle_path = _write_bundle_file(tmp_path)
    argv = [
        "register_m27m_prospective_capture.py",
        "--bundle",
        str(bundle_path),
        "--archive-root",
        str(tmp_path / "archive"),
    ]
    monkeypatch.setattr("sys.argv", argv)
    register_cli.main()
    capsys.readouterr()
    register_cli.main()
    second = json.loads(capsys.readouterr().out)
    assert second["cycle_created"] is False


def test_register_cli_main_exits_nonzero_on_conflicting_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first_bundle = _write_bundle_file(tmp_path, values=("302", "307", "309.3"))
    archive_root = tmp_path / "archive"
    monkeypatch.setattr(
        "sys.argv",
        [
            "register_m27m_prospective_capture.py",
            "--bundle",
            str(first_bundle),
            "--archive-root",
            str(archive_root),
        ],
    )
    register_cli.main()
    capsys.readouterr()

    second_bundle = tmp_path / "second.json"
    second_bundle.write_bytes(_bundle_bytes(values=("303", "307", "309.3")))
    monkeypatch.setattr(
        "sys.argv",
        [
            "register_m27m_prospective_capture.py",
            "--bundle",
            str(second_bundle),
            "--archive-root",
            str(archive_root),
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        register_cli.main()
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# verify CLI
# ---------------------------------------------------------------------------


def test_verify_cli_main_exits_zero_when_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    archive_root = tmp_path / "archive"
    register_prospective_capture(_bundle_bytes(), archive_root)
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_m27m_prospective_collection.py",
            "--archive-root",
            str(archive_root),
            "--as-of",
            _AS_OF.isoformat(),
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        verify_cli.main()
    assert exc_info.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert len(report["accepted_cycles"]) == 1


def test_verify_cli_main_exits_nonzero_on_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    archive_root = tmp_path / "archive"
    result = register_prospective_capture(_bundle_bytes(), archive_root)
    layout = archive_layout(archive_root)
    receipt_path = layout.receipts_dir / f"{result.receipt_id}.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["target_dates"] = ["1999-01-01", "1999-01-02", "1999-01-03"]
    receipt_path.write_text(json.dumps(receipt))

    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_m27m_prospective_collection.py",
            "--archive-root",
            str(archive_root),
            "--as-of",
            _AS_OF.isoformat(),
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        verify_cli.main()
    assert exc_info.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["accepted_cycles"] == []


def test_verify_cli_default_as_of_is_current_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    archive_root = tmp_path / "archive"
    monkeypatch.setattr(
        "sys.argv", ["verify_m27m_prospective_collection.py", "--archive-root", str(archive_root)]
    )
    with pytest.raises(SystemExit):
        verify_cli.main()
    report = json.loads(capsys.readouterr().out)
    reported_as_of = datetime.fromisoformat(report["as_of"])
    assert abs((reported_as_of - datetime.now(UTC)).total_seconds()) < 60


def test_verify_cli_performs_no_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive_root = tmp_path / "archive"
    register_prospective_capture(_bundle_bytes(), archive_root)
    layout = archive_layout(archive_root)
    before = {
        path: path.stat().st_mtime_ns
        for path in (
            *layout.bundles_dir.iterdir(),
            *layout.receipts_dir.iterdir(),
            *layout.cycles_dir.iterdir(),
        )
    }
    verify_archive(archive_root, as_of=_AS_OF)
    after = {
        path: path.stat().st_mtime_ns
        for path in (
            *layout.bundles_dir.iterdir(),
            *layout.receipts_dir.iterdir(),
            *layout.cycles_dir.iterdir(),
        )
    }
    assert before == after


# ---------------------------------------------------------------------------
# Adversarial verify_archive scenarios: symlink substitution
# ---------------------------------------------------------------------------


def test_verify_detects_symlink_substitution_for_bundle(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    result = register_prospective_capture(_bundle_bytes(), archive_root)
    layout = archive_layout(archive_root)
    bundle_path = layout.bundles_dir / f"{result.bundle_sha256}.json"
    real_content = bundle_path.read_bytes()
    bundle_path.unlink()
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_bytes(real_content)
    bundle_path.symlink_to(elsewhere)

    report = verify_archive(archive_root, as_of=_AS_OF)
    assert report.ok is False
    assert any(p.category == "symlink" for p in report.problems)
    assert report.accepted_cycles == ()


def test_verify_detects_symlink_substitution_for_receipt(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    result = register_prospective_capture(_bundle_bytes(), archive_root)
    layout = archive_layout(archive_root)
    receipt_path = layout.receipts_dir / f"{result.receipt_id}.json"
    real_content = receipt_path.read_bytes()
    receipt_path.unlink()
    elsewhere = tmp_path / "elsewhere-receipt.json"
    elsewhere.write_bytes(real_content)
    receipt_path.symlink_to(elsewhere)

    report = verify_archive(archive_root, as_of=_AS_OF)
    assert report.ok is False
    assert any(p.category == "symlink" for p in report.problems)


def test_verify_detects_symlink_substitution_for_cycle(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    result = register_prospective_capture(_bundle_bytes(), archive_root)
    layout = archive_layout(archive_root)
    cycle_path = layout.cycles_dir / f"{result.cycle_key}.json"
    real_content = cycle_path.read_bytes()
    cycle_path.unlink()
    elsewhere = tmp_path / "elsewhere-cycle.json"
    elsewhere.write_bytes(real_content)
    cycle_path.symlink_to(elsewhere)

    report = verify_archive(archive_root, as_of=_AS_OF)
    assert report.ok is False
    assert any(p.category == "symlink" for p in report.problems)
    assert report.accepted_cycles == ()


# ---------------------------------------------------------------------------
# Adversarial verify_archive scenarios: tampering after the fact
# ---------------------------------------------------------------------------


def test_verify_detects_tampered_bundle_content(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    result = register_prospective_capture(_bundle_bytes(), archive_root)
    layout = archive_layout(archive_root)
    bundle_path = layout.bundles_dir / f"{result.bundle_sha256}.json"
    bundle_path.write_bytes(bundle_path.read_bytes().replace(b"302", b"999"))

    report = verify_archive(archive_root, as_of=_AS_OF)
    assert report.ok is False
    assert any(p.category == "hash-mismatch" for p in report.problems)
    assert report.accepted_cycles == ()


def test_verify_detects_tampered_receipt_content(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    result = register_prospective_capture(_bundle_bytes(), archive_root)
    layout = archive_layout(archive_root)
    receipt_path = layout.receipts_dir / f"{result.receipt_id}.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["observations"][0]["central_kelvin"] = "999"
    receipt_path.write_text(json.dumps(receipt))

    report = verify_archive(archive_root, as_of=_AS_OF)
    assert report.ok is False
    assert any(p.category == "tampered-receipt" for p in report.problems)
    assert report.accepted_cycles == ()


def test_verify_detects_recomputed_but_wrong_receipt_identity(tmp_path: Path) -> None:
    """A receipt that claims a receipt_id (and is filed under that name) that
    does not match what re-derivation from its own bundle actually
    produces."""
    archive_root = tmp_path / "archive"
    result = register_prospective_capture(_bundle_bytes(), archive_root)
    layout = archive_layout(archive_root)
    receipt_path = layout.receipts_dir / f"{result.receipt_id}.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["bundle_sha256"] = "0" * 64
    receipt_path.write_text(json.dumps(receipt))

    report = verify_archive(archive_root, as_of=_AS_OF)
    assert report.ok is False
    assert report.accepted_cycles == ()


# ---------------------------------------------------------------------------
# Missing referenced artifacts
# ---------------------------------------------------------------------------


def test_verify_detects_missing_referenced_bundle(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    result = register_prospective_capture(_bundle_bytes(), archive_root)
    layout = archive_layout(archive_root)
    (layout.bundles_dir / f"{result.bundle_sha256}.json").unlink()

    report = verify_archive(archive_root, as_of=_AS_OF)
    assert report.ok is False
    assert any(p.category == "missing-artifact" for p in report.problems)
    assert report.accepted_cycles == ()


def test_verify_detects_missing_referenced_receipt(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    result = register_prospective_capture(_bundle_bytes(), archive_root)
    layout = archive_layout(archive_root)
    (layout.receipts_dir / f"{result.receipt_id}.json").unlink()

    report = verify_archive(archive_root, as_of=_AS_OF)
    assert report.ok is False
    assert any(p.category == "missing-artifact" for p in report.problems)
    assert report.accepted_cycles == ()


# ---------------------------------------------------------------------------
# Filename/hash identity and orphan detection
# ---------------------------------------------------------------------------


def test_verify_detects_filename_hash_mismatch_for_stray_bundle(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    layout = archive_layout(archive_root)
    layout.bundles_dir.mkdir(parents=True)
    real_bytes = _bundle_bytes()
    (layout.bundles_dir / (("0" * 64) + ".json")).write_bytes(real_bytes)  # deliberately wrong name

    report = verify_archive(archive_root, as_of=_AS_OF)
    assert report.ok is False
    assert any(p.category == "hash-mismatch" for p in report.problems)


def test_verify_reports_orphan_bundle_and_receipt_without_flagging_ok_false(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    accepted = register_prospective_capture(
        _bundle_bytes(values=("302", "307", "309.3")), archive_root
    )
    with pytest.raises(ForecastError):  # cherry-pick rejection, tested elsewhere in detail
        register_prospective_capture(_bundle_bytes(values=("303", "307", "309.3")), archive_root)

    report = verify_archive(archive_root, as_of=_AS_OF)
    assert report.ok is True
    assert len(report.orphan_bundles) == 1
    assert len(report.orphan_receipts) == 1
    assert [c.bundle_sha256 for c in report.accepted_cycles] == [accepted.bundle_sha256]


def test_verify_flags_invalid_orphan_bundle_as_problem(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    layout = archive_layout(archive_root)
    layout.bundles_dir.mkdir(parents=True)
    _observations, parsed, _sha = parse_and_validate_bundle(_bundle_bytes())
    parsed["protocol_identity"] = "tampered-but-self-consistent"
    raw = json.dumps(parsed, sort_keys=True).encode()
    sha = hashlib.sha256(raw).hexdigest()
    (layout.bundles_dir / f"{sha}.json").write_bytes(raw)

    report = verify_archive(archive_root, as_of=_AS_OF)
    assert report.ok is False
    assert any(p.category == "invalid-bundle" for p in report.problems)


def test_verify_flags_tampered_orphan_receipt_as_problem(tmp_path: Path) -> None:
    """An orphan receipt (unreferenced by any accepted cycle) that names a
    real archived bundle but whose content does not match what
    `derive_receipt` actually recomputes from that bundle must be reported
    as tampered, even though nothing else in the archive points to it."""
    archive_root = tmp_path / "archive"
    result = register_prospective_capture(_bundle_bytes(), archive_root)
    layout = archive_layout(archive_root)
    genuine_receipt = json.loads((layout.receipts_dir / f"{result.receipt_id}.json").read_text())

    forged_receipt_id = "f" * 64
    forged = dict(genuine_receipt)
    forged["receipt_id"] = forged_receipt_id
    forged["target_dates"] = ["1999-01-01", "1999-01-02", "1999-01-03"]
    (layout.receipts_dir / f"{forged_receipt_id}.json").write_text(json.dumps(forged))

    report = verify_archive(archive_root, as_of=_AS_OF)
    assert report.ok is False
    assert any(
        p.category == "tampered-receipt" and forged_receipt_id in p.path for p in report.problems
    )
    # The genuine, correctly-referenced cycle is unaffected by the forgery.
    assert [c.receipt_id for c in report.accepted_cycles] == [result.receipt_id]


# ---------------------------------------------------------------------------
# Malformed cycle payloads
# ---------------------------------------------------------------------------


def test_verify_rejects_cycle_with_extra_field(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    result = register_prospective_capture(_bundle_bytes(), archive_root)
    layout = archive_layout(archive_root)
    cycle_path = layout.cycles_dir / f"{result.cycle_key}.json"
    cycle = json.loads(cycle_path.read_text())
    cycle["unexpected_field"] = "x"
    cycle_path.write_text(json.dumps(cycle))

    report = verify_archive(archive_root, as_of=_AS_OF)
    assert report.ok is False
    assert any(p.category == "malformed-cycle" for p in report.problems)


def test_verify_rejects_cycle_claiming_nonzero_production_influence(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    result = register_prospective_capture(_bundle_bytes(), archive_root)
    layout = archive_layout(archive_root)
    cycle_path = layout.cycles_dir / f"{result.cycle_key}.json"
    cycle = json.loads(cycle_path.read_text())
    cycle["production_influence"] = "0.01"
    cycle_path.write_text(json.dumps(cycle))

    report = verify_archive(archive_root, as_of=_AS_OF)
    assert report.ok is False
    assert any(p.category == "malformed-cycle" for p in report.problems)


def test_verify_rejects_cycle_filename_mismatch(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    result = register_prospective_capture(_bundle_bytes(), archive_root)
    layout = archive_layout(archive_root)
    cycle_path = layout.cycles_dir / f"{result.cycle_key}.json"
    misnamed = layout.cycles_dir / "20260902T030000Z.json"
    misnamed.write_bytes(cycle_path.read_bytes())
    cycle_path.unlink()

    report = verify_archive(archive_root, as_of=_AS_OF)
    assert report.ok is False
    assert any(p.category == "filename-mismatch" for p in report.problems)
    assert report.accepted_cycles == ()


# ---------------------------------------------------------------------------
# No miss/operator note counts as evidence
# ---------------------------------------------------------------------------


def test_verify_stray_non_json_file_in_cycles_dir_never_counts_as_captured(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    layout = archive_layout(archive_root)
    layout.cycles_dir.mkdir(parents=True)
    (layout.cycles_dir / "operator-note.txt").write_text("captured by hand, trust me")

    report = verify_archive(archive_root, as_of=_SEPT1_REFERENCE + timedelta(days=1))
    assert report.ok is False  # unexpected-file is still surfaced
    classifications = dict(report.cycle_classifications)
    assert classifications[_SEPT1_CYCLE_KEY].value != "CAPTURED"


def test_verify_note_shaped_like_a_cycle_key_but_invalid_json_is_not_captured(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    layout = archive_layout(archive_root)
    layout.cycles_dir.mkdir(parents=True)
    (layout.cycles_dir / f"{_SEPT1_CYCLE_KEY}.json").write_text(
        "captured -- operator note, trust me"
    )

    report = verify_archive(archive_root, as_of=_SEPT1_REFERENCE + timedelta(days=1))
    assert report.ok is False
    classifications = dict(report.cycle_classifications)
    assert classifications[_SEPT1_CYCLE_KEY].value == "MISSED"
    assert report.accepted_cycles == ()


# ---------------------------------------------------------------------------
# Coverage boundary classification end to end
# ---------------------------------------------------------------------------


def test_verify_classifies_future_cycles_pending_when_none_registered(tmp_path: Path) -> None:
    report = verify_archive(tmp_path / "archive", as_of=_SEPT1_REFERENCE - timedelta(days=1))
    classifications = dict(report.cycle_classifications)
    assert classifications[_SEPT1_CYCLE_KEY].value == "PENDING"


def test_verify_classifies_past_uncaptured_cycles_missed(tmp_path: Path) -> None:
    report = verify_archive(tmp_path / "archive", as_of=_SEPT1_REFERENCE + timedelta(days=5))
    classifications = dict(report.cycle_classifications)
    assert classifications[_SEPT1_CYCLE_KEY].value == "MISSED"


def test_verify_mixed_archive_has_captured_pending_and_missed(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    register_prospective_capture(_bundle_bytes(reference="20260901030000"), archive_root)
    as_of = _SEPT1_REFERENCE + timedelta(
        days=10
    )  # sept1 captured; sept2..~sept11 missed; later pending
    report = verify_archive(archive_root, as_of=as_of)
    classifications = dict(report.cycle_classifications)
    assert classifications[_SEPT1_CYCLE_KEY].value == "CAPTURED"
    assert classifications["20260905T030000Z"].value == "MISSED"
    assert classifications["20261001T030000Z"].value == "PENDING"


# ---------------------------------------------------------------------------
# Source scan: no network / Kalshi / credential / outcome / risk / execution
# imports, anywhere in the M27M allowed-scope files.
# ---------------------------------------------------------------------------


_FORBIDDEN_TOKENS = (
    "production_execution",
    "credential",
    "risk_engine",
    "kalshi_account_gateway",
    "parse_ghcnd_daily",
    "GhcndDailySnapshotEvidence",
    "urllib",
    "socket",
    "requests",
    "subprocess",
)


@pytest.mark.parametrize(
    "path",
    (
        "services/forecasting/weather_prospective_operations.py",
        "scripts/register_m27m_prospective_capture.py",
        "scripts/verify_m27m_prospective_collection.py",
    ),
)
def test_no_forbidden_imports_in_m27m_files(path: str) -> None:
    source = Path(path).read_text()
    for token in _FORBIDDEN_TOKENS:
        assert token not in source, f"{token!r} found in {path}"


def test_m27m_operations_module_has_no_market_or_outcome_fields() -> None:
    source = Path("services/forecasting/weather_prospective_operations.py").read_text()
    for token in ("observed_deg_f", "residual", "crps", "market_data", "fair_value"):
        assert token not in source


def test_no_production_reverse_import_of_m27m() -> None:
    reverse_import_dirs = (
        "services/production_execution",
        "services/risk_engine",
        "services/kalshi_account_gateway",
        "services/execution_simulation",
        "services/demo_execution",
        "services/opportunity_engine",
    )
    for directory in reverse_import_dirs:
        for py_file in Path(directory).rglob("*.py"):
            source = py_file.read_text()
            assert "weather_prospective_operations" not in source, (
                f"reverse import found in {py_file}"
            )


# ---------------------------------------------------------------------------
# Frozen M27L/M27I files remain byte-unchanged relative to HEAD
# ---------------------------------------------------------------------------


_FROZEN_FILES = (
    "services/forecasting/weather_prospective.py",
    "services/forecasting/weather_prospective_capture.py",
    "services/forecasting/weather_calibration_grib.py",
    "scripts/capture_m27l_prospective_forecast.py",
    "tests/test_m27l_prospective_capture.py",
    "tests/test_m27l_prospective_capture_cli.py",
    "services/supervised_canary/m27i.py",
    "tests/test_m27i_candidate_exposure_pagination.py",
    "tests/test_m27i_live_weather_preflight.py",
)


@pytest.mark.parametrize("path", _FROZEN_FILES)
def test_frozen_m27l_m27i_files_are_byte_unchanged_relative_to_head(path: str) -> None:
    # Local repo read only (no network, no untrusted PATH); resolved via
    # shutil.which so this isn't a bare partial-path subprocess invocation.
    git = shutil.which("git")
    assert git is not None
    committed = subprocess.run(
        [git, "show", f"HEAD:{path}"], capture_output=True, check=True
    ).stdout
    working_tree = Path(path).read_bytes()
    assert working_tree == committed, (
        f"{path} differs from HEAD -- frozen file must not be modified"
    )
