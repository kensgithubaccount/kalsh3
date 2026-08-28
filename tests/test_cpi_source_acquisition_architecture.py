from __future__ import annotations

from pathlib import Path


def _production_python_files(repo_root: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    for root_name in ("services", "scripts"):
        root = repo_root / root_name
        if root.exists():
            paths.extend(root.rglob("*.py"))
    return tuple(sorted(paths))


def test_acquisition_authority_internals_have_exact_single_production_owner() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    owner = repo_root / "services/forecasting/cpi_source_acquisition.py"
    restricted = (
        "_ACQUISITION_EVIDENCE_CAPABILITY",
        "_ISSUED_ACQUISITION_FINGERPRINTS",
        "_TransportResult",
    )
    references: dict[str, set[Path]] = {symbol: set() for symbol in restricted}
    for path in _production_python_files(repo_root):
        source = path.read_text(encoding="utf-8")
        for symbol in restricted:
            if symbol in source:
                references[symbol].add(path)

    expected = {owner}
    assert references == {symbol: expected for symbol in restricted}


def test_acquisition_evidence_direct_construction_is_confined_to_acquisition_module() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    owner = repo_root / "services/forecasting/cpi_source_acquisition.py"
    constructor = "CPIBLSAcquisitionEvidence("
    references = {
        path
        for path in _production_python_files(repo_root)
        if constructor in path.read_text(encoding="utf-8")
    }
    assert references == {owner}
