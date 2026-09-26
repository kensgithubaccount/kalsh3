"""Explicit GLOBALTEMPERATURE settlement comparisons, not forecast calibration.

Authority: https://assets.kalshi.com/contract_terms/GLOBALTEMPERATURE.pdf
Page 1 defines BETWEEN with both endpoints included; ABOVE/BELOW are strict.
Page 2 requires the full source-reported precision. No whole-degree, one-decimal,
half-up, floor, ceiling, or unit conversion is introduced for these comparators.

KXHIGHCHI rules and structured strikes control, not a display label: a lower
tail displayed as "65 or below" has cap_strike=66 and rule LESS THAN 66. An upper
tail displayed as "74 or above" has floor_strike=73 and rule GREATER THAN 73.
Finalized expiration_value is already the official underlying value. It is NOT
settlement_value_dollars (the binary payout) and is never inferred from a bucket.

Scope: WN-A2 reconciliation only; WN-A1 probability/model logic is unchanged.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from .wn_a1_current_daily_high_authority import _RULE, _strike

BUCKET_SEMANTICS_VERSION = "wn-a2-globaltemperature-inclusive-full-precision-v1"
# Exact reviewed standard text shared by all 30 archived Sep21-25 KXHIGHCHI
# markets. It warns about preliminary rounding, but authorizes no transformation
# of the finalized expiration_value. Unknown secondary overrides fail closed.
REVIEWED_SECONDARY_RULES_SHA256 = "c1f11eaf372267f2e69ca8ba131916928052ec5e92138e45c16e197f2d4bb507"


class BucketSemanticsError(ValueError):
    """A comparison is unsupported or its inputs lack finite exact authority."""


def validate_bound_contract(
    *,
    raw: Mapping[str, object],
    comparator: str,
    lower: Decimal,
    upper: Decimal | None,
    target_local_date: str,
) -> None:
    """Revalidate the reviewed current-family rule against persisted strikes.

    Reuse WN-A1's exact rule parser, but do not require lifecycle ACTIVE when
    reconciling a finalized response. Unknown rounding/comparator wording,
    changed dates, or conflicts between structured strikes/rules fail closed.
    """
    secondary = raw.get("rules_secondary")
    if secondary not in (None, "") and (
        not isinstance(secondary, str)
        or hashlib.sha256(secondary.encode()).hexdigest() != REVIEWED_SECONDARY_RULES_SHA256
    ):
        raise BucketSemanticsError("unreviewed or contradictory secondary settlement rules")
    rule = raw.get("rules_primary")
    match = _RULE.fullmatch(rule) if isinstance(rule, str) else None
    if match is None:
        raise BucketSemanticsError("settlement rule shape is not the reviewed contract family")
    if datetime.strptime(match.group("date"), "%b %d, %Y").date().isoformat() != target_local_date:
        raise BucketSemanticsError("settlement rule date differs from persisted target")
    expected = {"RANGE": "between", "LT": "less", "GT": "greater"}.get(comparator)
    if raw.get("strike_type") != expected or expected is None:
        raise BucketSemanticsError("settlement strike type differs from persisted comparator")
    if comparator == "RANGE":
        structured_lower = _strike(raw.get("floor_strike"), "floor_strike")
        structured_upper = _strike(raw.get("cap_strike"), "cap_strike")
        if not match.group("between_low"):
            raise BucketSemanticsError("rule comparator conflicts with structured strike")
        if (lower, upper) != (structured_lower, structured_upper) or (lower, upper) != (
            Decimal(match.group("between_low")),
            Decimal(match.group("between_high")),
        ):
            raise BucketSemanticsError("settlement range differs from persisted/rule bounds")
    else:
        field, group = ("cap_strike", "less") if comparator == "LT" else ("floor_strike", "greater")
        unused_field = "floor_strike" if comparator == "LT" else "cap_strike"
        if raw.get(unused_field) is not None:
            raise BucketSemanticsError("tail has contradictory extra structured bound")
        if not match.group(group):
            raise BucketSemanticsError("rule comparator conflicts with structured strike")
        if (
            upper is not None
            or lower != _strike(raw.get(field), field)
            or lower != Decimal(match.group(group))
        ):
            raise BucketSemanticsError("settlement tail differs from persisted/rule threshold")


def contains_finalized_temperature(
    *, comparator: str, lower: Decimal, upper: Decimal | None, value: Decimal
) -> bool:
    """Compare exact canonical values against structured contract thresholds.

    RANGE: lower <= value <= upper. GT: value > lower. LT: value < lower,
    where the existing persisted LT field named lower contains cap_strike.
    Unsupported comparators (including EXACT and assumed rounding) fail closed.
    Gaps between adjacent structured buckets are not filled by nearest-bin logic.
    """
    if not isinstance(value, Decimal) or not value.is_finite():
        raise BucketSemanticsError("settlement temperature is not a finite exact Decimal")
    if not isinstance(lower, Decimal) or not lower.is_finite():
        raise BucketSemanticsError("settlement threshold is not a finite exact Decimal")
    if comparator == "RANGE":
        if not isinstance(upper, Decimal) or not upper.is_finite() or lower >= upper:
            raise BucketSemanticsError("bounded settlement range is missing or invalid")
        return lower <= value <= upper
    if comparator not in {"GT", "LT"} or upper is not None:
        raise BucketSemanticsError("unsupported or ambiguous settlement comparator")
    return value > lower if comparator == "GT" else value < lower
