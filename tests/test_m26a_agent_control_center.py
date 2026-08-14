"""M26A immutable agent contracts and owner-facing dashboard surfaces."""

from __future__ import annotations

import io
import json
import time
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from services.agent_control_center.domain import (
    AGENT_REGISTRY,
    AgentDefinition,
    AutonomyMode,
    DecisionReceipt,
    ImplementationAvailability,
    ResearchDecision,
    explain_decision,
)
from services.web_dashboard.app import DashboardApp
from services.web_dashboard.product import (
    NAV_SECTIONS,
    SURFACES,
    assert_navigation_covers_all_surfaces,
    assert_non_mutating_surfaces,
)
from services.web_dashboard.security import SecretBox, hash_password
from services.web_dashboard.store import StateStore


def _configured(tmp_path: Path) -> tuple[DashboardApp, str]:
    store = StateStore(tmp_path / "state.db")
    box = SecretBox(b"k" * 32)
    store.set_config("owner", "owner")
    store.set_config("password_hash", hash_password("LongProduction9Password"))
    store.set_config("vault", box.seal(b"read-only"))
    store.set_config("totp_secret", box.seal(b"JBSWY3DPEHPK3PXP"))
    token, _ = store.create_session(int(time.time()))
    return DashboardApp(store, box), token


def _get(app: DashboardApp, path: str, token: str) -> tuple[str, str]:
    captured: dict[str, Any] = {}

    def start(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status

    body = b"".join(
        app(
            {
                "PATH_INFO": path,
                "QUERY_STRING": "",
                "REQUEST_METHOD": "GET",
                "HTTP_COOKIE": f"session={token}",
                "CONTENT_LENGTH": "0",
                "wsgi.input": io.BytesIO(b""),
                "REMOTE_ADDR": "127.0.0.1",
            },
            start,
        )
    )
    return str(captured["status"]), body.decode()


def _receipt(**overrides: object) -> DecisionReceipt:
    values: dict[str, object] = {
        "receipt_id": "receipt-001",
        "created_at": datetime(2026, 8, 14, 12, tzinfo=UTC),
        "agent_id": "event-edge",
        "agent_version": "1.0.0",
        "instrument_id": "TEST-MARKET",
        "observed_market_price": Decimal("0.5100"),
        "model_probability": Decimal("0.5600"),
        "fair_value": Decimal("0.5600"),
        "raw_edge": Decimal("0.0500"),
        "estimated_fees": Decimal("0.0100"),
        "estimated_slippage": Decimal("0.0050"),
        "after_cost_edge": Decimal("0.0350"),
        "confidence": Decimal("0.61"),
        "evidence_references": ("evidence:one", "evidence:two"),
        "current_exposure": Decimal("0.00"),
        "applicable_limits": (("market_loss", Decimal("10.00")),),
        "risk_check_results": ("EVIDENCE_OK",),
        "decision": ResearchDecision.NO_TRADE,
        "rejected_alternatives": ("YES at stale quote",),
        "rejection_reasons": ("confidence is below its threshold",),
        "production_influence": Decimal("0"),
    }
    values.update(overrides)
    return DecisionReceipt(**values)  # type: ignore[arg-type]


def test_registry_is_unique_deterministic_immutable_and_zero_influence() -> None:
    ids = tuple(agent.agent_id for agent in AGENT_REGISTRY)
    assert ids == tuple(agent.agent_id for agent in AGENT_REGISTRY)
    assert len(ids) == len(set(ids))
    assert all(agent.production_influence == Decimal("0") for agent in AGENT_REGISTRY)
    with pytest.raises(FrozenInstanceError):
        AGENT_REGISTRY[0].display_name = "changed"  # type: ignore[misc]


def test_nonzero_influence_and_unsupported_autonomy_fail_closed() -> None:
    values = {field: getattr(AGENT_REGISTRY[0], field) for field in AGENT_REGISTRY[0].__slots__}
    values["production_influence"] = Decimal("0.01")
    with pytest.raises(ValueError, match="zero production influence"):
        AgentDefinition(**values)
    with pytest.raises(ValueError):
        AutonomyMode("LIVE")
    values["production_influence"] = Decimal("0")
    values["availability"] = ImplementationAvailability.PLANNED
    values["autonomy_mode"] = AutonomyMode.SHADOW
    with pytest.raises(ValueError, match="must be disabled"):
        AgentDefinition(**values)


def test_receipt_is_immutable_deterministic_and_preserves_decimal_text() -> None:
    receipt = _receipt()
    assert receipt.to_json() == receipt.to_json()
    payload = json.loads(receipt.to_json())
    assert payload["observed_market_price"] == "0.5100"
    assert payload["after_cost_edge"] == "0.0350"
    assert payload["applicable_limits"] == [["market_loss", "10.00"]]
    assert payload["rejected_alternatives"] == ["YES at stale quote"]
    assert payload["production_influence"] == "0"
    with pytest.raises(FrozenInstanceError):
        receipt.decision = ResearchDecision.WOULD_TRADE  # type: ignore[misc]


@pytest.mark.parametrize("agent_id", ["perps", "portfolio"])
def test_receipt_rejects_unavailable_disabled_agents(agent_id: str) -> None:
    with pytest.raises(ValueError, match="agent is not available"):
        _receipt(agent_id=agent_id)


@pytest.mark.parametrize("agent_id", ["event-edge", "learning"])
def test_available_non_disabled_agents_can_produce_research_receipts(agent_id: str) -> None:
    receipt = _receipt(agent_id=agent_id)
    assert receipt.agent_id == agent_id
    assert receipt.production_influence == Decimal("0")


def test_deterministic_no_trade_explanation_uses_structured_reason() -> None:
    receipt = _receipt()
    expected = (
        "Event Edge sees a 3.5% after-cost edge, but confidence is below its threshold, "
        "so it would not trade."
    )
    assert explain_decision(receipt) == expected
    assert explain_decision(receipt) == expected


def test_agents_routes_escape_registry_content_and_show_honest_empty_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, token = _configured(tmp_path)
    status, page = _get(app, "/agents", token)
    assert status == "200 OK"
    assert "Trading OFF" in page
    assert "Production influence: 0" in page
    assert "No decisions yet" in page
    assert "Not enough evidence" in page
    status, detail = _get(app, "/agents/event-edge", token)
    assert status == "200 OK"
    for heading in (
        "What it watches",
        "What it believes",
        "When it would act",
        "Why it can be wrong",
        "Current mode",
        "Guardrails",
        "Recent decisions",
        "Performance",
    ):
        assert heading in detail
    hostile = AgentDefinition(
        "hostile",
        "<script>alert(1)</script>",
        "1",
        "safe",
        "safe",
        "safe",
        "safe",
        "safe",
        "safe",
        ("<img src=x>",),
        ImplementationAvailability.AVAILABLE,
        AutonomyMode.SHADOW,
        Decimal("0"),
        "safe",
        "Not enough evidence",
    )
    monkeypatch.setattr("services.web_dashboard.app.AGENT_REGISTRY", (hostile,))
    _, escaped = _get(app, "/agents", token)
    assert "<script>" not in escaped and "&lt;script&gt;" in escaped
    assert "<img" not in escaped


def test_agent_detail_route_escapes_registry_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, token = _configured(tmp_path)
    hostile = AgentDefinition(
        "hostile-detail",
        "<script>alert(1)</script>",
        "1",
        "<img src=x onerror=alert(1)>",
        "<unsafe-universe>",
        "<unsafe-inputs>",
        "<unsafe-watches>",
        "<unsafe-belief>",
        "<unsafe-action>",
        ("<unsafe-risk>",),
        ImplementationAvailability.AVAILABLE,
        AutonomyMode.SHADOW,
        Decimal("0"),
        "<unsafe-guardrail>",
        "<unsafe-performance>",
    )
    monkeypatch.setattr("services.agent_control_center.domain.AGENT_REGISTRY", (hostile,))

    status, page = _get(app, "/agents/hostile-detail", token)

    assert status == "200 OK"
    for raw in (
        "<script>",
        "<img",
        "<unsafe-universe>",
        "<unsafe-inputs>",
        "<unsafe-watches>",
        "<unsafe-belief>",
        "<unsafe-action>",
        "<unsafe-risk>",
        "<unsafe-guardrail>",
        "<unsafe-performance>",
    ):
        assert raw not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "&lt;unsafe-guardrail&gt;" in page


def test_navigation_and_surfaces_are_complete_and_non_mutating() -> None:
    assert [section.label for section in NAV_SECTIONS] == [
        "Overview",
        "Agents",
        "Opportunities",
        "Positions",
        "Learning",
        "System",
    ]
    assert any(surface.path == "/agents" for surface in SURFACES)
    assert_navigation_covers_all_surfaces()
    assert_non_mutating_surfaces()


def test_overview_exposes_agent_authority_and_empty_receipt_state(tmp_path: Path) -> None:
    app, token = _configured(tmp_path)
    status, page = _get(app, "/", token)
    assert status == "200 OK"
    assert "Agent desk" in page
    assert "Production influence" in page and ">0<" in page
    assert "No decisions yet" in page
    assert "Trading OFF is the expected safe state" in page
