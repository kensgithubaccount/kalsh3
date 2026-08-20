"""M27N.1 -- OFFLINE adapter: persisted live evidence -> M27N rehearsal fixtures.

This module is a separate, additive counterpart to the frozen
:mod:`services.supervised_canary.m27n_weather_rehearsal` core -- it never modifies that module,
never adds networking or credential access to it, and never touches its CLI
(``scripts/run_m27n_weather_execution_rehearsal.py``), which remains a pure OFFLINE fixture
scenario builder exactly as reviewed.

What this module does: it takes already-produced, persisted evidence artifacts (the JSON a
live, already-reviewed gate wrote to disk in some earlier, separate run -- M27J's current-rules
snapshot, M27F/M27H's account/credential evidence, M27I's candidate-exposure evidence, a M13
risk quadruple) and independently re-validates/deserializes each one into the exact
``*Fixture``/domain object types
:func:`services.supervised_canary.m27n_weather_rehearsal.build_rehearsal` requires, so that a
caller can feed it real, re-checked evidence instead of hand-built OFFLINE fixtures.

Every security-relevant claim is recomputed or delegated to the existing validator for that
evidence type -- this module never trusts a caller-supplied "gate passed" boolean:

* current rules identity  ->
  :func:`services.supervised_canary.m27j.validate_current_rules_for_candidate` (which itself
  delegates to :func:`services.market_universe.market_snapshot.validate_market_snapshot`)
* account/reconciliation + M27H credential/signer evidence
  -> :func:`services.supervised_canary.readiness_report.operator_evidence`, which internally
  calls one function in
  :mod:`services.production_execution.installed_credential_verification` that independently
  re-derives both flags rather than trusting the artifact's own classification
* candidate-specific exposure -> reconstructed through
  :class:`services.supervised_canary.candidate_exposure_check.CandidateExposureEvidence` itself,
  whose ``succeeded`` property is always *derived* from ``classification`` -- this module never
  reads a raw "succeeded"/"passed" boolean out of a payload (that schema has none)
* M13 risk quadruple -> :meth:`services.risk_engine.domain.RiskIntent.freeze`,
  :meth:`services.risk_engine.domain.PortfolioRiskSnapshot.freeze`,
  :meth:`services.risk_engine.domain.RiskDecision.freeze` (each independently recomputes its own
  ``content_hash``/``decision_id`` from every other field and runs the type's own
  ``__post_init__`` validation); ``RiskAuthorization`` is reconstructed field-by-field and its
  ``authorization_id`` is independently re-derived and cross-checked downstream by
  ``m27n_weather_rehearsal``'s own ``m13_authorization_fresh``/``m13_authorization_bound`` gates,
  which this module never duplicates or weakens

No network, no credentials, no signer, no SQLite store is opened anywhere in this module. It
imports (but never calls the network-capable functions inside) a few modules that
``m27n_weather_rehearsal`` itself deliberately avoids importing precisely so that module could
keep a zero-transport-import-graph AST proof -- this module is the intentional, separate place
that inspection now lives, exactly as ``m27n_weather_rehearsal``'s own docstring anticipates
("a caller who wants that additional live ... check should run M27I itself, separately").

Known, disclosed gap (per M27N.1's own charter: report a missing safe boundary, never invent a
weaker one): there is no existing validating deserializer anywhere in this repository that turns
one persisted JSON evidence bundle into the
``(PhysicalTemperatureProxyProbability, CurrentWeatherForecastEvidence, MarketEconomicsEvidence)``
triple that ``select_experimental_candidate``/``build_rehearsal`` require as ``candidate_inputs``.
Each of those three types has its own strict validating builder
(:func:`services.forecasting.weather_probability.physical_temperature_proxy_probability`,
:func:`services.forecasting.weather_probability.build_current_weather_forecast_evidence`,
:meth:`services.opportunity_engine.live_economics.MarketEconomicsEvidence.create`), but each
takes typed intermediate objects (``RawGribEvidence``, ``WeatherResidualPopulation``,
``DailyTemperatureRoute``, ``MarketEconomicsReplayInput``/``TakerCost``/``NormalizedBook``), not
raw JSON, and nothing in this repository composes them from one on-disk artifact today. This
module therefore does NOT build ``candidate_inputs`` -- callers of
:func:`run_m27n1_rehearsal` must supply an already-validated sequence themselves, built through
each type's own existing constructor. Composing a safe single-artifact boundary for that triple
is future work, not something this module fabricates.

Two fixture fields are, by the existing architecture's own design, not independently
recomputable by *any* OFFLINE module without opening a SQLite store (which this milestone must
not do): ``M13Fixture.global_halt_clear``/``compliance_clear``/``kills_clear`` (authority is
``services.risk_engine.authorization.AuthorizationStore.safety_summary()``) and
``SubmissionBudgetFixture``'s two fields (authority is
``services.supervised_canary.store.CanaryStore``). This module accepts them as explicit,
already-produced booleans supplied by the caller -- it never fabricates or defaults them, and
raises if a caller passes anything but an actual ``bool``.
"""

from __future__ import annotations

import dataclasses
import json
import typing
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any

from services.forecasting.weather_probability import (
    CurrentWeatherForecastEvidence,
    PhysicalTemperatureProxyProbability,
)
from services.market_universe.market_snapshot import validate_market_snapshot
from services.opportunity_engine.live_economics import MarketEconomicsEvidence
from services.risk_engine.authorization import RiskAuthorization
from services.risk_engine.domain import (
    PortfolioRiskSnapshot,
    RiskDecision,
    RiskDomainError,
    RiskIntent,
)

from .candidate_exposure_check import (
    SCHEMA as _CANDIDATE_EXPOSURE_SCHEMA,
)
from .candidate_exposure_check import (
    CandidateExposureEvidence,
)
from .m27i import compute_account_snapshot_version, compute_reconciliation_version
from .m27j import validate_current_rules_for_candidate
from .m27n_weather_rehearsal import (
    AccountSnapshotFixture,
    CandidateExposureFixture,
    M13Fixture,
    RehearsalResult,
    RulesIdentityFixture,
    SubmissionBudgetFixture,
    build_rehearsal,
)
from .readiness_report import operator_evidence

_CandidateInput = tuple[
    PhysicalTemperatureProxyProbability, CurrentWeatherForecastEvidence, MarketEconomicsEvidence
]

_ACCOUNT_READ_GATES = (
    "AUTHENTICATED_PRODUCTION_BALANCE",
    "AUTHENTICATED_OPEN_ORDERS",
    "AUTHENTICATED_POSITIONS",
    "AUTHENTICATED_FILLS",
    "AUTHENTICATED_SETTLEMENTS",
)

_CANDIDATE_EXPOSURE_FIELDS = frozenset(CandidateExposureEvidence.__dataclass_fields__)


class AdapterError(ValueError):
    """A persisted evidence artifact failed independent M27N.1 revalidation/deserialization."""


# ---------------------------------------------------------------------------
# Generic JSON -> typed value coercion. Coercion only maps JSON's limited type
# vocabulary (str/int/float/bool/list/dict/None) onto the exact declared field type; it never
# performs the actual security validation -- that always happens in the type's own constructor
# (``.freeze()``'s hash recomputation and ``__post_init__``, or a dedicated ``validate_*``
# function) once coercion hands off a properly-typed value.
# ---------------------------------------------------------------------------


def _require_utc_datetime(raw: object, *, field: str) -> datetime:
    if not isinstance(raw, str):
        raise AdapterError(f"{field} is not a string")
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise AdapterError(f"{field} is not a valid ISO-8601 timestamp") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise AdapterError(f"{field} is not timezone-aware")
    return value.astimezone(UTC)


def _require_decimal(raw: object, *, field: str) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        raise AdapterError(f"{field} is not a Decimal-safe string/int")
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise AdapterError(f"{field} is not a valid decimal") from exc
    if not value.is_finite():
        raise AdapterError(f"{field} is not finite")
    return value


def _coerce(raw: object, annotation: object, *, field: str) -> object:
    args = typing.get_args(annotation)
    if type(None) in args:
        if raw is None:
            return None
        remaining = [a for a in args if a is not type(None)]
        return _coerce(raw, remaining[0], field=field)
    if annotation is Decimal:
        return _require_decimal(raw, field=field)
    if annotation is datetime:
        return _require_utc_datetime(raw, field=field)
    if isinstance(annotation, type) and issubclass(annotation, StrEnum):
        if not isinstance(raw, str):
            raise AdapterError(f"{field} is not a string")
        try:
            return annotation(raw)
        except ValueError as exc:
            raise AdapterError(f"{field} is not a valid {annotation.__name__}") from exc
    if annotation is bool:
        if not isinstance(raw, bool):
            raise AdapterError(f"{field} is not a boolean")
        return raw
    if annotation is int:
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise AdapterError(f"{field} is not an int")
        return raw
    if annotation is str:
        if not isinstance(raw, str):
            raise AdapterError(f"{field} is not a string")
        return raw
    origin = typing.get_origin(annotation)
    if origin is tuple:
        if not isinstance(raw, list):
            raise AdapterError(f"{field} is not a list")
        item_types = [a for a in args if a is not Ellipsis]
        item_type = item_types[0] if item_types else str
        return tuple(_coerce(item, item_type, field=f"{field}[]") for item in raw)
    raise AdapterError(f"{field} has an unsupported type for adapter coercion: {annotation!r}")


def _typed_kwargs(cls: type, payload: object, *, exclude: frozenset[str]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AdapterError(f"{cls.__name__} payload is not an object")
    hints = typing.get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name in exclude:
            continue
        if f.name not in payload:
            raise AdapterError(f"{cls.__name__} payload is missing field {f.name}")
        kwargs[f.name] = _coerce(payload[f.name], hints[f.name], field=f"{cls.__name__}.{f.name}")
    extra = set(payload) - {f.name for f in dataclasses.fields(cls)}
    if extra:
        raise AdapterError(f"{cls.__name__} payload has unexpected fields: {sorted(extra)}")
    return kwargs


# ---------------------------------------------------------------------------
# M27J current rules identity
# ---------------------------------------------------------------------------


def build_rules_identity_fixture(
    payload: object,
    *,
    expected_market_ticker: str,
    expected_event_ticker: str,
    expected_rules_hash: str,
    now: datetime,
) -> RulesIdentityFixture:
    """Independently re-validate a persisted current-side ``AuthoritativeMarketSnapshot`` JSON
    payload (as acquired out-of-band, for example by
    :func:`services.supervised_canary.m27j.acquire_current_market_rules`) into a
    :class:`RulesIdentityFixture`.

    Delegates the entire PASS/FAIL decision to
    :func:`services.supervised_canary.m27j.validate_current_rules_for_candidate`. Raises
    :class:`AdapterError` -- never returns a fixture -- for evidence that does not independently
    pass.
    """
    result = validate_current_rules_for_candidate(
        payload,
        expected_market_ticker=expected_market_ticker,
        expected_event_ticker=expected_event_ticker,
        expected_rules_hash=expected_rules_hash,
        now=now,
    )
    if not result.succeeded:
        raise AdapterError(f"current rules evidence did not validate: {result.reason}")
    # Re-run the identical shared validator to extract rules_hash/observed_at -- this is the
    # exact same function validate_current_rules_for_candidate itself calls, so there is no
    # drift risk between the PASS decision above and the values used below.
    snapshot = validate_market_snapshot(
        payload,
        expected_ticker=expected_market_ticker,
        expected_event_ticker=expected_event_ticker,
    )
    if not snapshot.succeeded or snapshot.rules_hash is None or snapshot.observed_at is None:
        raise AdapterError("current rules evidence passed but could not be re-extracted")
    return RulesIdentityFixture(
        market_ticker=expected_market_ticker,
        event_ticker=expected_event_ticker,
        expected_rules_hash=expected_rules_hash,
        current_rules_hash=snapshot.rules_hash,
        observed_at=snapshot.observed_at,
    )


# ---------------------------------------------------------------------------
# Account / reconciliation / M27H credential-signer evidence
# ---------------------------------------------------------------------------


def build_account_snapshot_fixture(
    *,
    live_read_evidence_path: Path,
    write_credential_evidence_path: Path,
    candidate_exposure_payload: object,
    expected_market_ticker: str,
    now: datetime,
) -> AccountSnapshotFixture:
    """Independently re-validate persisted M27F (live read/reconciliation) and M27H (installed
    credential/signer) JSON evidence files into an :class:`AccountSnapshotFixture`.

    All four boolean gates are read out of
    :func:`services.supervised_canary.readiness_report.operator_evidence`, which never trusts
    either artifact's own stored classification -- it re-derives freshness at consumption time
    and, for the M27H artifact, delegates to
    :func:`services.production_execution.installed_credential_verification.validate_installed_credential_evidence_for_readiness`.

    ``account_snapshot_version``/``reconciliation_version`` are computed through
    :func:`services.supervised_canary.m27i.compute_account_snapshot_version`/
    :func:`services.supervised_canary.m27i.compute_reconciliation_version` -- the same existing,
    reviewed identity functions M27I itself requires any ``PortfolioRiskSnapshot`` producer to
    use -- rather than this module inventing its own identity scheme. Because
    ``compute_reconciliation_version`` binds the identity to one specific market's exposure
    evidence, this also requires (and independently re-validates) the candidate exposure payload
    for the same ``expected_market_ticker``.
    """
    live_read_payload = json.loads(live_read_evidence_path.read_text())
    write_credential_payload = json.loads(write_credential_evidence_path.read_text())
    if not isinstance(live_read_payload, dict):
        raise AdapterError("live read evidence payload is not an object")
    if not isinstance(write_credential_payload, dict):
        raise AdapterError("write credential evidence payload is not an object")

    evidence = operator_evidence(
        live_read_evidence=live_read_evidence_path,
        write_credential_evidence=write_credential_evidence_path,
        now=now,
    )

    def _passed(name: str) -> bool:
        status, _reason = evidence[name]
        return status == "PASS"

    exposure = _parse_candidate_exposure_evidence(
        candidate_exposure_payload, expected_market_ticker=expected_market_ticker
    )
    account_snapshot_version = compute_account_snapshot_version(live_read_payload)
    reconciliation_version = compute_reconciliation_version(live_read_payload, exposure)

    read_observed_at = _require_utc_datetime(
        live_read_payload.get("completed_at"), field="live_read.completed_at"
    )
    signer_observed_at = _require_utc_datetime(
        write_credential_payload.get("completed_at"), field="write_credential.completed_at"
    )
    # Conservative: the fixture carries one observed_at, so use whichever artifact is older --
    # never let a fresh artifact of one kind paper over a stale artifact of the other.
    observed_at = min(read_observed_at, signer_observed_at)

    return AccountSnapshotFixture(
        account_snapshot_version=account_snapshot_version,
        reconciliation_version=reconciliation_version,
        observed_at=observed_at,
        production_reads_verified=all(_passed(name) for name in _ACCOUNT_READ_GATES),
        reconciled=_passed("ACCOUNT_RECONCILIATION"),
        write_credential_evidence_verified=_passed("PRODUCTION_WRITE_CREDENTIAL"),
        signer_runtime_evidence_verified=_passed("REAL_SIGNER_VALIDATION"),
    )


# ---------------------------------------------------------------------------
# Candidate-specific exposure evidence
# ---------------------------------------------------------------------------


def _parse_candidate_exposure_evidence(
    payload: object, *, expected_market_ticker: str
) -> CandidateExposureEvidence:
    """Independently re-validate a persisted
    :class:`services.supervised_canary.candidate_exposure_check.CandidateExposureEvidence` JSON
    payload, returning the reconstructed evidence object itself (used both to build a
    :class:`CandidateExposureFixture` and to feed
    :func:`services.supervised_canary.m27i.compute_reconciliation_version`).
    """
    if not isinstance(payload, dict) or set(payload) != _CANDIDATE_EXPOSURE_FIELDS:
        raise AdapterError("candidate exposure payload has unexpected or missing fields")
    if payload.get("schema") != _CANDIDATE_EXPOSURE_SCHEMA:
        raise AdapterError("candidate exposure payload schema mismatch")
    # software_version is deliberately not checked against a fixed constant: the existing
    # reviewed gate this evidence feeds (services.supervised_canary.m27i._candidate_exposure_gates)
    # never treats it as security-relevant either -- only market_ticker/succeeded/freshness/counts
    # are. It is still required to be a non-empty string (shape check only).
    if not isinstance(payload.get("software_version"), str) or not payload.get("software_version"):
        raise AdapterError("candidate exposure payload software_version is malformed")
    market_ticker = payload.get("market_ticker")
    if market_ticker != expected_market_ticker:
        raise AdapterError("candidate exposure payload is bound to a different market ticker")
    started_at = _require_utc_datetime(payload.get("started_at"), field="started_at")
    completed_at = _require_utc_datetime(payload.get("completed_at"), field="completed_at")
    if completed_at < started_at:
        raise AdapterError("candidate exposure completed_at precedes started_at")
    classification = payload.get("classification")
    if classification not in {"PASS", "BLOCKED"}:
        raise AdapterError("candidate exposure classification is not recognized")
    for name in ("orders_classification", "positions_classification"):
        if not isinstance(payload.get(name), str):
            raise AdapterError(f"candidate exposure {name} is malformed")
    open_order_count = payload.get("open_order_count")
    position_nonzero = payload.get("position_nonzero")
    if classification == "PASS":
        if isinstance(open_order_count, bool) or not isinstance(open_order_count, int):
            raise AdapterError("candidate exposure open_order_count is malformed")
        if not isinstance(position_nonzero, bool):
            raise AdapterError("candidate exposure position_nonzero is malformed")
    elif open_order_count is not None or position_nonzero is not None:
        raise AdapterError("candidate exposure counts must be null when classification is BLOCKED")
    reason = payload.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise AdapterError("candidate exposure reason is malformed")

    return CandidateExposureEvidence(
        schema=payload["schema"],
        software_version=payload["software_version"],
        market_ticker=market_ticker,
        started_at=started_at,
        completed_at=completed_at,
        orders_classification=payload["orders_classification"],
        positions_classification=payload["positions_classification"],
        open_order_count=open_order_count,
        position_nonzero=position_nonzero,
        classification=classification,
        reason=reason,
    )


def build_candidate_exposure_fixture(
    payload: object, *, expected_market_ticker: str
) -> CandidateExposureFixture:
    """Independently re-validate a persisted candidate-exposure JSON payload into a
    :class:`CandidateExposureFixture`.

    ``succeeded`` is always the type's own derived property (``classification == "PASS"``) --
    this payload schema carries no raw "succeeded"/"passed" field, and none is ever read.
    """
    evidence = _parse_candidate_exposure_evidence(
        payload, expected_market_ticker=expected_market_ticker
    )
    return CandidateExposureFixture(
        market_ticker=evidence.market_ticker,
        completed_at=evidence.completed_at,
        # Fail-closed sentinels: only reachable when evidence.succeeded is False, at which point
        # _exposure_gates() rejects on `succeeded` before ever inspecting these two fields.
        open_order_count=evidence.open_order_count if evidence.open_order_count is not None else -1,
        position_nonzero=(
            evidence.position_nonzero if evidence.position_nonzero is not None else True
        ),
        succeeded=evidence.succeeded,
        reason=evidence.reason,
    )


# ---------------------------------------------------------------------------
# M13 risk quadruple
# ---------------------------------------------------------------------------


def build_m13_fixture(
    *,
    risk_intent_payload: object,
    risk_snapshot_payload: object,
    risk_decision_payload: object,
    risk_authorization_payload: object,
    global_halt_clear: bool,
    compliance_clear: bool,
    kills_clear: bool,
) -> M13Fixture:
    """Build a :class:`M13Fixture` from four persisted, already-issued M13 artifacts.

    ``RiskIntent``/``PortfolioRiskSnapshot``/``RiskDecision`` are each reconstructed through
    their own ``.freeze(**values)`` classmethod, which independently recomputes
    ``content_hash`` (and, for ``RiskDecision``, ``decision_id``) from every other field and
    runs the type's own ``__post_init__`` validation (finite non-negative money, subaccount==0,
    etc.). This function never accepts a stored hash from the payload as an input to that
    computation. ``RiskAuthorization`` has no such classmethod; its ``authorization_id`` is
    independently re-derived and cross-checked against
    ``risk_decision_id``/``intent_hash``/``safety_state_hash``/``created_at`` by
    ``m27n_weather_rehearsal``'s own ``m13_authorization_fresh`` gate once this fixture reaches
    :func:`run_m27n1_rehearsal` -- this function does not duplicate or weaken that check.

    ``global_halt_clear``/``compliance_clear``/``kills_clear`` cannot be independently
    recomputed here: see module docstring "Known, disclosed gap". They are accepted only as
    explicit ``bool`` values already produced by a separate, out-of-band M13/M16 safety-state
    check -- this function raises if given anything else.
    """
    try:
        intent = RiskIntent.freeze(
            **_typed_kwargs(RiskIntent, risk_intent_payload, exclude=frozenset({"content_hash"}))
        )
        snapshot = PortfolioRiskSnapshot.freeze(
            **_typed_kwargs(
                PortfolioRiskSnapshot, risk_snapshot_payload, exclude=frozenset({"content_hash"})
            )
        )
        decision = RiskDecision.freeze(
            **_typed_kwargs(
                RiskDecision,
                risk_decision_payload,
                exclude=frozenset({"content_hash", "decision_id"}),
            )
        )
        authorization = RiskAuthorization(
            **_typed_kwargs(RiskAuthorization, risk_authorization_payload, exclude=frozenset())
        )
    except RiskDomainError as exc:
        raise AdapterError(f"M13 evidence failed domain validation: {exc}") from exc

    for name, value in (
        ("global_halt_clear", global_halt_clear),
        ("compliance_clear", compliance_clear),
        ("kills_clear", kills_clear),
    ):
        if not isinstance(value, bool):
            raise AdapterError(f"{name} must be an explicit already-produced boolean")

    return M13Fixture(
        authorization=authorization,
        risk_decision=decision,
        risk_intent=intent,
        risk_snapshot=snapshot,
        global_halt_clear=global_halt_clear,
        compliance_clear=compliance_clear,
        kills_clear=kills_clear,
    )


# ---------------------------------------------------------------------------
# Submission budget -- see module docstring "Known, disclosed gap".
# ---------------------------------------------------------------------------


def build_submission_budget_fixture(
    *, write_budget_used: bool, unresolved_canary_present: bool
) -> SubmissionBudgetFixture:
    """Build a :class:`SubmissionBudgetFixture` from two already-produced booleans.

    Cannot be independently recomputed by this OFFLINE module: the only authority is
    ``services.supervised_canary.store.CanaryStore``, which is SQLite-backed and would mutate a
    database file merely by being constructed. Raises if given anything but explicit ``bool``
    values already produced by a separate, out-of-band check.
    """
    for name, value in (
        ("write_budget_used", write_budget_used),
        ("unresolved_canary_present", unresolved_canary_present),
    ):
        if not isinstance(value, bool):
            raise AdapterError(f"{name} must be an explicit already-produced boolean")
    return SubmissionBudgetFixture(
        write_budget_used=write_budget_used, unresolved_canary_present=unresolved_canary_present
    )


# ---------------------------------------------------------------------------
# Orchestrator -- calls the existing, unmodified M27N pure rehearsal builder.
# ---------------------------------------------------------------------------


def run_m27n1_rehearsal(
    *,
    now: datetime,
    candidate_inputs: Sequence[_CandidateInput],
    m13: M13Fixture | None = None,
    account_snapshot: AccountSnapshotFixture | None = None,
    candidate_exposure: CandidateExposureFixture | None = None,
    rules_identity: RulesIdentityFixture | None = None,
    submission_budget: SubmissionBudgetFixture | None = None,
    maximum_accepted_fee: Decimal = Decimal("0"),
    expiration: datetime | None = None,
    order_group_id: str | None = None,
) -> RehearsalResult:
    """Call the existing, unmodified
    :func:`services.supervised_canary.m27n_weather_rehearsal.build_rehearsal` with
    adapter-produced fixtures. This function performs no gate logic of its own -- every gate is
    the same one ``build_rehearsal`` already independently re-derives from whatever fixtures it
    is given.

    THIS FUNCTION DOES NOT COMPOSE ``candidate_inputs`` -- see module docstring "Known, disclosed
    gap". Each ``(PhysicalTemperatureProxyProbability, CurrentWeatherForecastEvidence,
    MarketEconomicsEvidence)`` tuple must already have been built by the caller through each
    type's own existing validating constructor; no combined JSON -> triple deserializer exists
    anywhere in this repository today, and this module does not invent one.
    """
    return build_rehearsal(
        now=now,
        candidate_inputs=candidate_inputs,
        m13=m13,
        account_snapshot=account_snapshot,
        candidate_exposure=candidate_exposure,
        rules_identity=rules_identity,
        submission_budget=submission_budget,
        maximum_accepted_fee=maximum_accepted_fee,
        expiration=expiration,
        order_group_id=order_group_id,
    )
