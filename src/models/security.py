from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from src.models.enums import IncomeCategory


@dataclass
class Security:
    """A financial instrument (equity, bond, fund, etc.)."""

    isin: str
    name: str
    currency: str
    valor_number: str | None = None
    tax_value_per_unit: Decimal | None = None
    tax_value_per_unit_chf: Decimal | None = None
    income_category: IncomeCategory | None = None


@dataclass
class Holding:
    """A position in a security held on the tax reference date (31 Dec)."""

    security: Security
    quantity: Decimal
    tax_value_chf: Decimal | None = None
    exchange_rate: Decimal | None = None


@dataclass
class SecurityPayment:
    """A dividend, coupon, or other payment received from a security."""

    security: Security
    payment_date: date
    gross_amount: Decimal
    currency: str
    withholding_tax: Decimal
    net_amount: Decimal
    quantity: Decimal | None = None
    income_category: IncomeCategory | None = None
    gross_amount_chf: Decimal | None = None
    withholding_tax_chf: Decimal | None = None
    exchange_rate: Decimal | None = None
    amount_per_unit: Decimal | None = None
    ex_date: date | None = None
    # DA-1 fields (from Kursliste da1Rate data)
    non_recoverable_tax_pct: Decimal | None = None
    non_recoverable_tax_chf: Decimal | None = None
    lump_sum_tax_credit_pct: Decimal | None = None
    lump_sum_tax_credit_chf: Decimal | None = None
