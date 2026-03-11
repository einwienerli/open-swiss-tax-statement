"""Swissquote CSV transaction export parser.

Transforms a Swissquote CSV export (German or English locale) into the
``TaxStatement`` domain model.  Uses header-based column detection to support
multiple Swissquote CSV variants.  Handles WHT embedded in Kosten, storno
filtering, and optional portfolio PDF integration for holdings/cash.
"""

from __future__ import annotations

import csv
import io
import logging
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Sequence

from src.models.cash import CashBalance
from src.models.enums import TransactionType
from src.models.institution import Institution
from src.models.security import Holding, Security, SecurityPayment
from src.models.tax_statement import TaxStatement
from src.models.taxpayer import TaxPayer
from src.models.transaction import Transaction
from src.parsers.base import BrokerParser
from src.parsers.swissquote.constants import (
    CLEARING_NUMBER,
    ENGLISH_TRANSACTION_TYPES,
    GERMAN_TRANSACTION_TYPES,
    HEADER_TO_FIELD,
    INSTITUTION_NAME,
    INSTITUTION_UID,
    REQUIRED_FIELDS,
    compute_swissquote_iban,
)
from src.parsers.swissquote.portfolio_parser import PortfolioParser

logger = logging.getLogger(__name__)

# Date formats we attempt, in order of likelihood for Swissquote exports.
_DATE_FORMATS = (
    "%d-%m-%Y %H:%M:%S",  # Real format: 02-01-2025 09:00:42
    "%d-%m-%Y",            # Without time
    "%d.%m.%Y",            # Legacy: 15.03.2024
    "%Y-%m-%d",            # ISO: 2024-03-15
)

# Storno-related keywords (case-insensitive substrings)
_STORNO_KEYWORDS = ("storno", "storniert", "reversal", "reversed")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _normalize_header(s: str) -> str:
    """Strip non-ASCII chars for fuzzy header matching (handles encoding issues)."""
    return "".join(c for c in s.lower() if ord(c) < 128 and (c.isalnum() or c == " "))


def _build_column_map(header_line: str) -> dict[str, int]:
    """Build logical-field → column-index mapping from the CSV header.

    Supports any Swissquote CSV variant by matching header cell text against
    known column names (exact match first, then normalized fallback).
    """
    cleaned = header_line.strip().lstrip("\ufeff")
    cells = [c.strip().strip('"') for c in cleaned.split(";")]

    col_map: dict[str, int] = {}

    # Pass 1: exact match
    for idx, cell in enumerate(cells):
        field = HEADER_TO_FIELD.get(cell)
        if field and field not in col_map:
            col_map[field] = idx

    # Pass 2: normalized fallback for encoding issues (e.g. ü → replacement char)
    if not REQUIRED_FIELDS.issubset(col_map.keys()):
        norm_lookup = {_normalize_header(k): v for k, v in HEADER_TO_FIELD.items()}
        for idx, cell in enumerate(cells):
            norm = _normalize_header(cell)
            field = norm_lookup.get(norm)
            if field and field not in col_map:
                col_map[field] = idx

    missing = REQUIRED_FIELDS - col_map.keys()
    if missing:
        raise ValueError(
            f"CSV header missing required columns: {missing}. "
            f"Detected columns: {list(col_map.keys())}"
        )

    return col_map


def _parse_date(raw: str) -> date:
    """Parse a date string trying several common formats.

    Handles the real Swissquote format ``"DD-MM-YYYY HH:MM:SS"`` as well as
    legacy ``"DD.MM.YYYY"`` and ISO formats.

    Raises ``ValueError`` if none of the known formats match.
    """
    stripped = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(stripped, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unable to parse date: {raw!r}")


def _parse_number(raw: str | None) -> Decimal:
    """Parse a numeric string into ``Decimal``.

    Handles Swiss formatting (apostrophe as thousands separator), the
    special value ``"-"`` (returned as zero), and empty values.
    """
    if not raw or not raw.strip():
        return Decimal("0")
    cleaned = raw.strip()
    # Special value: single dash means zero/empty
    if cleaned == "-":
        return Decimal("0")
    # Remove apostrophe thousands separator (Swissquote Swiss locale)
    cleaned = cleaned.replace("'", "")
    # Handle comma/dot ambiguity
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[1]) <= 3:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        logger.warning("Unable to parse number: %r, defaulting to 0", raw)
        return Decimal("0")


def _classify_transaction(
    raw_type: str,
    type_map: dict[str, str],
) -> TransactionType:
    """Map a raw transaction type string to a ``TransactionType``.

    Tries exact match first (longest keys first), then returns OTHER.
    """
    stripped = raw_type.strip()
    if not stripped:
        logger.warning("Empty transaction type, mapping to OTHER")
        return TransactionType.OTHER

    # Try each known type (longest first to avoid partial matches)
    for key, canonical in sorted(type_map.items(), key=lambda kv: -len(kv[0])):
        if stripped == key:
            return TransactionType(canonical)

    logger.warning("Unknown transaction type: %r, mapping to OTHER", stripped)
    return TransactionType.OTHER


def _detect_locale(header_line: str) -> dict[str, str]:
    """Detect CSV locale from the header line.

    Returns the appropriate transaction-type mapping dictionary.

    Raises ``ValueError`` if the header does not match any known locale.
    """
    normalized = _normalize_header(header_line)

    if "datum" in normalized and "transaktionen" in normalized:
        return GERMAN_TRANSACTION_TYPES
    if "date" in normalized and "transactions" in normalized:
        return ENGLISH_TRANSACTION_TYPES

    raise ValueError(
        f"Unable to detect Swissquote CSV locale from header: {header_line!r}"
    )


def _is_storno_type(raw_type: str) -> bool:
    """Check if a raw transaction type indicates a storno/reversal."""
    lower = raw_type.lower()
    return any(kw in lower for kw in _STORNO_KEYWORDS)


# ── Main parser ─────────────────────────────────────────────────────────────


class SwissquoteCsvParser(BrokerParser):
    """Parses Swissquote CSV transaction exports into ``TaxStatement``."""

    # ── BrokerParser interface ───────────────────────────────────────────

    @property
    def name(self) -> str:
        return "Swissquote"

    @property
    def institution(self) -> Institution:
        return Institution(
            name=INSTITUTION_NAME,
            uid=INSTITUTION_UID,
            clearing_number=CLEARING_NUMBER,
        )

    def parse(
        self,
        file_content: str,
        tax_year: int,
        taxpayer: TaxPayer,
        portfolio_content: str | None = None,
        account_number: str = "",
    ) -> TaxStatement:
        """Parse a Swissquote CSV export into a ``TaxStatement``.

        Args:
            file_content: Full text of the CSV file.
            tax_year: Fiscal year for the statement.
            taxpayer: Taxpayer personal details.
            portfolio_content: Optional pdftotext output of portfolio PDF
                for year-end holdings and cash balances.
            account_number: Broker account number for statement ID.

        Returns:
            A ``TaxStatement`` with holdings, payments, cash balances,
            transactions, and expenses populated.
        """
        lines = file_content.splitlines()
        if not lines:
            raise ValueError("CSV file is empty")

        # 1. Detect locale and build column mapping from header
        type_map = _detect_locale(lines[0])
        col_map = _build_column_map(lines[0])

        # 2. Parse CSV rows into Transaction objects (with storno filtering)
        transactions = self._parse_rows(lines, type_map, col_map)

        # 3. Filter to the requested tax year
        year_transactions = [t for t in transactions if t.date.year == tax_year]

        # 4. Build a deduplicated security registry (ISIN → Security)
        securities = self._build_security_registry(year_transactions)

        # 5. Holdings and cash: from portfolio PDF or fallback to reconstruction
        portfolio_year: int | None = None
        if portfolio_content:
            portfolio_parser = PortfolioParser()
            holdings, cash_balances, portfolio_year = portfolio_parser.parse(portfolio_content)
        else:
            holdings = self._reconstruct_holdings(year_transactions, securities)
            cash_balances = self._compute_cash_balances(year_transactions)

        # 6. Extract security payments (dividends with WHT from Kosten)
        security_payments = self._extract_security_payments(
            year_transactions, securities
        )

        # 7. Extract expenses (custody fees)
        expenses = [
            t for t in year_transactions if t.type == TransactionType.CUSTODY_FEE
        ]

        # 8. Annotate interest on cash balances
        if portfolio_content:
            last_saldo = self._extract_last_saldo(lines, col_map, tax_year)
            self._annotate_interest_from_portfolio(cash_balances, last_saldo)
        # else: no interest annotation — CSV INTEREST rows use settlement dates
        # from the wrong year (Jan 1 of next year = previous year's interest)

        # 9. Annotate IBAN on cash balances
        if account_number:
            iban = compute_swissquote_iban(account_number)
            for cb in cash_balances:
                cb.iban = iban

        # 10. Fix dividend quantities using year-end holdings
        self._fix_dividend_quantities(
            security_payments, holdings, year_transactions
        )

        return TaxStatement(
            tax_year=tax_year,
            period_from=date(tax_year, 1, 1),
            period_to=date(tax_year, 12, 31),
            taxpayer=taxpayer,
            institution=self.institution,
            holdings=holdings,
            cash_balances=cash_balances,
            security_payments=security_payments,
            transactions=year_transactions,
            expenses=expenses,
            account_number=account_number,
            portfolio_year=portfolio_year,
        )

    # ── Internal helpers ─────────────────────────────────────────────────

    def _parse_rows(
        self,
        lines: Sequence[str],
        type_map: dict[str, str],
        col_map: dict[str, int],
    ) -> list[Transaction]:
        """Parse all CSV data lines into ``Transaction`` objects.

        Applies storno filtering: if any row's raw transaction type contains a
        storno keyword, all rows sharing that order number are removed.
        """
        reader = csv.reader(
            io.StringIO("\n".join(lines[1:])),
            delimiter=";",
        )

        # Minimum columns expected (max index in col_map + 1)
        min_cols = max(col_map.values()) + 1

        i_type = col_map["transaction_type"]
        i_order = col_map["order_number"]

        transactions: list[Transaction] = []
        storno_order_numbers: set[str] = set()
        raw_rows: list[tuple[int, list[str]]] = []

        # First pass: collect rows and identify storno order numbers
        for row_num, row in enumerate(reader, start=2):
            if not row or all(cell.strip() == "" for cell in row):
                continue

            if len(row) < min_cols:
                logger.warning(
                    "Row %d has %d columns (expected >= %d), skipping: %r",
                    row_num,
                    len(row),
                    min_cols,
                    row,
                )
                continue

            raw_type = row[i_type].strip()
            order_num = row[i_order].strip()

            if _is_storno_type(raw_type) and order_num:
                storno_order_numbers.add(order_num)

            raw_rows.append((row_num, row))

        # Second pass: parse rows, skipping storno-tainted order numbers
        for row_num, row in raw_rows:
            order_num = row[i_order].strip()
            raw_type = row[i_type].strip()

            # Skip rows whose order number is tainted by storno
            if order_num and order_num in storno_order_numbers:
                logger.info(
                    "Skipping row %d (order %s): storno-related", row_num, order_num
                )
                continue

            # Skip storno rows without order numbers
            if _is_storno_type(raw_type):
                continue

            try:
                tx = self._row_to_transaction(row, type_map, col_map, row_num)
                transactions.append(tx)
            except Exception:
                logger.warning(
                    "Failed to parse row %d, skipping: %r", row_num, row, exc_info=True
                )

        return transactions

    def _row_to_transaction(
        self,
        row: list[str],
        type_map: dict[str, str],
        col_map: dict[str, int],
        row_num: int,
    ) -> Transaction:
        """Convert a CSV row to a ``Transaction`` using the column map."""
        raw_date = row[col_map["date"]].strip()
        raw_order = row[col_map["order_number"]].strip()
        raw_type = row[col_map["transaction_type"]].strip()
        raw_name = row[col_map["name"]].strip()
        raw_isin = row[col_map["isin"]].strip()
        raw_qty = row[col_map["quantity"]].strip()
        raw_price = row[col_map["unit_price"]].strip()
        raw_costs = row[col_map["costs"]].strip()
        raw_interest = row[col_map["accrued_interest"]].strip()
        raw_net = row[col_map["net_amount"]].strip()
        raw_currency = row[col_map["currency"]].strip()

        tx_type = _classify_transaction(raw_type, type_map)
        tx_date = _parse_date(raw_date)

        security: Security | None = None
        if raw_isin:
            security = Security(
                isin=raw_isin,
                name=raw_name or raw_isin,
                currency=raw_currency or "CHF",
            )

        quantity = _parse_number(raw_qty) if raw_qty else None
        unit_price = _parse_number(raw_price) if raw_price else None
        accrued_interest = _parse_number(raw_interest) if raw_interest else None
        if accrued_interest == Decimal("0"):
            accrued_interest = None

        return Transaction(
            date=tx_date,
            type=tx_type,
            security=security,
            quantity=quantity,
            unit_price=unit_price,
            costs=_parse_number(raw_costs),
            net_amount=_parse_number(raw_net),
            currency=raw_currency or "CHF",
            order_number=raw_order or None,
            accrued_interest=accrued_interest,
        )

    @staticmethod
    def _build_security_registry(
        transactions: list[Transaction],
    ) -> dict[str, Security]:
        """Build a deduplicated ISIN → Security mapping."""
        registry: dict[str, Security] = {}
        for tx in transactions:
            if tx.security and tx.security.isin and tx.security.isin not in registry:
                registry[tx.security.isin] = tx.security
        return registry

    @staticmethod
    def _reconstruct_holdings(
        transactions: list[Transaction],
        securities: dict[str, Security],
    ) -> list[Holding]:
        """Reconstruct year-end holdings from buy/sell transaction flow.

        Fallback when no portfolio PDF is provided.
        """
        quantities: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

        for tx in transactions:
            if tx.security is None or tx.security.isin is None:
                continue
            isin = tx.security.isin
            if tx.type == TransactionType.BUY and tx.quantity:
                quantities[isin] += tx.quantity
            elif tx.type == TransactionType.SELL and tx.quantity:
                quantities[isin] -= tx.quantity

        holdings: list[Holding] = []
        for isin, qty in sorted(quantities.items()):
            if qty > 0:
                security = securities.get(isin)
                if security is None:
                    logger.warning(
                        "Security %s not found in registry, skipping holding", isin
                    )
                    continue
                holdings.append(Holding(security=security, quantity=qty))

        return holdings

    @staticmethod
    def _extract_security_payments(
        transactions: list[Transaction],
        securities: dict[str, Security],
    ) -> list[SecurityPayment]:
        """Extract dividend payments with WHT from the Kosten column.

        In the real 15-column format, withholding tax is embedded in the
        ``costs`` field of DIVIDEND rows (as a negative value). The
        ``net_amount`` already has WHT deducted, so:

            WHT = abs(costs)
            gross = net_amount + WHT
        """
        payments: list[SecurityPayment] = []
        for tx in transactions:
            if tx.type != TransactionType.DIVIDEND:
                continue
            if tx.security is None or tx.security.isin is None:
                continue

            isin = tx.security.isin
            security = securities.get(isin, tx.security)

            withholding_tax = abs(tx.costs)
            gross_amount = tx.net_amount + withholding_tax

            # Compute gross amount per unit when quantity is available
            amount_per_unit = None
            if tx.quantity and tx.quantity > 0:
                amount_per_unit = gross_amount / tx.quantity

            payments.append(
                SecurityPayment(
                    security=security,
                    payment_date=tx.date,
                    gross_amount=gross_amount,
                    currency=tx.currency,
                    withholding_tax=withholding_tax,
                    net_amount=tx.net_amount,
                    quantity=tx.quantity,
                    amount_per_unit=amount_per_unit,
                )
            )

        return payments

    @staticmethod
    def _compute_cash_balances(
        transactions: list[Transaction],
    ) -> list[CashBalance]:
        """Approximate year-end cash balances per currency.

        Fallback when no portfolio PDF is provided.
        """
        balances: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for tx in transactions:
            balances[tx.currency] += tx.net_amount

        return [
            CashBalance(currency=cur, balance=bal)
            for cur, bal in sorted(balances.items())
        ]

    @staticmethod
    def _fix_dividend_quantities(
        security_payments: list[SecurityPayment],
        holdings: list[Holding],
        transactions: list[Transaction],
    ) -> None:
        """Fix dividend quantities using year-end holdings and buy/sell history.

        Swissquote CSV reports dividend quantity as 1 (one payment event)
        rather than the actual share count. This method reconstructs the
        position at each dividend date to get the correct quantity.

        Strategy:
        - If a year-end holding exists: work backwards from year-end,
          adjusting for post-dividend buy/sell transactions.
        - If no year-end holding: reconstruct forward from buy/sell
          transactions in the CSV (works for same-year bought+sold).
        - If neither works: leave quantity as-is from CSV.
        """
        holdings_map = {h.security.isin: h.quantity for h in holdings}

        # Build buy/sell transactions by ISIN: (date, signed_qty)
        txns_by_isin: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)
        for t in transactions:
            if t.security and t.type in (TransactionType.BUY, TransactionType.SELL):
                qty = t.quantity or Decimal("0")
                if t.type == TransactionType.SELL:
                    qty = -abs(qty)
                txns_by_isin[t.security.isin].append((t.date, qty))

        for payment in security_payments:
            isin = payment.security.isin
            year_end_qty = holdings_map.get(isin)

            if year_end_qty is not None and year_end_qty > 0:
                # Reconstruct from year-end holding, adjusting for
                # buy/sell that happened AFTER this dividend
                buys_after = Decimal("0")
                sells_after = Decimal("0")
                for txn_date, signed_qty in txns_by_isin.get(isin, []):
                    if txn_date > payment.payment_date:
                        if signed_qty > 0:
                            buys_after += signed_qty
                        else:
                            sells_after += abs(signed_qty)
                qty_at_div = year_end_qty - buys_after + sells_after
            else:
                # No year-end holding — forward reconstruction
                txns = txns_by_isin.get(isin, [])
                if not txns:
                    continue
                qty_at_div = Decimal("0")
                for txn_date, signed_qty in sorted(txns, key=lambda x: x[0]):
                    if txn_date <= payment.payment_date:
                        qty_at_div += signed_qty

            if qty_at_div > 0:
                payment.quantity = qty_at_div
                payment.amount_per_unit = payment.gross_amount / qty_at_div

    @staticmethod
    def _extract_last_saldo(
        lines: Sequence[str],
        col_map: dict[str, int],
        tax_year: int,
    ) -> dict[str, Decimal]:
        """Extract the last running balance (Saldo) per currency from CSV rows.

        Scans year-filtered CSV rows to find the final Saldo value for each
        currency, by chronological date.  The CSV rows may not be in date
        order, so we track the latest date seen per currency.

        Used to compute accrued interest by comparing against portfolio
        year-end balances.

        Returns:
            Dict mapping currency code to last running balance.
        """
        i_date = col_map["date"]
        i_balance = col_map.get("balance")
        i_balance_ccy = col_map.get("balance_currency")
        # Fallback: balance_currency may not exist (older CSVs use the
        # net_amount currency column as the Saldo currency)
        i_ccy_fallback = col_map.get("currency")

        if i_balance is None:
            return {}

        last_saldo: dict[str, tuple[date, Decimal]] = {}
        reader = csv.reader(
            io.StringIO("\n".join(lines[1:])),
            delimiter=";",
        )
        min_cols = max(v for v in [i_date, i_balance, i_balance_ccy, i_ccy_fallback] if v is not None) + 1

        for row in reader:
            if not row or all(cell.strip() == "" for cell in row):
                continue
            if len(row) < min_cols:
                continue

            # Year filter
            try:
                tx_date = _parse_date(row[i_date].strip())
            except (ValueError, IndexError):
                continue
            if tx_date.year != tax_year:
                continue

            raw_balance = row[i_balance].strip() if i_balance < len(row) else ""
            if not raw_balance or raw_balance == "-":
                continue

            # Determine currency
            if i_balance_ccy is not None and i_balance_ccy < len(row):
                ccy = row[i_balance_ccy].strip()
            elif i_ccy_fallback is not None and i_ccy_fallback < len(row):
                ccy = row[i_ccy_fallback].strip()
            else:
                continue

            if not ccy:
                continue

            balance = _parse_number(raw_balance)
            prev = last_saldo.get(ccy)
            if prev is None or tx_date >= prev[0]:
                last_saldo[ccy] = (tx_date, balance)

        return {ccy: val for ccy, (_, val) in last_saldo.items()}

    @staticmethod
    def _annotate_interest_from_portfolio(
        cash_balances: list[CashBalance],
        last_saldo: dict[str, Decimal],
    ) -> None:
        """Compute interest as portfolio balance minus last CSV Saldo.

        The Swissquote CSV records interest on settlement date (Jan 1 of
        next year), so the CSV for year N contains year N-1's interest.
        The portfolio PDF shows the true year-end balance including accrued
        interest.  The difference = current year's interest.

        Args:
            cash_balances: Cash balances from portfolio (modified in place).
            last_saldo: Last running balance per currency from CSV rows.
        """
        for cb in cash_balances:
            saldo = last_saldo.get(cb.currency)
            if saldo is not None:
                interest = cb.balance - saldo
                cb.interest_earned = interest if interest > 0 else Decimal("0")
            # If currency is in portfolio but not in CSV, the entire balance
            # could be interest — but more likely it's a deposit. Report 0.

    @staticmethod
    def _annotate_interest(
        transactions: list[Transaction],
        cash_balances: list[CashBalance],
    ) -> None:
        """Add interest earned to the corresponding cash balance entries.

        .. deprecated::
            Used only by the Kontoauszug parser path, which calls this
            directly.  The CSV path now uses _annotate_interest_from_portfolio.
        """
        interest_by_currency: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for tx in transactions:
            if tx.type == TransactionType.INTEREST:
                interest_by_currency[tx.currency] += tx.net_amount

        balance_by_currency = {cb.currency: cb for cb in cash_balances}
        for currency, interest in interest_by_currency.items():
            if currency in balance_by_currency:
                balance_by_currency[currency].interest_earned = interest
            else:
                cash_balances.append(
                    CashBalance(
                        currency=currency,
                        balance=interest,
                        interest_earned=interest,
                    )
                )
