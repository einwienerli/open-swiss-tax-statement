from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from src.models.enums import TransactionType
from src.models.security import Security


@dataclass
class Transaction:
    """A single buy, sell, dividend, fee, or other financial transaction."""

    date: date
    type: TransactionType
    security: Security | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    costs: Decimal = Decimal("0")
    net_amount: Decimal = Decimal("0")
    currency: str = "CHF"
    order_number: str | None = None
    accrued_interest: Decimal | None = None
