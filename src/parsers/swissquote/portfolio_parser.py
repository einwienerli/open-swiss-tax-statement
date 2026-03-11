"""Swissquote Portfolio PDF parser (German and English).

Parses the text output of ``pypdf`` (layout mode) applied to a Swissquote
portfolio valuation PDF into year-end holdings and cash balances.

Handles the real Swissquote format with column headers, variable spacing,
and multi-line security names (e.g. long ADR names that wrap).
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation

from src.models.cash import CashBalance
from src.models.security import Holding, Security
from src.parsers.swissquote.constants import PORTFOLIO_LABELS

logger = logging.getLogger(__name__)

# ── Number parsing ───────────────────────────────────────────────────────────

_APOSTROPHE_RE = re.compile(r"'")


def _parse_amount(raw: str) -> Decimal:
    """Parse a Swiss-formatted number (apostrophe thousands, dot decimal).

    Examples: ``"6'576.00"`` → ``Decimal("6576.00")``,
              ``"-1'234.56"`` → ``Decimal("-1234.56")``.
    """
    cleaned = _APOSTROPHE_RE.sub("", raw.strip())
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        logger.warning("Unable to parse amount: %r, defaulting to 0", raw)
        return Decimal("0")


# ── Locale detection ────────────────────────────────────────────────────────


def _detect_portfolio_locale(text: str) -> str:
    """Detect portfolio PDF locale from the title.

    Returns ``'de'`` or ``'en'``.  Raises :class:`ValueError` for
    unsupported languages.
    """
    for locale, labels in PORTFOLIO_LABELS.items():
        if labels["title"] in text:
            return locale
    raise ValueError(
        "Unsupported Swissquote portfolio PDF language. "
        "Supported: German, English. "
        "Please switch your Swissquote account language to German or English."
    )


# ── Locale-driven regex builders ────────────────────────────────────────────


def _build_year_re(labels: dict) -> re.Pattern:
    """Build regex to extract the year from the portfolio title line."""
    title = re.escape(labels["title"])
    prefix = re.escape(labels["title_date_prefix"])
    return re.compile(rf"{title}\s+{prefix}\s+\d{{2}}\.\d{{2}}\.(\d{{4}})")


def _build_account_number_re(labels: dict) -> re.Pattern:
    """Build regex to extract the account number."""
    label = re.escape(labels["account_number_label"])
    return re.compile(rf"{label}\s+(\d+)")


def _build_account_owner_re(labels: dict) -> re.Pattern:
    """Build regex to extract the account owner name."""
    label = re.escape(labels["account_owner_label"])
    salutations = "|".join(re.escape(s) for s in labels["salutations"])
    return re.compile(rf"{label}\s+(?:{salutations})?\s*(.*)")


def _build_salutation_re(labels: dict) -> re.Pattern:
    """Build regex to strip salutation prefix from names."""
    salutations = "|".join(re.escape(s) for s in labels["salutations"])
    return re.compile(rf"^({salutations})\s+")


def _build_section_header_re(labels: dict) -> re.Pattern:
    """Build regex to match security section headers like 'Aktien in CHF'."""
    section_types = "|".join(re.escape(s) for s in labels["section_types"])
    return re.compile(rf"^({section_types})\s+in\s+([A-Z]{{3}})$")


def _build_cash_header_re(labels: dict) -> re.Pattern:
    """Build regex to match the cash section header."""
    header = re.escape(labels["cash_header"])
    return re.compile(rf"^{header}$")


def _build_cash_line_old_re(labels: dict) -> re.Pattern:
    """Build regex for old-format cash lines like 'Konto in CHF  balance  chf'."""
    prefix = re.escape(labels["cash_account_prefix"])
    return re.compile(
        rf"^{prefix}\s+in\s+([A-Z]{{3}})\s+"
        r"(-?[\d']+\.\d{2})\s+"
        r"(-?[\d']+\.\d{2})$"
    )


# ── Locale-neutral patterns (module-level) ──────────────────────────────────

# ISIN: 2 uppercase letters + 9 alphanumeric + 1 check digit (always numeric)
# The numeric check digit prevents matching 12-letter security names like "MERCADOLIBRE"
_ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")

# Date like 31.12.2025 (filtered out during number extraction)
_DATE_RE = re.compile(r"\d{2}\.\d{2}\.\d{4}")

# Any number with a decimal point (matches Swiss-formatted amounts)
_NUMBER_RE = re.compile(r"-?[\d']+\.\d+")

# Quantity at the start of stripped text
_QTY_RE = re.compile(r"^([\d']+(?:\.\d+)?)\s+")

# Cash line new format: "CHF  12345600  Kontokorrent (CHF)  605.89  1.00000  605.89  0.60"
_CASH_LINE_NEW_RE = re.compile(r"^\s*([A-Z]{3})\s+\d+\s+")

# ZIP + City line: "8008 Zurich"
_ZIP_CITY_RE = re.compile(r"^(\d{4})\s+(.+)$")


# ── Parser class ─────────────────────────────────────────────────────────────


class PortfolioParser:
    """Parses Swissquote portfolio PDF text into holdings and cash balances."""

    @staticmethod
    def extract_year(text: str) -> int | None:
        """Extract the statement year from the portfolio header.

        Supports both German (``Portfolio-Wertentwicklung per DD.MM.YYYY``)
        and English (``Portfolio performance at DD.MM.YYYY``) headers.
        Returns the year, or ``None`` if the header is not found.
        """
        try:
            locale = _detect_portfolio_locale(text)
        except ValueError:
            return None
        labels = PORTFOLIO_LABELS[locale]
        year_re = _build_year_re(labels)
        m = year_re.search(text)
        return int(m.group(1)) if m else None

    @staticmethod
    def extract_client_info(text: str) -> dict[str, str]:
        """Extract client details from the portfolio cover page.

        Supports both German and English portfolio PDFs.
        Returns dict with keys: first_name, last_name, street, zip_code,
        city, account_number (all strings, empty if not found).
        """
        try:
            locale = _detect_portfolio_locale(text)
        except ValueError:
            return {}

        labels = PORTFOLIO_LABELS[locale]
        account_number_re = _build_account_number_re(labels)
        account_owner_re = _build_account_owner_re(labels)
        salutation_re = _build_salutation_re(labels)
        title_anchor = labels["title"]
        salutations = labels["salutations"]

        info: dict[str, str] = {}

        # Extract account number
        m = account_number_re.search(text)
        if m:
            info["account_number"] = m.group(1)

        # Extract name from account owner field
        m = account_owner_re.search(text)
        if m:
            name = m.group(1).strip()
            # Remove salutation prefix if still present
            name = salutation_re.sub("", name).strip()
            parts = name.split(None, 1)
            if len(parts) == 2:
                info["first_name"] = parts[0]
                info["last_name"] = parts[1]
            elif len(parts) == 1:
                info["last_name"] = parts[0]

        # Extract address from the block before the portfolio title
        lines = text.split("\n")
        header_idx = None
        for i, line in enumerate(lines):
            if title_anchor in line:
                header_idx = i
                break

        if header_idx is not None:
            # Collect non-empty lines before the header (skip salutation+name lines)
            addr_lines = []
            for i in range(header_idx):
                stripped = lines[i].strip()
                if not stripped:
                    continue
                # Skip lines that start with any known salutation
                if any(stripped.startswith(s) for s in salutations):
                    continue
                addr_lines.append(stripped)

            # Last line before header is usually "8008 Zurich"
            # Second-to-last is usually the street
            if addr_lines:
                zip_match = _ZIP_CITY_RE.match(addr_lines[-1])
                if zip_match:
                    info["zip_code"] = zip_match.group(1)
                    info["city"] = zip_match.group(2)
                    if len(addr_lines) >= 2:
                        info["street"] = addr_lines[-2]

        return info

    def parse(self, text: str) -> tuple[list[Holding], list[CashBalance], int | None]:
        """Parse ``pypdf`` output of a Swissquote portfolio PDF.

        Args:
            text: Plain text extracted from the PDF via ``pypdf`` layout mode.

        Returns:
            A tuple of ``(holdings, cash_balances, year)``.  ``year`` is the
            statement year extracted from the header, or ``None`` if not found.
        """
        holdings: list[Holding] = []
        cash_balances: list[CashBalance] = []

        # Detect locale; fall back to trying all locales for fragments without title
        try:
            locale = _detect_portfolio_locale(text)
        except ValueError:
            if not text.strip():
                return [], [], None
            # Fallback: default to German for backwards compatibility
            # (supports inline test fixtures that start with "Liquidit\u00e4t" without a title)
            locale = "de"

        labels = PORTFOLIO_LABELS[locale]
        year_re = _build_year_re(labels)
        section_header_re = _build_section_header_re(labels)
        cash_header_re = _build_cash_header_re(labels)
        cash_line_old_re = _build_cash_line_old_re(labels)

        # Extract year
        m = year_re.search(text)
        year = int(m.group(1)) if m else None

        current_currency: str | None = None
        in_cash_section = False

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            # Check for security section header
            section_match = section_header_re.match(line)
            if section_match:
                current_currency = section_match.group(2)
                in_cash_section = False
                continue

            # Check for cash section header
            if cash_header_re.match(line):
                in_cash_section = True
                current_currency = None
                continue

            # Skip "Total" summary lines
            if line.startswith("Total"):
                continue

            # Parse cash lines
            if in_cash_section:
                cb = self._parse_cash_line(line, cash_line_old_re)
                if cb:
                    cash_balances.append(cb)
                continue

            # Parse security lines (only when inside a security section)
            if current_currency is not None:
                holding = self._parse_security_line(line, current_currency)
                if holding:
                    holdings.append(holding)

        return holdings, cash_balances, year

    @staticmethod
    def _parse_security_line(line: str, currency: str) -> Holding | None:
        """Try to parse a security line using ISIN as anchor.

        Works with any column layout: extracts quantity and name from before
        the ISIN, then picks the CHF valuation as the second-to-last number
        after the ISIN (last number is the percentage).
        """
        isin_match = _ISIN_RE.search(line)
        if not isin_match:
            return None

        isin = isin_match.group(1)
        before = line[:isin_match.start()].strip()
        after = line[isin_match.end():]

        # Extract quantity and name from before ISIN
        qty_match = _QTY_RE.match(before)
        if not qty_match:
            return None
        quantity = _parse_amount(qty_match.group(1))
        name = before[qty_match.end():].strip()

        if not name:
            return None

        # Extract numbers from after ISIN, excluding dates
        after_clean = _DATE_RE.sub("", after)
        numbers = _NUMBER_RE.findall(after_clean)

        # CHF value is second-to-last (last is percentage)
        if len(numbers) >= 2:
            chf_value = _parse_amount(numbers[-2])
        elif len(numbers) == 1:
            chf_value = _parse_amount(numbers[0])
        else:
            logger.warning("No CHF value found for %s, skipping", isin)
            return None

        return Holding(
            security=Security(isin=isin, name=name, currency=currency),
            quantity=quantity,
            tax_value_chf=chf_value,
        )

    @staticmethod
    def _parse_cash_line(line: str, cash_line_old_re: re.Pattern | None = None) -> CashBalance | None:
        """Try to parse a cash balance line.

        Supports two formats:
        - New: ``CHF  12345600  Kontokorrent (CHF)  605.89  1.00000  605.89  0.60``
        - Old: ``Konto in CHF  37'383.95  37'383.95``
        """
        # New format: currency + reference number + description + numbers
        new_match = _CASH_LINE_NEW_RE.match(line)
        if new_match:
            currency = new_match.group(1)
            numbers = _NUMBER_RE.findall(line)
            # Last 4 numbers: balance, exchange_rate, chf_value, percentage
            if len(numbers) >= 4:
                balance = _parse_amount(numbers[-4])
                chf_value = _parse_amount(numbers[-2])
                return CashBalance(
                    currency=currency,
                    balance=balance,
                    tax_value_chf=chf_value,
                )
            # Fewer numbers: try balance + chf_value as last two
            if len(numbers) >= 2:
                balance = _parse_amount(numbers[-2])
                chf_value = _parse_amount(numbers[-1])
                return CashBalance(
                    currency=currency,
                    balance=balance,
                    tax_value_chf=chf_value,
                )
            return None

        # Old format: "Konto in CHF  balance  chf_value" (locale-specific prefix)
        if cash_line_old_re is not None:
            old_match = cash_line_old_re.match(line)
            if old_match:
                currency = old_match.group(1)
                balance = _parse_amount(old_match.group(2))
                chf_value = _parse_amount(old_match.group(3))
                return CashBalance(
                    currency=currency,
                    balance=balance,
                    tax_value_chf=chf_value,
                )

        return None
