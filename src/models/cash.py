from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class CashBalance:
    """Cash balance in a single currency as of the tax reference date."""

    currency: str
    balance: Decimal
    tax_value_chf: Decimal | None = None
    interest_earned: Decimal = Decimal("0")
    interest_earned_chf: Decimal | None = None
    exchange_rate: Decimal | None = None
    iban: str | None = None
