"""M27N.2 -- OFFLINE Event/Market reconstruction: validated snapshot evidence -> typed object.

Why this module exists (read before extending): :func:`services.market_universe.event_snapshot.
validate_event_snapshot` and :func:`services.market_universe.market_snapshot.
validate_market_snapshot` each independently re-derive and cross-check a snapshot's hash/identity
material -- but deliberately return only scalar fields (``EventSnapshotValidation``/
``SnapshotValidation``: ticker, series/event ticker, rules/metadata hash, observed_at), never the
:class:`services.market_universe.domain.Event`/:class:`services.market_universe.domain.Market`
object each validator's own internal ``Event.parse``/``Market.parse`` call already built and then
discarded. :func:`services.forecasting.daily_temperature.route_daily_temperature` requires the
actual object (it reads ``market.raw["rules_primary"]``, ``event.raw.get("settlement_sources")``,
etc.) -- hashes alone are insufficient.

Authorization note: this module closes that gap only the narrowest way explicitly authorized for
M27N.2 -- it re-runs the SAME canonical ``Event.parse``/``Market.parse`` the validator itself
calls, on the SAME immutable ``raw_body_b64`` string already read out of the SAME payload dict the
validator just proved matches its recorded ``body_sha256``. There is no second fetch, no second
read of a different string, and no code path here that parses packet-supplied JSON that has not
first passed the frozen validator. Every hash/identity field the validator independently derived
is cross-checked against the fresh reparse; any mismatch fails closed
(:class:`EvidenceReconstructionError`), never a best-effort continue.

This module never modifies :mod:`services.market_universe.event_snapshot` or
:mod:`services.market_universe.market_snapshot`, has no transport, network, subprocess, or
credential dependency of its own, and accepts only already-produced evidence payloads.
"""

from __future__ import annotations

import base64
import json

from services.market_universe.domain import Event, Market, UniverseValidationError
from services.market_universe.event_snapshot import validate_event_snapshot
from services.market_universe.market_snapshot import validate_market_snapshot


class EvidenceReconstructionError(ValueError):
    """Validated snapshot evidence failed independent Event/Market reconstruction."""


def reconstruct_event(payload: object, *, expected_ticker: str) -> Event:
    """Reconstruct the typed :class:`Event` behind an already-PASSing event snapshot payload.

    Raises :class:`EvidenceReconstructionError` (fail closed) if the payload does not itself
    independently validate, if the reparse fails the canonical parser, or if the freshly reparsed
    object's identity disagrees with what the validator itself independently derived.
    """
    validation = validate_event_snapshot(payload, expected_ticker=expected_ticker)
    if not validation.succeeded:
        raise EvidenceReconstructionError(
            f"event snapshot evidence did not validate: {validation.classification} "
            f"({validation.reason})"
        )
    if not isinstance(payload, dict):  # pragma: no cover - succeeded implies dict
        raise EvidenceReconstructionError("event snapshot payload is not an object")
    # validate_event_snapshot's PASS already proved raw_body_b64 decodes to bytes whose sha256
    # equals the recorded body_sha256, and that Event.parse succeeds on the embedded event object
    # -- this decodes the exact same string from the exact same payload dict a second time, so it
    # cannot diverge from what the validator itself just checked.
    body = base64.b64decode(payload["raw_body_b64"], validate=True)
    raw_event = json.loads(body)["event"]
    try:
        event = Event.parse(raw_event)
    except UniverseValidationError as exc:
        raise EvidenceReconstructionError(f"event reparse failed: {exc}") from exc
    if (
        event.ticker != validation.ticker
        or event.series_ticker != validation.series_ticker
        or event.metadata_hash != validation.metadata_hash
    ):
        raise EvidenceReconstructionError(
            "reparsed event does not match the validator's own independently-derived identity"
        )
    return event


def reconstruct_market(
    payload: object, *, expected_ticker: str, expected_event_ticker: str
) -> Market:
    """Reconstruct the typed :class:`Market` behind an already-PASSing market snapshot payload.

    Mirrors :func:`reconstruct_event` exactly, for the market side. Raises
    :class:`EvidenceReconstructionError` (fail closed) on any validation, reparse, or identity
    cross-check failure.
    """
    validation = validate_market_snapshot(
        payload, expected_ticker=expected_ticker, expected_event_ticker=expected_event_ticker
    )
    if not validation.succeeded:
        raise EvidenceReconstructionError(
            f"market snapshot evidence did not validate: {validation.classification} "
            f"({validation.reason})"
        )
    if not isinstance(payload, dict):  # pragma: no cover - succeeded implies dict
        raise EvidenceReconstructionError("market snapshot payload is not an object")
    body = base64.b64decode(payload["raw_body_b64"], validate=True)
    raw_market = json.loads(body)["market"]
    try:
        market = Market.parse(raw_market)
    except UniverseValidationError as exc:
        raise EvidenceReconstructionError(f"market reparse failed: {exc}") from exc
    if (
        market.ticker != validation.ticker
        or market.event_ticker != validation.event_ticker
        or market.rules_hash != validation.rules_hash
        or market.metadata_hash != validation.metadata_hash
    ):
        raise EvidenceReconstructionError(
            "reparsed market does not match the validator's own independently-derived identity"
        )
    return market
