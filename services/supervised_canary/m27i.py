"""M27I -- operator-only, READ-ONLY final live weather canary preflight.

This module never arms production, never sends/cancels/amends/decreases an order, never calls
a mutating endpoint, never touches ``security_boundary.py``/``transport.py`` production-execute
paths, and never consumes the final real-money acknowledgement. It answers exactly one
question: given current, already-reviewed evidence, would there be exactly one currently
eligible experimental M27D candidate safe to present to a human for final approval right now?

Everything here is evidence *consumption* and *binding*, never re-derivation of economic or
selection logic:

* candidate selection/threshold/ranking is delegated entirely to
  :func:`services.supervised_canary.m27d.select_experimental_candidate` -- this module never
  reimplements it;
* M27F/M27H live-evidence gate derivation (freshness, classification) is delegated to
  :func:`services.supervised_canary.readiness_report.operator_evidence` -- this module never
  reimplements ``_consumption_fresh``;
* M13 risk is delegated to a fresh, already-produced :class:`services.risk_engine.domain.
  RiskDecision`/:class:`services.risk_engine.domain.RiskIntent`/:class:`services.risk_engine.
  domain.PortfolioRiskSnapshot` triple -- this module never recomputes exposure, reserve, or
  loss-limit math itself, and never fabricates a "one contract is small" shortcut. It only
  independently re-binds that triple to the exact candidate and to the exact current M27F/
  candidate-exposure account evidence, re-derives every content hash rather than trusting the
  ones stamped on the objects, re-checks the decision is still inside its own 5-second expiry
  contract, and re-checks that global halt/kills/compliance are clear right now (a real,
  read-only :class:`services.risk_engine.authorization.AuthorizationStore` query).

Where current evidence cannot honestly prove a gate (no defensible authority source exists in
this repository yet), the gate is left ``False``/BLOCKED with an explicit reason. Nothing here
is ever marked PASS because a unit test once passed, and no gate is ever constructed with an
unconditional literal ``True`` -- every entry in the gate table below is the return value of a
function that independently derived it from supplied evidence.

Gemini M27I delta repair (2026-08-18):

1. The M13 risk decision is now independently re-bound to a caller-supplied
   :class:`PortfolioRiskSnapshot`, and the snapshot's ``account_snapshot_version``/
   ``reconciliation_version`` fields are independently re-derived from the exact fresh M27F
   account evidence and M27I candidate-exposure evidence in use right now (see
   :func:`compute_account_snapshot_version`/:func:`compute_reconciliation_version`) rather than
   merely trusted.
2. The preflight's own expiry is now the minimum of every underlying evidence deadline, not a
   fixed 30 seconds capped only by the candidate's own expiry.
3. ``candidate_current``/``model_eligible`` no longer PASS unconditionally -- each is
   independently derived from evidence.
4. ``trading_active``/``exchange_active`` now require the exact, reviewed
   ``kalsh3.m27e.public-read.v1`` fixed-origin GET-only evidence schema produced by
   ``scripts/m27e_public_read_acceptance.py`` -- a caller-fabricated JSON object can no longer
   unlock either gate.
5. Exactly one currently-qualifying M27D candidate is now enforced literally: two or more
   qualifying candidates abstains rather than silently picking one.
6. Fee currentness is now proven from the upstream authoritative
   :class:`services.opportunity_engine.live_fees.CurrentSeriesFeeObservation`/
   :class:`services.opportunity_engine.live_fees.EventFeeOverride` pair -- this module resolves
   the current fee regime itself; a bare, timestamp-free ``ResolvedFeeRegime`` can no longer
   unlock ``fee_verified`` by itself.
7. A serialized preflight artifact is never trusted merely because it deserializes --
   :func:`validate_preflight_artifact` independently recomputes its canonical content hash.

Gemini M27I FINAL delta repair (2026-08-18) -- rules currentness:

An earlier pass of this repair aliased fresh M27E ``markets`` evidence (ticker/event/status
existence) directly to ``rules_current``. Gemini correctly rejected that: proving a market
currently *exists and is open* is not the same claim as proving its *rules identity* --
``MarketEconomicsEvidence.market_rules_hash`` -- is still current. No reviewed source in this
repository can make that second comparison: ``market_rules_hash`` is caller-supplied at
economics-evaluation time, not independently derivable from any GET response this repository
has reviewed, and no field on the M27E evidence bundle (ticker, event, status, title, market
metadata hash, orderbook, or an old ``MarketEconomicsEvidence``) is authoritative current
rules-text identity -- using any of them to "prove" rules currentness would be inventing
authority, not consuming it.

This module therefore keeps two claims cleanly separate:

* ``market_open_current`` / ``book_executable`` (folded together into ``market_tradable``):
  the honest, still-provable claim that the exchange currently reports this exact market open
  and that its displayed book still supports the candidate's price/quantity right now.
* ``rules_current``: the *rules identity* claim -- never derived here from market existence,
  status, metadata hash, orderbook, or any other proxy, because none of those is that authority.

M27J delta (2026-08-18): a dedicated authority for current rules identity now exists --
:mod:`services.supervised_canary.m27j`. It performs a single fresh, bounded, unauthenticated
PUBLIC GET of the exact-market endpoint, and independently re-derives that market's rules hash
through the same canonical :meth:`services.market_universe.domain.Market.parse` this repository
already uses everywhere else a rules hash is computed.

Gemini M27J DELTA repair (2026-08-18) -- expected-side authority:

The first M27J delta compared M27J's fresh current rules hash directly against
``economics.market_rules_hash``. Gemini correctly rejected that: ``economics.market_rules_hash``
is caller-supplied at the legacy :func:`services.opportunity_engine.live_economics.
normalize_live_orderbook` call site -- a caller can simply copy today's M27J hash into economics
and trivially obtain ``H == H``, proving nothing about where that hash actually came from. This
module now requires BOTH independently validated sides before ``rules_current`` can PASS:

* an **authoritative economics-market binding**
  (:func:`services.opportunity_engine.authoritative_economics.
  validate_authoritative_economics_market_binding`) -- proof that the exact
  ``MarketEconomicsEvidence`` in use was built, via
  :func:`services.opportunity_engine.authoritative_economics.build_authoritative_market_economics`,
  from an independently re-validated live market snapshot acquired at (or immediately before)
  economics-evaluation time, never a bare stamped string; and
* fresh **current-side M27J evidence**
  (:func:`services.supervised_canary.m27j.validate_current_rules_for_candidate`), compared not
  against ``economics.market_rules_hash`` but against the rules hash the binding validator itself
  independently re-derived from the expected snapshot's raw bytes.

No authoritative binding supplied -- even when a legacy, caller-fabricated
``economics.market_rules_hash`` happens to equal the current M27J hash -- leaves
``rules_current`` ``BLOCKED``. This is the mandatory adversarial case: legacy
``MarketEconomicsEvidence`` (still valid for research, unmodified, see
:mod:`services.opportunity_engine.live_economics`) is never sufficient for live-canary rules
authority on its own. The old fail-closed default is unchanged in every other respect: no M27J
evidence, stale M27J evidence, a malformed artifact, or a changed rules hash all still leave
``rules_current`` ``BLOCKED``. Only an exact, fresh, independently re-derived match on both sides
unlocks it. Because ``rules_current`` remains a required gate, M27I can now reach
``PREFLIGHT_READY`` on an otherwise-perfect path, but only when both authorities are supplied and
agree.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from services.forecasting.weather_probability import (
    CLAIM_TYPE,
    SETTLEMENT_MAPPING_STATUS,
    CurrentWeatherForecastEvidence,
    PhysicalTemperatureProxyProbability,
)
from services.forecasting.weather_prospective import FAMILY, FROZEN_MODEL_IDENTITIES
from services.opportunity_engine.authoritative_economics import (
    validate_authoritative_economics_market_binding,
)
from services.opportunity_engine.books import OutcomeSide
from services.opportunity_engine.domain import OpportunityError
from services.opportunity_engine.live_economics import (
    MarketEconomicsEvidence,
    replay_market_economics,
)
from services.opportunity_engine.live_fees import (
    CurrentSeriesFeeObservation,
    EventFeeOverride,
    resolve_current_fee_regime,
)
from services.production_execution.installed_credential_verification import (
    EVIDENCE_FRESHNESS as M27H_EVIDENCE_FRESHNESS,
)
from services.risk_engine.authorization import AuthorizationStore
from services.risk_engine.domain import (
    PortfolioRiskSnapshot,
    RiskDecision,
    RiskDecisionState,
    RiskIntent,
)
from services.risk_engine.domain import content_hash as _risk_content_hash

from . import m27j
from .candidate_exposure_check import CandidateExposureEvidence
from .live_read_acceptance import USER_DATA_FRESHNESS
from .m27d import (
    AUGUST_END,
    AUGUST_START,
    CANARY_STATUS,
    MAX_BOOK_AGE,
    MAX_FORECAST_AGE,
    ONE_CONTRACT,
    CandidateResult,
    CandidateState,
    ExperimentalCandidate,
    select_experimental_candidate,
)
from .readiness_report import operator_evidence
from .store import CanaryStore

SCHEMA = "kalsh3.m27i.live-weather-preflight.v1"
SOFTWARE_VERSION = "kalsh3.m27i.live-weather-preflight/1"
PREFLIGHT_TTL = timedelta(seconds=30)

# Reused, not reinvented, freshness bounds: every one of these matches an existing reviewed
# evidence contract elsewhere in this repository (M27F/M27H's own 30-second live-evidence
# window, M27D's own book/forecast-age bounds). ``MAX_MARKET_METADATA_AGE`` and
# ``EXCHANGE_STATUS_FRESHNESS`` extend that same 30-second convention to the M27E public
# evidence bundle, which previously had no consumption-time freshness bound at all.
MAX_MARKET_METADATA_AGE = timedelta(seconds=30)
EXCHANGE_STATUS_FRESHNESS = timedelta(seconds=30)
FEE_FRESHNESS = USER_DATA_FRESHNESS

_CandidateInput = tuple[
    PhysicalTemperatureProxyProbability, CurrentWeatherForecastEvidence, MarketEconomicsEvidence
]

# The 20 gates named in the M27I milestone, plus ``m13_verified`` (required by the milestone's
# own section 9, though not listed among the 20 -- see module docstring), plus two informational
# sub-gates -- ``market_open_current``/``book_executable`` -- that this module tracks and reports
# separately from ``market_tradable`` so a human reader can never conflate "the exchange
# currently reports this market open and executable" with "rules identity is currently proven"
# (Gemini FINAL delta repair: those are different claims, and only the first is provable today).
GATE_NAMES: tuple[str, ...] = (
    "production_read_live_verified",
    "real_reconciliation_live_verified",
    "signer_runtime_live_verified",
    "model_eligible",
    "source_evidence_ready",
    "write_credential_installed",
    "compliance_clear",
    "global_halt_clear",
    "kills_clear",
    "exchange_active",
    "trading_active",
    "market_open_current",
    "book_executable",
    "market_tradable",
    "rules_current",
    "candidate_current",
    "fee_verified",
    "account_reconciled",
    "no_unknown_orders",
    "no_unknown_positions",
    "clock_safe",
    "write_budget_safe",
    "read_budget_safe",
    "m13_verified",
)

# Reason used when this exact preflight run supplied no M27J current-rules evidence at all.
# ``rules_current`` is never derived from market existence, status, metadata hash, orderbook, or
# any other proxy, because none of those is authoritative current rules identity -- only
# validated M27J evidence (see :mod:`services.supervised_canary.m27j`) is.
NO_AUTHORITATIVE_CURRENT_RULES_IDENTITY = "NO_AUTHORITATIVE_CURRENT_RULES_IDENTITY"


class AbstentionReason:
    NO_SUPPORTED_OPEN_MARKET = "ABSTAIN_NO_SUPPORTED_OPEN_MARKET"
    OUTSIDE_TARGET_WINDOW = "ABSTAIN_OUTSIDE_TARGET_WINDOW"
    STALE_FORECAST = "ABSTAIN_STALE_FORECAST"
    NO_MATCHING_ECONOMICS = "ABSTAIN_NO_MATCHING_ECONOMICS"
    THRESHOLD_OR_POLICY_NOT_MET = "ABSTAIN_THRESHOLD_OR_POLICY_NOT_MET"
    MULTIPLE_QUALIFYING_CANDIDATES = "ABSTAIN_MULTIPLE_QUALIFYING_CANDIDATES"


def _diagnose_market_discovery(inputs: Sequence[_CandidateInput], now: datetime) -> str:
    """Best-effort, structural-only classification of *why* no candidate qualifies.

    Never re-derives eligibility itself (that is exclusively
    :func:`services.supervised_canary.m27d.select_experimental_candidate`'s job) -- this only
    inspects the shape of the supplied inputs to give a human a more specific abstention reason
    than M27D's own single generic message.
    """
    if not inputs:
        return AbstentionReason.NO_SUPPORTED_OPEN_MARKET
    in_window = [item for item in inputs if AUGUST_START <= item[1].local_target_date <= AUGUST_END]
    if not in_window:
        return AbstentionReason.OUTSIDE_TARGET_WINDOW
    fresh = [
        item for item in in_window if now - item[1].forecast_reference_time <= MAX_FORECAST_AGE
    ]
    if not fresh:
        return AbstentionReason.STALE_FORECAST
    with_economics = [item for item in fresh if item[2] is not None]
    if not with_economics:
        return AbstentionReason.NO_MATCHING_ECONOMICS
    return AbstentionReason.THRESHOLD_OR_POLICY_NOT_MET


@dataclass(frozen=True, slots=True)
class GateResult:
    passed: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PreflightGates:
    results: dict[str, GateResult]

    @property
    def all_pass(self) -> bool:
        return all(result.passed for result in self.results.values())

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(sorted(name for name, result in self.results.items() if not result.passed))

    def to_json(self) -> dict[str, Any]:
        return {
            name: {"pass": result.passed, "reason": result.reason}
            for name, result in self.results.items()
        }


@dataclass(frozen=True, slots=True)
class PreflightArtifact:
    schema: str
    software_version: str
    created_at: datetime
    expires_at: datetime
    state: str
    abstain_reason: str | None
    candidate_id: str | None
    market_ticker: str | None
    event_ticker: str | None
    target_date: str | None
    selected_side: str | None
    executable_price: str | None
    maximum_fee: str | None
    maximum_commitment: str | None
    maximum_loss: str | None
    proxy_probability: str | None
    research_probability_discrepancy: str | None
    model_identity: str | None
    forecast_evidence_identity: str | None
    economics_evidence_identity: str | None
    gates: PreflightGates
    missing_gates: tuple[str, ...]
    warning: str | None
    content_hash: str = ""

    def __post_init__(self) -> None:
        payload = self._payload_for_hash()
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        object.__setattr__(self, "content_hash", digest)

    def _payload_for_hash(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "software_version": self.software_version,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "state": self.state,
            "abstain_reason": self.abstain_reason,
            "candidate_id": self.candidate_id,
            "market_ticker": self.market_ticker,
            "event_ticker": self.event_ticker,
            "target_date": self.target_date,
            "selected_side": self.selected_side,
            "executable_price": self.executable_price,
            "maximum_fee": self.maximum_fee,
            "maximum_commitment": self.maximum_commitment,
            "maximum_loss": self.maximum_loss,
            "proxy_probability": self.proxy_probability,
            "research_probability_discrepancy": self.research_probability_discrepancy,
            "model_identity": self.model_identity,
            "forecast_evidence_identity": self.forecast_evidence_identity,
            "economics_evidence_identity": self.economics_evidence_identity,
            "gates": self.gates.to_json(),
            "missing_gates": list(self.missing_gates),
            "warning": self.warning,
        }

    def to_json(self) -> dict[str, Any]:
        return {**self._payload_for_hash(), "content_hash": self.content_hash}

    def is_ready(self) -> bool:
        return self.state == "PREFLIGHT_READY"

    def fresh(self, now: datetime) -> bool:
        return self.created_at <= now <= self.expires_at

    def binds_to(self, candidate_id: str) -> bool:
        """Cheap identity check for the live, freshly-constructed in-process object only.

        This is safe here because ``__post_init__`` already stamped ``content_hash`` from this
        exact object's own fields -- there is no deserialization step between construction and
        this call. It must never be used as a substitute for :func:`validate_preflight_artifact`
        on a payload that has been serialized, transmitted, stored, or otherwise deserialized:
        a caller can hash lies too, so any JSON payload must have its content hash independently
        recomputed (see :func:`validate_preflight_artifact`) before any of its fields -- including
        ``candidate_id`` -- may be trusted.
        """
        return self.candidate_id == candidate_id


_PREFLIGHT_FIELDS = frozenset(
    {
        "schema",
        "software_version",
        "created_at",
        "expires_at",
        "state",
        "abstain_reason",
        "candidate_id",
        "market_ticker",
        "event_ticker",
        "target_date",
        "selected_side",
        "executable_price",
        "maximum_fee",
        "maximum_commitment",
        "maximum_loss",
        "proxy_probability",
        "research_probability_discrepancy",
        "model_identity",
        "forecast_evidence_identity",
        "economics_evidence_identity",
        "gates",
        "missing_gates",
        "warning",
        "content_hash",
    }
)


@dataclass(frozen=True, slots=True)
class PreflightValidation:
    valid: bool
    reason: str | None = None


def _canonical_preflight_hash(payload: dict[str, Any]) -> str:
    material = {key: value for key, value in payload.items() if key != "content_hash"}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def validate_preflight_artifact(
    payload: object, *, expected_candidate_id: str | None, now: datetime
) -> PreflightValidation:
    """Independently re-validate a serialized :class:`PreflightArtifact` payload.

    Never trusts the serialized ``content_hash``/``state``/``candidate_id`` in isolation:
    recomputes the canonical content hash (excluding only the ``content_hash`` field itself,
    exactly as ``PreflightArtifact.__post_init__`` computed it) and rejects any payload whose
    stored hash does not match, whose keys are unexpected, or whose timestamps/candidate
    binding do not line up with the caller's own expectations. ``binds_to`` alone must never be
    trusted on a deserialized payload -- call this first.
    """
    if not isinstance(payload, dict):
        return PreflightValidation(False, "preflight artifact payload is not an object")
    if set(payload) != _PREFLIGHT_FIELDS:
        return PreflightValidation(False, "preflight artifact has unexpected or missing fields")
    if payload.get("schema") != SCHEMA:
        return PreflightValidation(False, "preflight artifact schema mismatch")
    stored_hash = payload.get("content_hash")
    if not isinstance(stored_hash, str) or not stored_hash:
        return PreflightValidation(False, "preflight artifact content hash missing")
    if _canonical_preflight_hash(payload) != stored_hash:
        return PreflightValidation(
            False, "preflight artifact content hash does not match its contents"
        )
    created_at = _parse_timestamp(payload.get("created_at"))
    expires_at = _parse_timestamp(payload.get("expires_at"))
    if created_at is None or expires_at is None:
        return PreflightValidation(False, "preflight artifact timestamps malformed")
    if expires_at <= created_at:
        return PreflightValidation(False, "preflight artifact expiry is not after creation")
    if not (created_at <= now <= expires_at):
        return PreflightValidation(False, "preflight artifact is not fresh at validation time")
    if payload.get("state") not in {"PREFLIGHT_READY", "BLOCKED", "ABSTAIN"}:
        return PreflightValidation(False, "preflight artifact state is not recognized")
    if expected_candidate_id is not None and payload.get("candidate_id") != expected_candidate_id:
        return PreflightValidation(
            False, "preflight artifact is not bound to the expected candidate"
        )
    return PreflightValidation(True, None)


def _parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def _fresh(
    observed: datetime | None, now: datetime, window: timedelta = USER_DATA_FRESHNESS
) -> bool:
    if observed is None:
        return False
    if observed.tzinfo is None or observed.utcoffset() is None:
        return False
    age = now - observed.astimezone(UTC)
    return timedelta(0) <= age <= window


def _load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


# ---------------------------------------------------------------------------
# Book-depth / replay-consistency tradability (M27A replay, never reimplemented)
# ---------------------------------------------------------------------------


def _market_currentness(
    candidate: ExperimentalCandidate, economics: MarketEconomicsEvidence, now: datetime
) -> GateResult:
    """Independently re-derive book-depth tradability at consumption time.

    Reuses M27A's own :func:`replay_market_economics` -- never reimplements the fee/book-walk
    math -- to prove the evidence's stored taker costs are still exactly reproducible from its
    embedded book/fee inputs (tamper/consistency evidence), plus the same book-age bound M27D
    itself enforces, re-checked against the true current ``now``. This alone does not prove the
    *market itself* is still open/current -- see :func:`_public_market_evidence` for that half of
    ``market_tradable``.
    """
    if not _fresh(economics.orderbook_observed_at, now, MAX_BOOK_AGE):
        return GateResult(False, "orderbook evidence is stale at consumption time")
    try:
        replayed_yes, replayed_no = replay_market_economics(economics)
    except (AttributeError, TypeError) as exc:
        return GateResult(False, f"economics replay unavailable: {exc}")
    if (replayed_yes, replayed_no) != (economics.yes, economics.no):
        return GateResult(False, "economics evidence failed independent replay consistency")
    cost = economics.yes if candidate.selected_side is OutcomeSide.YES else economics.no
    tradable = (
        cost is not None
        and cost.depth.filled >= ONE_CONTRACT
        and cost.depth.worst_price is not None
        and cost.depth.worst_price == candidate.executable_price
    )
    reason = None if tradable else "current depth or price no longer supports the candidate"
    return GateResult(tradable, reason)


# ---------------------------------------------------------------------------
# M27E public evidence: exchange status + current market metadata (Gemini blockers 3, 9, 10)
# ---------------------------------------------------------------------------

_M27E_SCHEMA = "kalsh3.m27e.public-read.v1"
_M27E_HOST = "https://external-api.kalshi.com"
_EXCHANGE_STATUS_PATH = "/trade-api/v2/exchange/status"
_TRADABLE_MARKET_STATUSES = frozenset({"open"})


@dataclass(frozen=True, slots=True)
class _PublicMarketEvidence:
    exchange_active: GateResult
    trading_active: GateResult
    market_open_current: GateResult
    market_metadata_observed_at: datetime | None
    exchange_status_observed_at: datetime | None


def _exchange_status_gate(
    exchange_status: object, now: datetime
) -> tuple[GateResult, GateResult, datetime | None]:
    """Independently validate the exact reviewed ``exchange/status`` GET evidence shape.

    Never trusts a caller-fabricated ``{"trading_active": true}`` object: requires the exact
    fixed path, an HTTP 200 SUCCESS classification, a non-empty response body hash, and evidence
    freshness at consumption time, all reused directly from
    ``scripts/m27e_public_read_acceptance.py``'s own output schema -- never a second transport.
    """
    fallback = GateResult(False, "no fresh, valid public exchange-status evidence")
    if not isinstance(exchange_status, dict):
        return fallback, fallback, None
    if (
        exchange_status.get("classification") != "SUCCESS"
        or exchange_status.get("status") != 200
        or exchange_status.get("path") != _EXCHANGE_STATUS_PATH
        or not isinstance(exchange_status.get("body_sha256"), str)
        or not exchange_status.get("body_sha256")
    ):
        return fallback, fallback, None
    observed_at = _parse_timestamp(exchange_status.get("observed_at"))
    if observed_at is None or not _fresh(observed_at, now, EXCHANGE_STATUS_FRESHNESS):
        stale = GateResult(False, "public exchange-status evidence is stale")
        return stale, stale, observed_at
    payload = exchange_status.get("payload")
    if not isinstance(payload, dict):
        return fallback, fallback, observed_at
    exchange_active_value = payload.get("exchange_active")
    trading_active_value = payload.get("trading_active")
    if not isinstance(exchange_active_value, bool):
        exchange_active = GateResult(
            False, "public evidence does not report an exchange_active boolean"
        )
    else:
        exchange_active = GateResult(
            exchange_active_value, None if exchange_active_value else "exchange reports not active"
        )
    if not isinstance(trading_active_value, bool):
        trading_active = GateResult(
            False, "public evidence does not report a trading_active boolean"
        )
    else:
        trading_active = GateResult(
            trading_active_value,
            None if trading_active_value else "exchange reports trading inactive",
        )
    return exchange_active, trading_active, observed_at


def _market_open_gate(
    markets: object, candidate: ExperimentalCandidate, now: datetime
) -> tuple[GateResult, datetime | None]:
    """Independently locate the candidate's exact ticker in fresh, complete public market evidence.

    Reuses the same reviewed, fixed-origin, GET-only ``markets`` pagination evidence already
    produced by ``scripts/m27e_public_read_acceptance.py`` -- no new transport. Proves only the
    exact ticker/event pair is present, uniquely identified, and currently ``open`` as of a few
    seconds ago -- market *existence and status*, nothing more.

    This is deliberately NOT ``rules_current``: it never claims market rules text is
    byte-identical to what economics evidence assumed. Ticker/event/status existence is not a
    substitute for that authority -- see :mod:`services.supervised_canary.m27j` and
    :func:`_rules_current_gate`.
    """
    fallback = GateResult(False, "no fresh, valid public current-market evidence")
    if (
        not isinstance(markets, dict)
        or markets.get("classification") != "SUCCESS"
        or markets.get("pagination_complete") is not True
    ):
        return fallback, None
    pages = markets.get("pages")
    if not isinstance(pages, list) or not pages:
        return fallback, None
    observed_ats: list[datetime] = []
    entries: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict) or page.get("classification") != "SUCCESS":
            return fallback, None
        observed = _parse_timestamp(page.get("observed_at"))
        if observed is None:
            return fallback, None
        observed_ats.append(observed)
        page_payload = page.get("payload")
        page_markets = page_payload.get("markets") if isinstance(page_payload, dict) else None
        if not isinstance(page_markets, list):
            return fallback, None
        entries.extend(item for item in page_markets if isinstance(item, dict))
    oldest_observed = min(observed_ats)
    if not _fresh(oldest_observed, now, MAX_MARKET_METADATA_AGE):
        return GateResult(False, "public current-market evidence is stale"), oldest_observed
    matches = [item for item in entries if item.get("ticker") == candidate.market_ticker]
    if len(matches) != 1:
        reason = "candidate market not uniquely present in fresh current-market evidence"
        return GateResult(False, reason), oldest_observed
    market = matches[0]
    if market.get("event_ticker") != candidate.event_ticker:
        return GateResult(False, "current market evidence event ticker mismatch"), oldest_observed
    status = market.get("status")
    if status not in _TRADABLE_MARKET_STATUSES:
        reason = f"current market status is not tradable ({status!r})"
        return GateResult(False, reason), oldest_observed
    return GateResult(True, None), oldest_observed


def _public_market_evidence(
    public_evidence_path: Path | None, candidate: ExperimentalCandidate, now: datetime
) -> _PublicMarketEvidence:
    missing = GateResult(False, "no independent current-market authority evidence source exists")
    if public_evidence_path is None:
        return _PublicMarketEvidence(missing, missing, missing, None, None)
    raw = _load_json(public_evidence_path)
    if raw is None or raw.get("schema") != _M27E_SCHEMA or raw.get("host") != _M27E_HOST:
        malformed = GateResult(False, "public evidence is not the reviewed M27E acquisition schema")
        return _PublicMarketEvidence(malformed, malformed, malformed, None, None)
    exchange_active, trading_active, exchange_observed_at = _exchange_status_gate(
        raw.get("exchange_status"), now
    )
    market_open_current, market_observed_at = _market_open_gate(raw.get("markets"), candidate, now)
    return _PublicMarketEvidence(
        exchange_active,
        trading_active,
        market_open_current,
        market_observed_at,
        exchange_observed_at,
    )


def _rules_current_gate(
    m27j_payload: dict[str, Any] | None,
    m27a_binding_payload: dict[str, Any] | None,
    candidate: ExperimentalCandidate,
    economics: MarketEconomicsEvidence,
    now: datetime,
) -> GateResult:
    """Rules currentness -- requires BOTH an authoritative expected-side binding and fresh
    current-side M27J evidence. Never compares against a bare ``economics.market_rules_hash``
    string.

    Never re-derives or re-parses rules identity itself -- that duplication is exactly what the
    module docstring forbids. The expected side delegates to :func:`services.opportunity_engine.
    authoritative_economics.validate_authoritative_economics_market_binding`, which independently
    re-validates the binding's embedded market snapshot and cross-checks it against the live
    ``economics`` object in use (evidence_id, ticker/event, rules/metadata hash, orderbook source
    hash, price-range hash, and acquisition/evaluation timestamp ordering) -- never trusting the
    binding's own stamped fields in isolation. Only the rules hash *that validator itself
    independently re-derived* is ever used as the expected comparison target for the current
    side, via :func:`services.supervised_canary.m27j.validate_current_rules_for_candidate`.

    No binding supplied is fail-closed -- **even when a legacy, caller-fabricated**
    ``economics.market_rules_hash`` **happens to equal the current M27J hash**: that equality
    proves nothing about provenance, and using it to unlock this gate is exactly the "inventing
    authority, not consuming it" mistake the module docstring forbids. No M27J evidence, a stale
    artifact, a malformed artifact, or a rules hash that no longer matches all likewise fail
    closed; a caller-injected ``rules_current``/``rules_hash`` field anywhere is inert (both
    validators reject any unexpected field on their evidence payloads outright).
    """
    if m27a_binding_payload is None:
        return GateResult(False, NO_AUTHORITATIVE_CURRENT_RULES_IDENTITY)
    binding_result = validate_authoritative_economics_market_binding(
        m27a_binding_payload,
        economics=economics,
        expected_market_ticker=candidate.market_ticker,
        expected_event_ticker=candidate.event_ticker,
    )
    if not binding_result.succeeded:
        return GateResult(False, binding_result.reason)
    if m27j_payload is None:
        return GateResult(False, NO_AUTHORITATIVE_CURRENT_RULES_IDENTITY)
    expected_rules_hash = binding_result.rules_hash
    if expected_rules_hash is None:  # pragma: no cover - PASS always carries rules_hash
        return GateResult(False, "authoritative binding validation result incomplete")
    result = m27j.validate_current_rules_for_candidate(
        m27j_payload,
        expected_market_ticker=candidate.market_ticker,
        expected_event_ticker=candidate.event_ticker,
        expected_rules_hash=expected_rules_hash,
        now=now,
    )
    return GateResult(result.succeeded, None if result.succeeded else result.reason)


# ---------------------------------------------------------------------------
# Fee currentness (Gemini blocker 6)
# ---------------------------------------------------------------------------


def _fee_gate(
    economics: MarketEconomicsEvidence,
    series_observation: CurrentSeriesFeeObservation | None,
    event_override: EventFeeOverride | None,
    event_observed_at: datetime | None,
    now: datetime,
) -> GateResult:
    """Resolve the current fee regime here, from upstream authoritative evidence only.

    A bare :class:`ResolvedFeeRegime` carries no ``observed_at`` and can no longer unlock this
    gate by itself -- this module calls :func:`resolve_current_fee_regime` itself against a
    freshly-timestamped series observation and event override, exactly as the milestone
    requires, and requires the result to still match the candidate's economics evidence.
    """
    if series_observation is None or event_override is None or event_observed_at is None:
        return GateResult(False, "no current fee source evidence supplied")
    if not _fresh(series_observation.observed_at, now, FEE_FRESHNESS):
        return GateResult(False, "current series fee observation is stale")
    if not _fresh(event_observed_at, now, FEE_FRESHNESS):
        return GateResult(False, "current event fee metadata is stale")
    try:
        resolved = resolve_current_fee_regime(series_observation, event_override)
    except OpportunityError as exc:
        return GateResult(False, f"current fee regime unresolvable: {exc}")
    if resolved.regime_id != economics.resolved_fee_regime_id:
        return GateResult(False, "current fee regime no longer matches candidate economics")
    if resolved.series_observation_id != economics.series_fee_observation_id:
        return GateResult(
            False, "current series fee observation no longer matches candidate economics"
        )
    if resolved.event_metadata_hash != economics.event_fee_hash:
        return GateResult(False, "current event fee metadata no longer matches candidate economics")
    return GateResult(True, None)


# ---------------------------------------------------------------------------
# Model eligibility / candidate currentness (Gemini blocker 3)
# ---------------------------------------------------------------------------


def _model_eligible_gate(candidate: ExperimentalCandidate) -> GateResult:
    """Independently re-check the frozen identity fields M27D itself stamped on this candidate.

    Never re-derives model eligibility from raw weather evidence -- that duplication is
    exactly what the module docstring forbids. This instead defends against a tampered or
    drifted :class:`ExperimentalCanaryEligibility` record reaching this far by re-checking every
    frozen identity field it carries against the exact frozen constants M27D itself enforces.
    """
    eligibility = candidate.eligibility
    reasons: list[str] = []
    if eligibility.status != CANARY_STATUS or eligibility.human_approval_required is not True:
        reasons.append("candidate status is not the frozen supervised experimental canary status")
    if eligibility.claim_type != CLAIM_TYPE:
        reasons.append("candidate claim type is not the frozen GHCN physical-temperature proxy")
    if eligibility.settlement_mapping_status != SETTLEMENT_MAPPING_STATUS:
        reasons.append("candidate settlement mapping is not explicitly unvalidated GHCND proxy")
    if eligibility.model_identity not in FROZEN_MODEL_IDENTITIES.values():
        reasons.append("candidate model identity is not one of the three frozen identities")
    if eligibility.source_family != FAMILY:
        reasons.append("candidate source family mismatch")
    if not (AUGUST_START <= eligibility.target_date <= AUGUST_END):
        reasons.append("candidate target date is outside the August-only canary window")
    return GateResult(not reasons, "; ".join(reasons) if reasons else None)


def _candidate_current_gate(
    candidate: ExperimentalCandidate,
    probability: PhysicalTemperatureProxyProbability,
    forecast: CurrentWeatherForecastEvidence,
    economics: MarketEconomicsEvidence,
) -> GateResult:
    """Independently re-bind the selected candidate to the exact evidence triple in use now.

    Never re-derives selection/ranking -- only proves nothing has drifted between what M27D
    selected and the exact ``(probability, forecast, economics)`` currently being consumed.
    """
    reasons: list[str] = []
    if candidate.economics_evidence_identity != economics.evidence_id:
        reasons.append("candidate economics evidence identity binding mismatch")
    if candidate.market_ticker != economics.market_ticker:
        reasons.append("candidate market ticker binding mismatch")
    if candidate.event_ticker != economics.event_ticker:
        reasons.append("candidate event ticker binding mismatch")
    if candidate.series_ticker != economics.series_ticker:
        reasons.append("candidate series ticker binding mismatch")
    if candidate.eligibility.forecast_evidence_identity != forecast.evidence_identity:
        reasons.append("candidate forecast evidence identity binding mismatch")
    if candidate.eligibility.model_identity != probability.model_identity:
        reasons.append("candidate model identity binding mismatch")
    if candidate.eligibility.target_date != forecast.local_target_date:
        reasons.append("candidate target date binding mismatch")
    if candidate.eligibility.weather_result_identity != probability.result_identity:
        reasons.append("candidate weather result identity binding mismatch")
    if economics.requested_quantity != ONE_CONTRACT:
        reasons.append("candidate economics requested quantity is not exactly one contract")
    return GateResult(not reasons, "; ".join(reasons) if reasons else None)


# ---------------------------------------------------------------------------
# Account / M27F / M27H gates (unchanged from the reviewed acceptance boundary)
# ---------------------------------------------------------------------------


def _account_gates(
    *,
    m27f_evidence_path: Path | None,
    m27h_evidence_path: Path | None,
    public_evidence_path: Path | None,
    now: datetime,
) -> dict[str, GateResult]:
    evidence = operator_evidence(
        public_evidence=public_evidence_path,
        live_read_evidence=m27f_evidence_path,
        write_credential_evidence=m27h_evidence_path,
        now=now,
    )

    def gate(name: str) -> GateResult:
        status, reason = evidence.get(name, ("NOT TESTED", "no evidence supplied"))
        return GateResult(status == "PASS", None if status == "PASS" else reason)

    reads_ok = all(
        gate(name).passed
        for name in (
            "AUTHENTICATED_PRODUCTION_BALANCE",
            "AUTHENTICATED_OPEN_ORDERS",
            "AUTHENTICATED_POSITIONS",
            "AUTHENTICATED_FILLS",
            "AUTHENTICATED_SETTLEMENTS",
        )
    )
    reconciliation = gate("ACCOUNT_RECONCILIATION")
    return {
        "production_read_live_verified": GateResult(
            gate("CANDIDATE_KEY_AUTHENTICATED_GET").passed and reads_ok,
            None if reads_ok else "one or more authenticated M27F reads did not pass",
        ),
        "real_reconciliation_live_verified": reconciliation,
        "account_reconciled": reconciliation,
        "read_budget_safe": GateResult(
            reconciliation.passed,
            None if reconciliation.passed else "read sweep did not fully succeed",
        ),
        "write_credential_installed": gate("PRODUCTION_WRITE_CREDENTIAL"),
        "signer_runtime_live_verified": gate("REAL_SIGNER_VALIDATION"),
    }


def _candidate_exposure_gates(
    candidate: ExperimentalCandidate, exposure: CandidateExposureEvidence | None, now: datetime
) -> dict[str, GateResult]:
    if exposure is None:
        reason = "no candidate-specific exposure evidence supplied"
        return {
            "no_unknown_orders": GateResult(False, reason),
            "no_unknown_positions": GateResult(False, reason),
        }
    if exposure.market_ticker != candidate.market_ticker:
        reason = "candidate exposure evidence is bound to a different market ticker"
        return {
            "no_unknown_orders": GateResult(False, reason),
            "no_unknown_positions": GateResult(False, reason),
        }
    if not exposure.succeeded:
        reason = exposure.reason or "candidate exposure evidence did not pass"
        return {
            "no_unknown_orders": GateResult(False, reason),
            "no_unknown_positions": GateResult(False, reason),
        }
    if not _fresh(exposure.completed_at, now):
        reason = "candidate exposure evidence is stale"
        return {
            "no_unknown_orders": GateResult(False, reason),
            "no_unknown_positions": GateResult(False, reason),
        }
    orders_ok = exposure.open_order_count == 0
    positions_ok = exposure.position_nonzero is False
    return {
        "no_unknown_orders": GateResult(
            orders_ok, None if orders_ok else "open order exists for this market"
        ),
        "no_unknown_positions": GateResult(
            positions_ok, None if positions_ok else "nonzero position exists for this market"
        ),
    }


# ---------------------------------------------------------------------------
# M13 -- current-state binding, hash re-derivation, and its own expiry contract
# (Gemini blockers 1, 2)
# ---------------------------------------------------------------------------

_M27F_READ_NAMES = ("balance", "orders", "positions", "fills", "settlements")


def _canonical_hash(material: dict[str, Any]) -> str:
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def compute_account_snapshot_version(m27f_payload: dict[str, Any]) -> str:
    """Deterministic identity of the exact fresh M27F account evidence in use right now.

    Derived from the exact ``key_id_hash`` plus each portfolio-read's exact ``payload_sha256``
    -- never from counts or caller labels alone. Any caller producing a
    :class:`PortfolioRiskSnapshot` for M27I consumption must stamp its
    ``account_snapshot_version`` with exactly this value so M27I can independently re-derive and
    confirm it, rather than trust it.
    """
    reads = m27f_payload.get("reads")
    by_name = (
        {item.get("name"): item for item in reads if isinstance(item, dict)}
        if isinstance(reads, list)
        else {}
    )
    material = {
        "key_id_hash": m27f_payload.get("key_id_hash"),
        "subaccount": m27f_payload.get("subaccount"),
        **{
            f"{name}_payload_sha256": (by_name.get(name) or {}).get("payload_sha256")
            for name in _M27F_READ_NAMES
        },
    }
    return _canonical_hash(material)


def compute_reconciliation_version(
    m27f_payload: dict[str, Any], exposure: CandidateExposureEvidence
) -> str:
    """Deterministic identity of fresh reconciliation + candidate-exposure state right now.

    Combines the M27F reconciliation booleans with the exact candidate-specific exposure
    identity (never counts alone) so a snapshot can only bind to *this* market's exposure
    evidence, not merely "some" exposure check having once passed.
    """
    reconciliation = m27f_payload.get("reconciliation")
    reconciliation = reconciliation if isinstance(reconciliation, dict) else {}
    material = {
        "balance_succeeded": reconciliation.get("balance_succeeded"),
        "open_orders_complete": reconciliation.get("open_orders_complete"),
        "positions_complete": reconciliation.get("positions_complete"),
        "fills_complete": reconciliation.get("fills_complete"),
        "settlements_complete": reconciliation.get("settlements_complete"),
        "subaccount_binding_verified": reconciliation.get("subaccount_binding_verified"),
        "candidate_market_ticker": exposure.market_ticker,
        "candidate_open_order_count": exposure.open_order_count,
        "candidate_position_nonzero": exposure.position_nonzero,
        "candidate_exposure_classification": exposure.classification,
    }
    return _canonical_hash(material)


def _recompute_domain_hash(obj: Any, *, exclude: frozenset[str]) -> str:
    values = {
        field.name: getattr(obj, field.name)
        for field in dataclass_fields(obj)
        if field.name not in exclude
    }
    return _risk_content_hash(values)


def _hash_intact(obj: Any, *, hash_fields: tuple[str, ...]) -> bool:
    exclude = frozenset(hash_fields)
    digest = _recompute_domain_hash(obj, exclude=exclude)
    return all(getattr(obj, field) == digest for field in hash_fields)


def _m13_gate(
    *,
    candidate: ExperimentalCandidate,
    risk_decision: RiskDecision | None,
    risk_intent: RiskIntent | None,
    risk_snapshot: PortfolioRiskSnapshot | None,
    m27f_payload: dict[str, Any] | None,
    candidate_exposure: CandidateExposureEvidence | None,
    authorization_store: AuthorizationStore,
    now: datetime,
) -> tuple[GateResult, GateResult, GateResult, GateResult]:
    """Independently re-bind and re-check a fresh, already-produced M13 risk pass.

    Never recomputes exposure/reserve/loss-limit math -- that remains exclusively
    :func:`services.risk_engine.engine.evaluate_risk`'s job, run out of band by the caller with
    real current portfolio data. This proves the supplied ``(intent, snapshot, decision)`` triple
    (a) has content hashes that independently re-derive to what is stamped on each object -- a
    caller can hash lies too; (b) actually passed; (c) is bound by hash to an intent and
    portfolio snapshot whose economically material fields match this exact candidate and the
    exact fresh current account evidence in use right now (never merely copied strings); (d) is
    still inside its own real 5-second ``decided_at``/``expires_at`` expiry contract, not a
    generic 30-second rule; and (e) global halt/kills/compliance are clear *right now* via a
    real, read-only :class:`AuthorizationStore` query -- never a cached claim.
    """
    summary = authorization_store.safety_summary()
    halt_active = bool(summary["global_halt"])
    halt_clear = GateResult(not halt_active, "global halt active" if halt_active else None)
    compliance_state = summary["compliance_state"]
    compliance_ok = compliance_state == "CLEAR"
    compliance_clear = GateResult(
        compliance_ok, None if compliance_ok else f"compliance state is {compliance_state}"
    )
    kill_rows = summary["kill_states"]
    if not isinstance(kill_rows, tuple):
        raise TypeError("safety summary kill_states is malformed")
    kills_ok = all(row[1] == "NORMAL" for row in kill_rows)
    kills_clear = GateResult(
        kills_ok, None if kills_ok else "one or more kill switches are not NORMAL"
    )

    if risk_decision is None or risk_intent is None or risk_snapshot is None:
        return (
            GateResult(False, "no fresh M13 risk decision/intent/snapshot supplied"),
            halt_clear,
            compliance_clear,
            kills_clear,
        )

    reasons: list[str] = []
    if not _hash_intact(risk_intent, hash_fields=("content_hash",)):
        reasons.append("risk intent content hash failed independent recomputation")
    if not _hash_intact(risk_snapshot, hash_fields=("content_hash",)):
        reasons.append("risk snapshot content hash failed independent recomputation")
    if not _hash_intact(risk_decision, hash_fields=("content_hash", "decision_id")):
        reasons.append("risk decision content hash failed independent recomputation")

    if risk_decision.state != RiskDecisionState.PASS_NEXT_GATE or risk_decision.reasons:
        reasons.append("risk decision did not cleanly pass")
    if risk_decision.intent_hash != risk_intent.content_hash:
        reasons.append("risk decision is not bound to the supplied intent")
    if risk_decision.portfolio_state_hash != risk_snapshot.content_hash:
        reasons.append("risk decision is not bound to the supplied portfolio snapshot")
    if risk_decision.reconciliation_version != risk_snapshot.reconciliation_version:
        reasons.append("risk decision reconciliation version does not match its own snapshot")
    if risk_intent.candidate_id != candidate.candidate_id:
        reasons.append("risk intent is bound to a different candidate")
    if risk_intent.forecast_id != candidate.eligibility.weather_result_identity:
        reasons.append("risk intent forecast binding mismatch")
    if risk_intent.market_ticker != candidate.market_ticker:
        reasons.append("risk intent market binding mismatch")
    if risk_intent.outcome_side != candidate.selected_side.value:
        reasons.append("risk intent side binding mismatch")
    if risk_intent.price != candidate.executable_price:
        reasons.append("risk intent price binding mismatch")
    if risk_intent.quantity != ONE_CONTRACT:
        reasons.append("risk intent quantity is not exactly one contract")
    if risk_intent.maximum_loss_if_filled != candidate.maximum_loss:
        reasons.append("risk intent maximum loss binding mismatch")
    if risk_intent.subaccount != 0:
        reasons.append("risk intent is not bound to subaccount 0")

    # M13's own real expiry contract: decided_at + 5 seconds, inclusive at both boundaries --
    # never a generic 30-second age rule.
    decided_at, expires_at = risk_decision.decided_at, risk_decision.expires_at
    if decided_at.tzinfo is None or expires_at.tzinfo is None:
        reasons.append("risk decision timestamps are not timezone-aware")
    elif not (decided_at <= now <= expires_at):
        reasons.append("risk decision is outside its own expiry contract")

    observed_at = risk_snapshot.observed_at
    if observed_at.tzinfo is None:
        reasons.append("portfolio snapshot observed_at is not timezone-aware")
    elif observed_at > decided_at:
        reasons.append("portfolio snapshot was observed after the risk decision")
    if not risk_snapshot.account_fresh:
        reasons.append("portfolio snapshot reports account data is not fresh")

    if m27f_payload is None:
        reasons.append("no fresh M27F account evidence supplied to bind the current account state")
    elif candidate_exposure is None:
        reasons.append("no candidate exposure evidence supplied to bind the current account state")
    else:
        expected_account_version = compute_account_snapshot_version(m27f_payload)
        expected_reconciliation_version = compute_reconciliation_version(
            m27f_payload, candidate_exposure
        )
        if risk_snapshot.account_snapshot_version != expected_account_version:
            reasons.append(
                "portfolio snapshot account_snapshot_version does not match current M27F evidence"
            )
        if risk_snapshot.reconciliation_version != expected_reconciliation_version:
            reasons.append(
                "portfolio snapshot reconciliation_version does not match current "
                "reconciliation/candidate-exposure evidence"
            )
        m27f_completed_at = _parse_timestamp(m27f_payload.get("completed_at"))
        if m27f_completed_at is None:
            reasons.append("current M27F account evidence timestamp malformed")
        elif m27f_completed_at > decided_at:
            reasons.append("current account evidence was acquired after the risk decision")

    if not (halt_clear.passed and compliance_clear.passed and kills_clear.passed):
        reasons.append("safety state is not clear at consumption time")
    loss_state_version = summary.get("loss_state_version")
    if loss_state_version is not None and risk_decision.loss_state_version != loss_state_version:
        reasons.append("risk decision loss-state version does not match current safety state")

    m13 = GateResult(not reasons, "; ".join(reasons) if reasons else None)
    return m13, halt_clear, compliance_clear, kills_clear


# ---------------------------------------------------------------------------
# Preflight expiry -- minimum of every underlying evidence deadline (Gemini blocker 2)
# ---------------------------------------------------------------------------


def _compute_expiry(
    *,
    now: datetime,
    candidate: ExperimentalCandidate,
    forecast: CurrentWeatherForecastEvidence,
    economics: MarketEconomicsEvidence,
    risk_decision: RiskDecision | None,
    m27f_payload: dict[str, Any] | None,
    m27h_payload: dict[str, Any] | None,
    exposure: CandidateExposureEvidence | None,
    market_metadata_observed_at: datetime | None,
    exchange_status_observed_at: datetime | None,
    series_fee_observation: CurrentSeriesFeeObservation | None,
    event_fee_observed_at: datetime | None,
    m27j_payload: dict[str, Any] | None,
) -> datetime:
    deadlines: list[datetime] = [now + PREFLIGHT_TTL, candidate.eligibility.expires_at]
    deadlines.append(economics.orderbook_observed_at + MAX_BOOK_AGE)
    deadlines.append(forecast.forecast_reference_time + MAX_FORECAST_AGE)
    if risk_decision is not None:
        deadlines.append(risk_decision.expires_at)
    if m27f_payload is not None:
        completed = _parse_timestamp(m27f_payload.get("completed_at"))
        if completed is not None:
            deadlines.append(completed + USER_DATA_FRESHNESS)
    if m27h_payload is not None:
        completed = _parse_timestamp(m27h_payload.get("completed_at"))
        if completed is not None:
            deadlines.append(completed + M27H_EVIDENCE_FRESHNESS)
        signer_completed = _parse_timestamp(m27h_payload.get("signer_completed_at"))
        if signer_completed is not None:
            deadlines.append(signer_completed + M27H_EVIDENCE_FRESHNESS)
    if exposure is not None:
        deadlines.append(exposure.completed_at + USER_DATA_FRESHNESS)
    if market_metadata_observed_at is not None:
        deadlines.append(market_metadata_observed_at + MAX_MARKET_METADATA_AGE)
    if exchange_status_observed_at is not None:
        deadlines.append(exchange_status_observed_at + EXCHANGE_STATUS_FRESHNESS)
    if series_fee_observation is not None:
        deadlines.append(series_fee_observation.observed_at + FEE_FRESHNESS)
    if event_fee_observed_at is not None:
        deadlines.append(event_fee_observed_at + FEE_FRESHNESS)
    if m27j_payload is not None:
        # The preflight can never extend the rules proof: cap to M27J's own stamped expiry
        # (never a fresh window computed here) if that field parses at all.
        m27j_expires = _parse_timestamp(m27j_payload.get("expires_at"))
        if m27j_expires is not None:
            deadlines.append(m27j_expires)
    return min(deadlines)


@dataclass(frozen=True, slots=True)
class PreflightResult:
    artifact: PreflightArtifact


def _abstain_artifact(now: datetime, reason: str) -> PreflightArtifact:
    return PreflightArtifact(
        SCHEMA,
        SOFTWARE_VERSION,
        now,
        now + PREFLIGHT_TTL,
        "ABSTAIN",
        reason,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        PreflightGates({}),
        (),
        None,
    )


def build_preflight(
    *,
    now: datetime,
    candidate_inputs: Sequence[_CandidateInput],
    m27f_evidence_path: Path | None = None,
    m27h_evidence_path: Path | None = None,
    public_evidence_path: Path | None = None,
    m27j_evidence_path: Path | None = None,
    m27a_binding_evidence_path: Path | None = None,
    current_series_fee_observation: CurrentSeriesFeeObservation | None = None,
    current_event_fee_override: EventFeeOverride | None = None,
    current_event_fee_observed_at: datetime | None = None,
    candidate_exposure: CandidateExposureEvidence | None = None,
    risk_decision: RiskDecision | None = None,
    risk_intent: RiskIntent | None = None,
    risk_snapshot: PortfolioRiskSnapshot | None = None,
    authorization_store: AuthorizationStore,
    canary_store: CanaryStore,
) -> PreflightResult:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("preflight clock must be timezone-aware")

    candidate_result: CandidateResult = select_experimental_candidate(candidate_inputs, now=now)
    if candidate_result.state is CandidateState.ABSTAIN or candidate_result.selected is None:
        abstain_reason = _diagnose_market_discovery(candidate_inputs, now)
        return PreflightResult(_abstain_artifact(now, abstain_reason))

    # Exactly one currently qualifying candidate, enforced literally (Gemini blocker 5). M27D's
    # own ranking is untouched and may still order multiple candidates for its own purposes --
    # M27I simply refuses to present a live-money candidate when more than one qualifies.
    if len(candidate_result.candidates) > 1:
        return PreflightResult(
            _abstain_artifact(now, AbstentionReason.MULTIPLE_QUALIFYING_CANDIDATES)
        )
    if candidate_result.selected != candidate_result.candidates[0]:
        return PreflightResult(
            _abstain_artifact(now, AbstentionReason.MULTIPLE_QUALIFYING_CANDIDATES)
        )

    candidate = candidate_result.selected
    target_economics_id = candidate.economics_evidence_identity
    probability, forecast, economics = next(
        item for item in candidate_inputs if item[2].evidence_id == target_economics_id
    )

    m27f_payload = _load_json(m27f_evidence_path)
    m27h_payload = _load_json(m27h_evidence_path)
    m27j_payload = _load_json(m27j_evidence_path)
    m27a_binding_payload = _load_json(m27a_binding_evidence_path)

    account_gates = _account_gates(
        m27f_evidence_path=m27f_evidence_path,
        m27h_evidence_path=m27h_evidence_path,
        public_evidence_path=public_evidence_path,
        now=now,
    )
    exposure_gates = _candidate_exposure_gates(candidate, candidate_exposure, now)

    public_evidence = _public_market_evidence(public_evidence_path, candidate, now)
    book_executable = _market_currentness(candidate, economics, now)
    market_open_current = public_evidence.market_open_current
    # "Open/executable now" (``market_tradable``) is deliberately kept separate from "rules
    # currently verified" (``rules_current``, see ``_rules_current_gate``): the latter is
    # independently provable only when BOTH the authoritative expected-side economics binding
    # validates and fresh M27J current-side evidence validates and matches it. Do NOT fold
    # ``rules_current`` into this: that is exactly the false-authority alias Gemini's FINAL delta
    # review rejected.
    market_tradable = GateResult(
        book_executable.passed and market_open_current.passed,
        book_executable.reason if not book_executable.passed else market_open_current.reason,
    )
    rules_current = _rules_current_gate(
        m27j_payload, m27a_binding_payload, candidate, economics, now
    )

    fee_verified = _fee_gate(
        economics,
        current_series_fee_observation,
        current_event_fee_override,
        current_event_fee_observed_at,
        now,
    )
    m13_verified, global_halt_clear, compliance_clear, kills_clear = _m13_gate(
        candidate=candidate,
        risk_decision=risk_decision,
        risk_intent=risk_intent,
        risk_snapshot=risk_snapshot,
        m27f_payload=m27f_payload,
        candidate_exposure=candidate_exposure,
        authorization_store=authorization_store,
        now=now,
    )
    write_budget_used = canary_store.submission_budget_used()
    unresolved_canary = canary_store.unresolved_canary_present()

    source_bound = (
        forecast.evidence_identity == candidate.eligibility.forecast_evidence_identity
        and economics.evidence_id == candidate.eligibility.market_evidence_identity
    )
    clock_safe = (
        forecast.forecast_reference_time <= now
        and economics.orderbook_observed_at <= now
        and economics.economics_observed_at <= now
    )

    results: dict[str, GateResult] = {
        **account_gates,
        "model_eligible": _model_eligible_gate(candidate),
        "source_evidence_ready": GateResult(
            source_bound, None if source_bound else "candidate evidence identities do not bind"
        ),
        "compliance_clear": compliance_clear,
        "global_halt_clear": global_halt_clear,
        "kills_clear": kills_clear,
        "exchange_active": public_evidence.exchange_active,
        "trading_active": public_evidence.trading_active,
        "market_open_current": market_open_current,
        "book_executable": book_executable,
        "market_tradable": market_tradable,
        "rules_current": rules_current,
        "candidate_current": _candidate_current_gate(candidate, probability, forecast, economics),
        "fee_verified": fee_verified,
        **exposure_gates,
        "clock_safe": GateResult(
            clock_safe, None if clock_safe else "evidence timestamp precedes consumption time"
        ),
        "write_budget_safe": GateResult(
            not write_budget_used,
            None if not write_budget_used else "global one-order canary budget already used",
        ),
        "m13_verified": m13_verified,
    }
    if unresolved_canary and results["no_unknown_positions"].passed:
        results["no_unknown_positions"] = GateResult(
            False, "an unresolved canary session already exists"
        )

    gates = PreflightGates(results)
    ready = gates.all_pass
    expires_at = _compute_expiry(
        now=now,
        candidate=candidate,
        forecast=forecast,
        economics=economics,
        risk_decision=risk_decision,
        m27f_payload=m27f_payload,
        m27h_payload=m27h_payload,
        exposure=candidate_exposure,
        market_metadata_observed_at=public_evidence.market_metadata_observed_at,
        exchange_status_observed_at=public_evidence.exchange_status_observed_at,
        series_fee_observation=current_series_fee_observation,
        event_fee_observed_at=current_event_fee_observed_at,
        m27j_payload=m27j_payload,
    )
    if expires_at <= now:
        ready = False

    artifact = PreflightArtifact(
        SCHEMA,
        SOFTWARE_VERSION,
        now,
        expires_at,
        "PREFLIGHT_READY" if ready else "BLOCKED",
        None,
        candidate.candidate_id,
        candidate.market_ticker,
        candidate.event_ticker,
        candidate.eligibility.target_date.isoformat(),
        candidate.selected_side.value,
        str(candidate.executable_price),
        str(candidate.maximum_fee),
        str(candidate.maximum_commitment),
        str(candidate.maximum_loss),
        str(probability.probability),
        str(candidate.research_probability_discrepancy),
        candidate.eligibility.model_identity,
        candidate.eligibility.forecast_evidence_identity,
        candidate.economics_evidence_identity,
        gates,
        gates.missing,
        candidate.truth_warning,
    )
    return PreflightResult(artifact)


def render_preflight(artifact: PreflightArtifact) -> str:
    lines = [
        "M27I LIVE WEATHER CANARY PREFLIGHT",
        f"schema: {artifact.schema}",
        f"state: {artifact.state}",
        # Scoped strictly to what this renderer itself can prove about THIS operation --
        # render_preflight never independently inspects or proves global production arm state,
        # so it must never claim one (no ``production_state``/``PRODUCTION_ARMED``/
        # ``PRODUCTION_WRITE_CREDENTIAL`` line here). These five lines are M27I's own reviewed,
        # in-module guarantee: read-only, no arm action, no mutation, no canary-burn consumption,
        # no final-acknowledgement consumption.
        "M27I_REQUEST_TYPE: READ_ONLY",
        "M27I_ARM_ACTION: NONE",
        "M27I_MUTATION: NO",
        "M27I_CANARY_BURN_ACTION: NONE",
        "M27I_FINAL_ACK_ACTION: NONE",
    ]
    if artifact.state == "ABSTAIN":
        lines.append(f"reason: {artifact.abstain_reason}")
        return "\n".join(lines)
    lines.extend(
        [
            f"market_ticker: {artifact.market_ticker}",
            f"target_date: {artifact.target_date}",
            f"side: {artifact.selected_side}",
            f"executable_price: {artifact.executable_price}",
            f"maximum_fee: {artifact.maximum_fee}",
            f"maximum_commitment: {artifact.maximum_commitment}",
            f"maximum_loss: {artifact.maximum_loss}",
            f"proxy_probability: {artifact.proxy_probability}",
            f"research_discrepancy: {artifact.research_probability_discrepancy}",
            f"model_identity: {artifact.model_identity}",
            "WARNING: UNVALIDATED GHCND SETTLEMENT PROXY",
            f"expires_at: {artifact.expires_at.isoformat()}",
        ]
    )
    # Rendered explicitly and separately -- never collapsed into one line -- so a human reading
    # this output can never conflate "the exchange currently reports this market open/executable"
    # (``market_open_current``/``book_executable``, folded into ``market_tradable``) with
    # "current rules identity is verified" (``rules_current``). ``rules_current`` is
    # independently provable only when BOTH the authoritative expected-side economics binding
    # validates (see :func:`_rules_current_gate`/:func:`services.opportunity_engine.
    # authoritative_economics.validate_authoritative_economics_market_binding`) AND fresh M27J
    # current-side evidence validates and matches that binding's independently re-derived rules
    # hash (see :func:`services.supervised_canary.m27j.validate_current_rules_for_candidate`) --
    # absence, failure, staleness, or a mismatch on either side still blocks ``rules_current``.
    # A BLOCKED result here must never be misread as "market unavailable".
    gate_results = artifact.gates.results
    market_open = gate_results.get("market_open_current")
    if market_open is not None:
        lines.append(f"MARKET OPEN/CURRENT: {'PASS' if market_open.passed else 'BLOCKED'}")
    book_executable = gate_results.get("book_executable")
    if book_executable is not None:
        lines.append(f"BOOK EXECUTABLE: {'PASS' if book_executable.passed else 'BLOCKED'}")
    rules_current = gate_results.get("rules_current")
    if rules_current is not None:
        status = "PASS" if rules_current.passed else "BLOCKED"
        suffix = f" -- {rules_current.reason}" if rules_current.reason else ""
        lines.append(f"RULES CURRENTNESS: {status}{suffix}")
    if artifact.state != "PREFLIGHT_READY":
        lines.append(f"missing_gates: {', '.join(artifact.missing_gates)}")
    return "\n".join(lines)
