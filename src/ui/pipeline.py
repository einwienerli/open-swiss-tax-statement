"""Pipeline orchestrator: wraps the backend into a single function for the UI."""

from __future__ import annotations

import logging
import time
import warnings
from decimal import Decimal

from pypdf import PdfReader

from src.enrichment import enrich
from src.enrichment.exchange_rates import has_rates
from src.generators.ech0196.xml_builder import _determine_income_category
from src.generators.pdf import generate_pdf
from src.models import TaxPayer, TaxStatement
from src.models.enums import TransactionType
from src.parsers.swissquote import SwissquoteCsvParser, SwissquoteKontoauszugParser

logger = logging.getLogger(__name__)


def _extract_pdf_text(pdf_path: str) -> str:
    """Extract layout-mode text from a PDF via pypdf."""
    pypdf_logger = logging.getLogger("pypdf")
    prev_level = pypdf_logger.level
    pypdf_logger.setLevel(logging.ERROR)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            reader = PdfReader(pdf_path)
            pages = []
            for page in reader.pages:
                text = page.extract_text(extraction_mode="layout")
                if text:
                    pages.append(text)
        return "\n".join(pages)
    finally:
        pypdf_logger.setLevel(prev_level)


def _read_input_file(input_path: str) -> tuple[str, str]:
    """Read the transaction input file and detect its format.

    Returns:
        Tuple of (text_content, input_type) where input_type is
        "kontoauszug" for PDF or "csv" for CSV files.
    """
    if input_path.lower().endswith(".pdf"):
        text = _extract_pdf_text(input_path)
        return text, "kontoauszug"
    else:
        with open(input_path, encoding="latin-1") as f:
            return f.read(), "csv"


def _extract_portfolio_text(portfolio_pdf_path: str | None) -> str | None:
    """Extract text from portfolio PDF via pypdf."""
    if not portfolio_pdf_path:
        return None
    return _extract_pdf_text(portfolio_pdf_path)


def _parse_and_enrich(
    provider: str,
    input_text: str,
    input_type: str,
    portfolio_text: str | None,
    tax_year: int,
    taxpayer: TaxPayer,
    account_number: str = "",
) -> tuple[TaxStatement, list[str]]:
    """Parse input + portfolio and enrich with ESTV data.

    Args:
        provider: Broker provider name (e.g. "swissquote").
        input_text: Transaction file content (CSV text or Kontoauszug PDF text).
        input_type: Either "csv" or "kontoauszug".
        portfolio_text: Optional portfolio PDF text.
        tax_year: Fiscal year.
        taxpayer: Taxpayer details.
        account_number: Broker account number.

    Returns:
        Tuple of (statement, warnings).
    """
    parse_warnings: list[str] = []

    if provider == "swissquote":
        if input_type == "kontoauszug":
            parser = SwissquoteKontoauszugParser()
        else:
            parser = SwissquoteCsvParser()
        if portfolio_text is None:
            raise ValueError("Portfolio PDF is required for Swissquote")
    else:
        raise ValueError(f"Unsupported provider: {provider}")

    logger.info("Parser: %s (%s)", provider, input_type)

    statement = parser.parse(
        input_text, tax_year, taxpayer,
        portfolio_content=portfolio_text,
        account_number=account_number,
    )

    # Validate portfolio year matches tax year
    if statement.portfolio_year is not None:
        logger.info("Year check: portfolio=%d, form=%d", statement.portfolio_year, tax_year)
    if (
        statement.portfolio_year is not None
        and statement.portfolio_year != tax_year
    ):
        raise ValueError(
            f"Portfolio PDF is for year {statement.portfolio_year}, "
            f"but tax year {tax_year} was selected"
        )

    if not has_rates(tax_year):
        parse_warnings.append(
            f"No ESTV exchange rates available for {tax_year}. "
            "Foreign currency amounts are not converted to CHF."
        )

    try:
        enrich(statement)
    except Exception as e:
        raise RuntimeError(f"Enrichment failed: {e}") from e

    return statement, parse_warnings


def _statement_to_summary(statement: TaxStatement) -> dict:
    """Convert a TaxStatement into a JSON-serializable summary dict."""
    def _dec(val: Decimal | None) -> str | None:
        return str(val) if val is not None else None

    holdings = [
        {
            "name": h.security.name,
            "isin": h.security.isin,
            "currency": h.security.currency,
            "quantity": str(h.quantity),
            "tax_value_chf": _dec(h.tax_value_chf),
        }
        for h in statement.holdings
    ]

    cash_balances = [
        {
            "currency": c.currency,
            "balance": str(c.balance),
            "tax_value_chf": _dec(c.tax_value_chf),
            "interest": str(c.interest_earned),
        }
        for c in statement.cash_balances
    ]

    security_payments = [
        {
            "name": p.security.name,
            "date": p.payment_date.isoformat(),
            "gross": str(p.gross_amount),
            "currency": p.currency,
            "category": _determine_income_category(p).value,
            "wht": str(p.withholding_tax),
            "net": str(p.net_amount),
        }
        for p in statement.security_payments
    ]

    expenses = [
        {
            "name": e.security.name if e.security else "Account",
            "date": e.date.isoformat(),
            "amount": str(e.net_amount),
            "currency": e.currency,
        }
        for e in statement.expenses
    ]

    # Aggregate buy/sell transactions by ISIN — matching XML builder logic:
    # holdings + payments + securities that were bought (fully sold positions)
    bought_isins = set()
    for t in statement.transactions:
        if t.security and t.type == TransactionType.BUY:
            bought_isins.add(t.security.isin)
    relevant_isins = (
        {h.security.isin for h in statement.holdings}
        | {p.security.isin for p in statement.security_payments}
        | bought_isins
    )
    trades_by_isin: dict[str, dict] = {}
    for tx in statement.transactions:
        if tx.type not in (TransactionType.BUY, TransactionType.SELL):
            continue
        isin = tx.security.isin
        if isin not in relevant_isins:
            continue
        if isin not in trades_by_isin:
            trades_by_isin[isin] = {
                "name": tx.security.name,
                "isin": isin,
                "currency": tx.security.currency,
                "qty_bought": Decimal("0"),
                "qty_sold": Decimal("0"),
            }
        if tx.type == TransactionType.BUY:
            trades_by_isin[isin]["qty_bought"] += tx.quantity
        else:
            trades_by_isin[isin]["qty_sold"] += tx.quantity

    trades = []
    for t in trades_by_isin.values():
        net = t["qty_bought"] - t["qty_sold"]
        trades.append({
            "name": t["name"],
            "isin": t["isin"],
            "currency": t["currency"],
            "qty_bought": str(t["qty_bought"]),
            "qty_sold": str(t["qty_sold"]),
            "net_change": str(net),
        })

    return {
        "holdings": holdings,
        "cash_balances": cash_balances,
        "security_payments": security_payments,
        "expenses": expenses,
        "trades": trades,
        "totals": {
            "holdings_count": len(holdings),
            "payments_count": len(security_payments),
            "cash_count": len(cash_balances),
            "expenses_count": len(expenses),
            "trades_count": len(trades),
        },
    }


def parse_statement(
    provider: str,
    input_path: str,
    portfolio_pdf_path: str | None,
    tax_year: int,
    taxpayer: TaxPayer,
    account_number: str = "",
) -> dict:
    """Parse transaction file + portfolio, enrich, return summary for preview.

    Args:
        provider: Broker provider name.
        input_path: Path to transaction file (CSV or Kontoauszug PDF).
        portfolio_pdf_path: Path to portfolio PDF.
        tax_year: Fiscal year.
        taxpayer: Taxpayer details.
        account_number: Broker account number.

    Returns dict with keys:
        holdings: list of {name, isin, currency, quantity, tax_value_chf}
        cash_balances: list of {currency, balance, tax_value_chf, interest}
        security_payments: list of {name, date, gross, currency, wht, net}
        expenses: list of {name, date, amount, currency}
        totals: {holdings_count, payments_count, cash_count, expenses_count}
        warnings: list of warning strings
    """
    input_text, input_type = _read_input_file(input_path)
    portfolio_text = _extract_portfolio_text(portfolio_pdf_path)
    statement, parse_warnings = _parse_and_enrich(
        provider, input_text, input_type, portfolio_text, tax_year, taxpayer,
        account_number=account_number,
    )
    summary = _statement_to_summary(statement)
    summary["warnings"] = parse_warnings
    return summary


def run_pipeline(
    provider: str,
    input_path: str,
    portfolio_pdf_path: str | None,
    tax_year: int,
    taxpayer: TaxPayer,
    output_path: str,
    account_number: str = "",
    input_filenames: list[str] | None = None,
) -> list[str]:
    """Run the full E-Steuerauszug pipeline.

    Steps:
        1. Read transaction file (CSV or Kontoauszug PDF)
        2. Extract text from portfolio PDF via pypdf (if provided)
        3. Parse via broker-specific parser
        4. Enrich with ESTV FX rates + Kursliste
        5. Generate PDF
        6. Write output file

    Returns:
        List of warning strings (empty if no warnings).

    Raises:
        FileNotFoundError: If the PDF file does not exist.
        ValueError: If the parser rejects the input.
        RuntimeError: If enrichment or PDF generation fails.
    """
    start = time.monotonic()

    input_text, input_type = _read_input_file(input_path)
    portfolio_text = _extract_portfolio_text(portfolio_pdf_path)
    statement, parse_warnings = _parse_and_enrich(
        provider, input_text, input_type, portfolio_text, tax_year, taxpayer,
        account_number=account_number,
    )

    # Generate PDF
    try:
        pdf_bytes = generate_pdf(statement, input_filenames=input_filenames)
    except Exception as e:
        raise RuntimeError(f"PDF generation failed: {e}") from e

    # Write output
    with open(output_path, "wb") as f:
        f.write(pdf_bytes)

    elapsed = time.monotonic() - start
    logger.info("Pipeline completed in %.2fs", elapsed)

    return parse_warnings
