from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from src.models.cash import CashBalance
from src.models.institution import Institution
from src.models.security import Holding, SecurityPayment
from src.models.taxpayer import TaxPayer
from src.models.transaction import Transaction


@dataclass
class TaxStatement:
    """Complete tax statement produced by a parser and consumed by generators.

    This is the central domain object that bridges parsing (input) and
    generation (output) stages of the pipeline.
    """

    tax_year: int
    period_from: date
    period_to: date
    taxpayer: TaxPayer
    institution: Institution
    holdings: list[Holding] = field(default_factory=list)
    cash_balances: list[CashBalance] = field(default_factory=list)
    security_payments: list[SecurityPayment] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    expenses: list[Transaction] = field(default_factory=list)
    total_tax_value: Decimal | None = None
    total_gross_revenue_a: Decimal | None = None
    total_gross_revenue_b: Decimal | None = None
    total_withholding_tax_claim: Decimal | None = None
    account_number: str = ""
    portfolio_year: int | None = None
