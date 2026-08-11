from __future__ import annotations

import time
from pathlib import Path

from services.web_dashboard.app import DashboardApp
from tests.test_m1_security_ui import call, configured_store


def source_fixture(source_id: str, state: str = "SHADOW") -> dict[str, object]:
    return {
        "source_id": source_id,
        "source_name": source_id.title(),
        "state": state,
        "source_class": "Primary source" if source_id == "rss" else "Market",
        "last_event": "2026-08-10T00:00:00Z",
        "latency_ms": 420,
        "uptime": "fixture",
        "duplicate_rate": "0%",
        "parse_errors": 0,
        "unique_relevant_signals": 1,
        "monthly_cost": "$0",
        "setup_requirement": "Bearer token required" if source_id == "x" else None,
        "production_influence": "NONE",
        "integration_note": "Direct API: Not available"
        if source_id == "predictbuddy"
        else "Official public/authorized path",
    }


def signal_fixture(
    signal_id: str, headline: str = "FED <script>alert(1)</script>"
) -> dict[str, object]:
    return {
        "signal_id": signal_id,
        "detected_at": f"2026-08-10T00:00:{int(signal_id) % 60:02d}Z",
        "headline": headline,
        "source_name": "Official Federal Reserve",
        "source_class": "Primary source",
        "age_label": "14 seconds ago",
        "latency_label": "420 ms after publication",
        "kalshi_market": "KXFED",
        "polymarket_relationship": "STRONG_CANDIDATE",
        "verification_state": "PRIMARY_CONFIRMED",
        "corroboration": "1 independent source chain",
        "manipulation_flags": "None",
        "kalshi_reaction": "+1.0 point since observation",
        "polymarket_reaction": "+3.1 points since observation",
        "current_action": "Research only",
        "production_influence": "NONE",
        "source_lineage": "Federal Reserve release",
        "provenance": "official feed item hash abc",
        "duplicate_chain": "No duplicates",
        "correction_state": "Original active",
    }


def test_breaking_sources_signal_detail_escape_and_states(tmp_path: Path) -> None:
    store, box, token, _ = configured_store(tmp_path)
    for source, state in (
        ("polymarket", "SHADOW"),
        ("rss", "SHADOW"),
        ("bluesky", "SHADOW"),
        ("x", "SETUP REQUIRED"),
        ("predictbuddy", "SETUP REQUIRED"),
        ("reddit", "SETUP REQUIRED"),
    ):
        store.seed_external_source_fixture(source_fixture(source, state))
    store.seed_breaking_signal_fixture(signal_fixture("1"))
    app = DashboardApp(store, box)
    _, _, breaking = call(app, "/breaking", cookie=f"session={token}")
    assert (
        b"Breaking Now" in breaking
        and b"Trading influence: NONE" in breaking
        and b"&lt;script&gt;" in breaking
        and b"<script>" not in breaking
    )
    _, _, sources = call(app, "/sources", cookie=f"session={token}")
    assert (
        b"SETUP REQUIRED" in sources
        and b"Direct API: Not available" in sources
        and b"Production influence: NONE" in sources
    )
    _, _, detail = call(app, "/breaking/1", cookie=f"session={token}")
    assert b"not a trade recommendation" in detail and b"Raw payload" in detail


def test_breaking_ui_paginates_large_fixture_without_external_scan(tmp_path: Path) -> None:
    store, box, token, _ = configured_store(tmp_path)
    start = time.monotonic()
    for i in range(2000):
        store.seed_breaking_signal_fixture(signal_fixture(str(i), f"Signal {i}"))
    assert time.monotonic() - start < 10
    app = DashboardApp(store, box)
    _, _, body = call(app, "/breaking", cookie=f"session={token}")
    assert body.count(b"signal-card") <= 50 and b"2000" in body


def test_m5_has_no_authority_or_mutation_imports() -> None:
    source = "\n".join(path.read_text() for path in Path("services/breaking_signals").glob("*.py"))
    forbidden = (
        "risk_engine",
        "authorize_new_risk",
        "kalshi_account_gateway.auth",
        "execution_gateway",
        "RequestSigner",
        'method="POST"',
        'method="PUT"',
        'method="PATCH"',
        'method="DELETE"',
    )
    assert (
        all(item not in source for item in forbidden)
        and "production_influence" in source
        and "SHADOW RESEARCH DATA" in source
    )
