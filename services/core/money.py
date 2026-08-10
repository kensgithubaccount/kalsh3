"""Exact fixed-point financial primitives."""

from decimal import Decimal, InvalidOperation


def decimal_from_wire(value: str) -> Decimal:
    """Parse a finite fixed-point wire value without accepting binary floats."""
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid fixed-point value") from exc
    if not parsed.is_finite():
        raise ValueError("fixed-point value must be finite")
    return parsed
