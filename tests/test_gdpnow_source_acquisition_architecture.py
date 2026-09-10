from __future__ import annotations

from pathlib import Path


def _production_python_files(repo_root: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    for root_name in ("services", "scripts"):
        root = repo_root / root_name
        if root.exists():
            paths.extend(root.rglob("*.py"))
    return tuple(sorted(paths))


def test_acquisition_internals_have_exact_single_production_owner() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    owner = repo_root / "services/forecasting/gdpnow_source_acquisition.py"
    restricted = (
        "_GDPNOW_EVIDENCE_ISSUANCE_CAPABILITY",
        "_ISSUED_GDPNOW_ACQUISITION_FINGERPRINTS",
        "_GDPNowTransportResult",
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
    owner = repo_root / "services/forecasting/gdpnow_source_acquisition.py"
    constructor = "GDPNowAcquisitionEvidence("
    references = {
        path
        for path in _production_python_files(repo_root)
        if constructor in path.read_text(encoding="utf-8")
    }
    assert references == {owner}


def test_parsed_vintage_internals_have_exact_single_production_owner() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    owner = repo_root / "services/forecasting/gdpnow_parsing.py"
    restricted = (
        "_PARSED_VINTAGE_CAPABILITY",
        "_ISSUED_PARSED_FINGERPRINTS",
    )
    references: dict[str, set[Path]] = {symbol: set() for symbol in restricted}
    for path in _production_python_files(repo_root):
        source = path.read_text(encoding="utf-8")
        for symbol in restricted:
            if symbol in source:
                references[symbol].add(path)

    expected = {owner}
    assert references == {symbol: expected for symbol in restricted}


def test_parsed_vintage_direct_construction_is_confined_to_parsing_module() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    owner = repo_root / "services/forecasting/gdpnow_parsing.py"
    constructor = "ParsedGDPNowVintage("
    references = {
        path
        for path in _production_python_files(repo_root)
        if constructor in path.read_text(encoding="utf-8")
    }
    assert references == {owner}


def test_no_gdpnow_module_makes_network_acquisition_automatic_in_ci() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    needles = ("acquire_gdpnow_commentary_page(",)
    for path in sorted((repo_root / ".github/workflows").glob("*.yml")):
        workflow = path.read_text(encoding="utf-8")
        assert all(needle not in workflow for needle in needles)


def test_no_gdpnow_module_imports_or_targets_kalshi_acquisition_or_scoring() -> None:
    """D1-G1 must not depend on any Kalshi client, quote, fee, or scoring module.

    Mentioning "Kalshi" in prose (to state a scope boundary) is fine and expected;
    what is forbidden is an actual import of, or reference to, Kalshi transport,
    quote, fee, orderbook, decision, or scoring machinery.
    """
    repo_root = Path(__file__).resolve().parents[1]
    forbidden_imports = (
        "services.kalshi_account_gateway",
        "services.real_time_market_data",
        "services.production_execution",
        "services.execution_simulation",
        "services.risk_engine",
    )
    forbidden_symbols = ("KXGDP", "orderbook", "OrderBook", "decision_cutoff", "after_cost")
    for name in ("gdpnow_source_acquisition.py", "gdpnow_parsing.py"):
        source = (repo_root / "services/forecasting" / name).read_text(encoding="utf-8")
        assert all(value not in source for value in forbidden_imports), name
        assert all(value not in source for value in forbidden_symbols), name
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                assert "kalshi" not in stripped.lower(), (name, stripped)
