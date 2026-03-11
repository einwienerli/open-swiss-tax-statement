"""Enrichment orchestrator: populates CHF values, valor numbers, and tax prices.

Takes a TaxStatement produced by a broker parser and enriches it with:
- Year-end FX rates for CHF conversion
- Kursliste data (valor numbers, year-end prices, security categories)
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from src.enrichment.exchange_rates import MissingRateError, get_rate
from src.enrichment.kursliste_parser import (
    Da1Rate,
    KurslisteEntry,
    load_bundled_da1_rates,
    load_bundled_kursliste,
)

if TYPE_CHECKING:
    from src.models.tax_statement import TaxStatement

log = logging.getLogger(__name__)


def enrich(
    statement: TaxStatement,
    rates: dict[str, Decimal] | None = None,
    kursliste: dict[str, KurslisteEntry] | None = None,
    da1_rates: dict[tuple[str, str], Da1Rate] | None = None,
) -> TaxStatement:
    """Enrich a TaxStatement with CHF conversions and Kursliste data.

    Args:
        statement: The statement to enrich (modified in place and returned).
        rates: Optional dict of currency→CHF rate. If None, loads from
               bundled ESTV data for the statement's tax year.
        kursliste: Optional dict of ISIN→KurslisteEntry from Kursliste XML.
        da1_rates: Optional dict of (country, securityGroup)→Da1Rate.
                   If None, loads from bundled da1_rates JSON.

    Returns:
        The same statement object, enriched with CHF values.
    """
    year = statement.tax_year

    log.info(
        "Enriching statement: year=%d, holdings=%d, payments=%d, cash_balances=%d",
        year,
        len(statement.holdings),
        len(statement.security_payments),
        len(statement.cash_balances),
    )

    # Auto-load bundled kursliste if none provided
    if kursliste is None:
        kursliste = load_bundled_kursliste(year)

    if kursliste:
        log.info("Kursliste loaded: %d entries for year %d", len(kursliste), year)
    else:
        log.warning("No kursliste available for year %d — enrichment will be incomplete", year)

    # Auto-load bundled DA-1 rates if none provided
    if da1_rates is None:
        da1_rates = load_bundled_da1_rates(year)

    def _get_fx(currency: str) -> Decimal | None:
        """Return FX rate or None if rates are unavailable for this year."""
        if rates is not None:
            if currency == "CHF":
                return Decimal("1")
            if currency not in rates:
                raise MissingRateError(
                    f"No exchange rate for {currency} in year {year}"
                )
            return rates[currency]
        try:
            return get_rate(currency, year)
        except MissingRateError:
            log.warning("No FX rate for %s in year %d, skipping CHF conversion", currency, year)
            return None

    # Enrich holdings
    for holding in statement.holdings:
        isin = holding.security.isin
        currency = holding.security.currency
        fx = _get_fx(currency)

        # Apply Kursliste data if available
        if kursliste and isin in kursliste:
            entry = kursliste[isin]
            if not holding.security.valor_number:
                holding.security.valor_number = entry.valor_number
            if entry.year_end_price is not None and holding.security.tax_value_per_unit is None:
                holding.security.tax_value_per_unit = entry.year_end_price

            # CHF unit price: prefer Kursliste taxValueCHF, fall back to
            # original price × FX rate for CHF securities
            if holding.security.tax_value_per_unit_chf is None:
                if entry.tax_value_chf is not None:
                    holding.security.tax_value_per_unit_chf = entry.tax_value_chf
                elif currency == "CHF" and entry.year_end_price is not None:
                    holding.security.tax_value_per_unit_chf = entry.year_end_price

            # Use pre-computed CHF tax value from Kursliste V2.2 if available
            if entry.tax_value_chf is not None and holding.tax_value_chf is None:
                holding.tax_value_chf = holding.quantity * entry.tax_value_chf

        if fx is None:
            continue

        # Compute CHF tax value if we have a per-unit price (fallback for V1.2 data)
        if holding.security.tax_value_per_unit is not None and holding.tax_value_chf is None:
            holding.tax_value_chf = holding.quantity * holding.security.tax_value_per_unit * fx

        # CHF unit price fallback: original price × FX (when Kursliste didn't have taxValueCHF)
        if holding.security.tax_value_per_unit_chf is None and holding.security.tax_value_per_unit is not None:
            holding.security.tax_value_per_unit_chf = holding.security.tax_value_per_unit * fx

        # Set exchange rate for non-CHF
        if currency != "CHF":
            holding.exchange_rate = fx

    # Log holdings match rate
    if statement.holdings:
        matched = sum(1 for h in statement.holdings if h.security.valor_number)
        total = len(statement.holdings)
        log.info("Holdings enrichment: %d/%d matched with valor number", matched, total)
        if matched < total:
            unmatched = [
                h.security.isin
                for h in statement.holdings
                if not h.security.valor_number
            ]
            log.info("Unmatched ISINs: %s", ", ".join(unmatched))

    # Enrich cash balances — always use ESTV rate (authoritative for tax purposes)
    for cb in statement.cash_balances:
        fx = _get_fx(cb.currency)
        if fx is None:
            continue

        if cb.currency != "CHF":
            cb.tax_value_chf = cb.balance * fx
            cb.exchange_rate = fx
        elif cb.tax_value_chf is None:
            cb.tax_value_chf = cb.balance

        if cb.interest_earned and cb.interest_earned_chf is None:
            cb.interest_earned_chf = cb.interest_earned * fx

    # Enrich security payments
    for p in statement.security_payments:
        fx = _get_fx(p.currency)
        if fx is None:
            continue

        if p.gross_amount_chf is None:
            p.gross_amount_chf = p.gross_amount * fx

        if p.withholding_tax_chf is None:
            p.withholding_tax_chf = p.withholding_tax * fx

        if p.currency != "CHF":
            p.exchange_rate = fx

    # Enrich security payments with DA-1 rate data
    if da1_rates:
        _enrich_da1(statement, da1_rates, kursliste)
        da1_count = sum(
            1 for p in statement.security_payments
            if p.lump_sum_tax_credit_pct is not None
        )
        log.info(
            "DA-1 enrichment: %d/%d payments received a DA-1 rate",
            da1_count,
            len(statement.security_payments),
        )

    return statement


def _determine_security_group(isin: str, kursliste: dict[str, KurslisteEntry] | None) -> str:
    """Determine security group (SHARE/FUND/BOND) from Kursliste data.

    Falls back to SHARE if not found in Kursliste.
    """
    if kursliste and isin in kursliste:
        group = kursliste[isin].security_group
        if group:
            return group
    return "SHARE"


def _enrich_da1(
    statement: TaxStatement,
    da1_rates: dict[tuple[str, str], Da1Rate],
    kursliste: dict[str, KurslisteEntry] | None,
) -> None:
    """Populate DA-1 fields on Rubrik B security payments.

    For each foreign (non-CH) payment, looks up the DA-1 rate by
    (country, securityGroup) and computes non-recoverable tax and
    lump-sum tax credit amounts from the rate × gross CHF.
    """
    from src.generators.ech0196.xml_builder import _determine_income_category
    from src.models.enums import IncomeCategory

    for p in statement.security_payments:
        cat = _determine_income_category(p)
        if cat != IncomeCategory.RUBRIK_B:
            continue

        country = p.security.isin[:2].upper() if len(p.security.isin) >= 2 else ""
        if not country or country == "CH":
            continue

        security_group = _determine_security_group(p.security.isin, kursliste)
        rate = da1_rates.get((country, security_group))
        # Fund distributions are treated as dividend income under tax treaties,
        # so they use the same DA-1 rates as shares.  The ESTV DA-1 rate tables
        # only publish SHARE and BOND rates — no separate FUND rates.
        if rate is None and security_group == "FUND":
            rate = da1_rates.get((country, "SHARE"))
        if rate is None:
            continue

        gross_chf = p.gross_amount_chf if p.gross_amount_chf is not None else p.gross_amount

        if rate.non_recoverable is not None and p.non_recoverable_tax_pct is None:
            p.non_recoverable_tax_pct = rate.non_recoverable
            p.non_recoverable_tax_chf = gross_chf * rate.non_recoverable / Decimal("100")

        if rate.release is not None and p.lump_sum_tax_credit_pct is None:
            p.lump_sum_tax_credit_pct = rate.release
            p.lump_sum_tax_credit_chf = gross_chf * rate.release / Decimal("100")
