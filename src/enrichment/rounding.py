"""DIN 1333 rounding utility for Swiss tax values."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def round_to_005(amount: Decimal) -> Decimal:
    """Round to nearest 0.05 CHF per DIN 1333."""
    return (amount * 20).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / 20
