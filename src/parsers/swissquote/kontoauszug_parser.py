"""Swissquote Kontoauszug (account statement) PDF parser.

Transforms the pypdf layout-mode text of a Swissquote Kontoauszug PDF into a
``TaxStatement``.  This is an alternative input to the CSV parser, with
advantages: explicit WHT (Taxen), correct dividend quantities (Anzahl),
and per-currency year-end cash balances (Schlussbilanz).

The portfolio PDF remains required for year-end security holdings and ISIN
resolution on dividend rows (dividends only have "NAME (SYMBOL)", no ISIN).
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

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
    INSTITUTION_NAME,
    INSTITUTION_UID,
    KONTOAUSZUG_LABELS,
    compute_swissquote_iban,
)
from src.parsers.swissquote.portfolio_parser import PortfolioParser

logger = logging.getLogger(__name__)

# ── Locale detection ─────────────────────────────────────────────────────────


def _detect_kontoauszug_locale(text: str) -> str:
    """Detect Kontoauszug PDF locale from the period line.

    Returns ``'de'`` or ``'en'``.  Raises :class:`ValueError` for
    unsupported languages.
    """
    for locale, labels in KONTOAUSZUG_LABELS.items():
        if re.search(labels["period_pattern"], text):
            return locale
    raise ValueError(
        "Unsupported Swissquote Kontoauszug PDF language. "
        "Supported: German, English. "
        "Please switch your Swissquote account language to German or English."
    )


# ── Locale-driven regex builders ─────────────────────────────────────────────


def _build_period_re(labels: dict) -> re.Pattern:
    """Build regex to extract the statement period."""
    return re.compile(labels["period_pattern"])


def _build_client_name_re(labels: dict) -> re.Pattern:
    """Build regex to match client name after salutation."""
    salutations = "|".join(re.escape(s) for s in labels["salutations"])
    return re.compile(rf"(?:{salutations})\s+(.+)")


def _build_kunde_re(labels: dict) -> re.Pattern:
    """Build regex to extract the account number."""
    label = re.escape(labels["account_number_label"])
    return re.compile(rf"{label}\s+(\d+)")


def _build_currency_section_re(labels: dict) -> re.Pattern:
    """Build regex to match currency section headers."""
    prefix = re.escape(labels["currency_section_prefix"])
    return re.compile(rf"{prefix}\s*in\s*(\w+)")


def _build_detail_kv_re(labels: dict) -> re.Pattern:
    """Build regex to match key-value detail lines in transaction blocks."""
    keys = "|".join(re.escape(k) for k in labels["detail_keys"])
    return re.compile(rf"^\s+({keys}):\s*(.+)")


def _build_page_footer_re(labels: dict) -> re.Pattern:
    """Build regex to match page footer lines."""
    # Common Swissquote footer patterns (language-neutral bank name line)
    patterns = [r"Swissquote Bank (?:AG|Ltd).*(?:" + "|".join(labels["page_footer_patterns"]) + r")"]
    # Also match Customer Care line (present in both languages)
    patterns.append(r"Customer Care.*\d{3} \d{2} \d{2} \d{2}")
    # Add individual language-specific footer patterns
    for p in labels["page_footer_patterns"]:
        patterns.append(p)
    return re.compile("|".join(patterns))


def _build_page_header_re(labels: dict) -> re.Pattern:
    """Build regex to match page header lines."""
    patterns = list(labels["page_header_patterns"])
    # IBAN line is always present in headers
    patterns.append(r"IBAN\s*:")
    return re.compile("|".join(patterns))


def _build_section_summary_re(labels: dict) -> re.Pattern:
    """Build regex to match section summary lines."""
    prefixes = "|".join(re.escape(p) for p in labels["section_summary_prefixes"])
    return re.compile(rf"^\s*(?:{prefixes})\s*")


def _build_column_header_re(labels: dict) -> re.Pattern:
    """Build regex to match the column header line."""
    return re.compile(labels["column_header_pattern"])


# ── Language-neutral regexes (module-level) ──────────────────────────────────

# Date at start of a line (DD.MM.YYYY)
_RE_DATE_LINE = re.compile(r"^\s*(\d{2}\.\d{2}\.\d{4})\s+(.+)")

# Security name line with symbol: "NAME (SYMBOL)" -- continuation line
_RE_SECURITY_LINE = re.compile(r"^\s+(.+?)\s*\(([A-Z0-9. ]+)\)\s*$")

# FX detail line: "EUR - USD" or "USD - CHF" etc.
_RE_FX_PAIR = re.compile(r"^\s+([A-Z]{3})\s*-\s*([A-Z]{3})\s*$")
_RE_FX_RATE = re.compile(r"^\s+1\s+[A-Z]{3}\s*=\s*([\d.,\s]+)\s+[A-Z]{3}")

# Cover page: IBAN
_RE_IBAN = re.compile(r"IBAN\s+(CH[\d\s]+\d)")

# Cover page: per-currency end balances
_RE_END_BALANCE = re.compile(r"([\d']+\.\d{2})\s+(CHF|USD|EUR|GBP|DKK|SEK|NOK|JPY|CAD|AUD)")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _clean_number(raw: str) -> str:
    """Remove internal whitespace from numeric tokens (pypdf artifact).

    pypdf layout mode can insert spaces inside numbers, e.g. "69. 3" for 69.30
    or "1 28.70" for 128.70.  Strip all internal spaces.
    """
    return re.sub(r"\s+", "", raw.strip())


def _parse_number(raw: str | None) -> Decimal:
    """Parse a numeric string in Swiss format into ``Decimal``.

    Handles apostrophe thousands separator, internal-space pypdf artifacts,
    and the special value "-".
    """
    if not raw or not raw.strip():
        return Decimal("0")
    cleaned = _clean_number(raw)
    if cleaned == "-":
        return Decimal("0")
    # Remove apostrophe thousands separator
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



# Words that do not identify an issuer: legal forms, share classes, and
# listing descriptors as used in Swissquote security names.
_NAME_NOISE_WORDS = frozenset({
    "n", "na", "nam", "namen", "reg", "regd", "registered", "inh", "br",
    "ag", "sa", "se", "nv", "plc", "ltd", "inc", "corp", "co", "company",
    "holding", "holdings", "hldg", "group", "grp", "the",
    "ord", "shs", "sh", "adr", "ads", "rep",
})


def _name_tokens(name: str) -> frozenset[str]:
    """Significant lower-case word tokens of a security name.

    Strips a trailing ticker suffix like ``(NOVN)`` and noise words.
    """
    name = re.sub(r"\s*\([A-Z0-9.]+\)\s*$", "", name).lower()
    tokens = re.findall(r"[a-z0-9&]+", name.replace(".", ""))
    return frozenset(t for t in tokens if t not in _NAME_NOISE_WORDS)


def _match_isin_by_name(name: str, candidates: dict[str, str]) -> str | None:
    """Find the ISIN whose security name matches ``name``.

    Exact match of significant tokens wins; otherwise a one-sided subset
    match is accepted if it is unique.  Returns None when no or several
    candidates match.
    """
    wanted = _name_tokens(name)
    if not wanted:
        return None
    exact: set[str] = set()
    partial: set[str] = set()
    for isin, cand_name in candidates.items():
        tokens = _name_tokens(cand_name)
        if not tokens:
            continue
        if tokens == wanted:
            exact.add(isin)
        elif tokens <= wanted or wanted <= tokens:
            partial.add(isin)
    if len(exact) == 1:
        return exact.pop()
    if not exact and len(partial) == 1:
        return partial.pop()
    return None


def _parse_date(raw: str) -> date:
    """Parse DD.MM.YYYY date string."""
    cleaned = _clean_number(raw)  # Handle any space artifacts
    return datetime.strptime(cleaned, "%d.%m.%Y").date()



# ── Transaction block parsing ─────────────────────────────────────────────────


def _parse_block_details(
    detail_lines: list[str],
    detail_kv_re: re.Pattern,
    detail_keys_map: dict[str, str],
    page_footer_re: re.Pattern,
    page_header_re: re.Pattern,
) -> dict:
    """Parse the continuation lines of a transaction block.

    Returns a dict with extracted key-value pairs (using canonical keys)
    plus the security name and symbol if found.
    """
    details: dict[str, str] = {}
    name_parts: list[str] = []
    symbol: str | None = None

    for line in detail_lines:
        # Try key-value pattern first
        kv_match = detail_kv_re.match(line)
        if kv_match:
            key = kv_match.group(1).strip()
            value = kv_match.group(2).strip()
            canonical = detail_keys_map.get(key, key.lower())
            details[canonical] = value
            continue

        # Try security name with symbol — but skip if the "name" part is a
        # known detail key, which indicates it's a continuation of a detail
        # value (e.g. "Exchange (NYSE)" is part of "Trading Venue: New York
        # Stock / Exchange (NYSE)", not a security name).
        sec_match = _RE_SECURITY_LINE.match(line)
        if sec_match:
            candidate_name = sec_match.group(1).strip()
            if candidate_name not in detail_keys_map:
                name_parts.append(candidate_name)
                symbol = sec_match.group(2).strip()
                continue

        # FX pair line
        fx_match = _RE_FX_PAIR.match(line)
        if fx_match:
            details["fx_from"] = fx_match.group(1)
            details["fx_to"] = fx_match.group(2)
            continue

        # FX rate line
        rate_match = _RE_FX_RATE.match(line)
        if rate_match:
            details["fx_rate"] = rate_match.group(1)
            continue

        # Continuation of security name (no key, no symbol yet)
        stripped = line.strip()
        if stripped and not page_footer_re.search(stripped) and not page_header_re.search(stripped):
            # Could be a continuation of the security name
            # (e.g. "ALIBABA GROUP HOLDING ADR REP 8" on one line,
            #  "ORD (BABA)" on the next)
            sec_match2 = re.match(r"^\s+(.+?)\s*\(([A-Z0-9. ]+)\)\s*$", line)
            if sec_match2:
                cand = sec_match2.group(1).strip()
                if cand not in detail_keys_map:
                    name_parts.append(cand)
                    symbol = sec_match2.group(2).strip()
                # else: detail-key continuation (e.g. "Exchange (NYSE)") — skip
            elif stripped and not re.match(r"^[\d'.,\s]+$", stripped):
                # Text that doesn't look like a number -- probably name continuation
                name_parts.append(stripped)

    if name_parts:
        details["security_name"] = " ".join(name_parts)
    if symbol:
        details["symbol"] = symbol

    return details


# ── Main parser ───────────────────────────────────────────────────────────────


class SwissquoteKontoauszugParser(BrokerParser):
    """Parses Swissquote Kontoauszug (account statement) PDF text."""

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
        """Parse Kontoauszug text into a ``TaxStatement``.

        Args:
            file_content: pypdf layout-mode text of the Kontoauszug PDF.
            tax_year: Fiscal year for the statement.
            taxpayer: Taxpayer personal details.
            portfolio_content: pypdf text of portfolio PDF (required for
                year-end holdings and ISIN resolution).
            account_number: Broker account number.

        Returns:
            A populated ``TaxStatement``.
        """
        if not file_content or not file_content.strip():
            raise ValueError("Kontoauszug PDF text is empty")

        # Detect locale and build all locale-specific regexes
        locale = _detect_kontoauszug_locale(file_content)
        logger.info("Kontoauszug locale: %s", locale)
        labels = KONTOAUSZUG_LABELS[locale]

        period_re = _build_period_re(labels)
        client_name_re = _build_client_name_re(labels)
        kunde_re = _build_kunde_re(labels)
        currency_section_re = _build_currency_section_re(labels)
        detail_kv_re = _build_detail_kv_re(labels)
        page_footer_re = _build_page_footer_re(labels)
        page_header_re = _build_page_header_re(labels)
        section_summary_re = _build_section_summary_re(labels)
        column_header_re = _build_column_header_re(labels)

        detail_keys_map = labels["detail_keys"]
        transaction_types = labels["transaction_types"]
        fee_keyword = labels["fee_keyword"]
        saldo_label = labels["saldo_label"]
        address_exclude_prefixes = labels["address_exclude_prefixes"]

        # 1. Parse cover page
        cover_info = self._parse_cover_page(
            file_content,
            period_re=period_re,
            client_name_re=client_name_re,
            kunde_re=kunde_re,
            address_exclude_prefixes=address_exclude_prefixes,
        )

        # Use account number from cover page if not provided
        if not account_number and cover_info.get("account_number"):
            account_number = cover_info["account_number"]

        # 2. Split into per-currency sections and parse transactions
        sections = self._split_currency_sections(file_content, currency_section_re)
        logger.info("Kontoauszug currency sections: %d", len(sections))
        all_transactions: list[Transaction] = []
        all_payments: list[SecurityPayment] = []
        all_expenses: list[Transaction] = []
        cash_balances: list[CashBalance] = []
        symbol_isin_map: dict[str, str] = {}

        for currency, section_text in sections:
            blocks = self._assemble_blocks(
                section_text,
                page_footer_re=page_footer_re,
                page_header_re=page_header_re,
                section_summary_re=section_summary_re,
                column_header_re=column_header_re,
                saldo_label=saldo_label,
            )
            for block in blocks:
                result = self._parse_block(
                    block,
                    currency,
                    detail_kv_re=detail_kv_re,
                    detail_keys_map=detail_keys_map,
                    page_footer_re=page_footer_re,
                    page_header_re=page_header_re,
                    transaction_types=transaction_types,
                    fee_keyword=fee_keyword,
                )
                if result is None:
                    continue

                if isinstance(result, SecurityPayment):
                    all_payments.append(result)
                elif isinstance(result, CashBalance):
                    cash_balances.append(result)
                elif isinstance(result, Transaction):
                    if result.type == TransactionType.CUSTODY_FEE:
                        all_expenses.append(result)
                    all_transactions.append(result)
                    # Build symbol->ISIN map from buy/sell with ISINs
                    if (
                        result.security
                        and result.security.isin
                        and result.type in (TransactionType.BUY, TransactionType.SELL)
                    ):
                        # Store both with and without the ISIN
                        if hasattr(result, "_symbol") and result._symbol:
                            symbol_isin_map[result._symbol] = result.security.isin

        # 3. Holdings from portfolio PDF (required)
        portfolio_year: int | None = None
        if portfolio_content:
            portfolio_parser = PortfolioParser()
            holdings, portfolio_cash, portfolio_year = portfolio_parser.parse(
                portfolio_content
            )
            # Use portfolio cash if we didn't get Schlussbilanz from Kontoauszug
            if not cash_balances:
                cash_balances = portfolio_cash

            # Build symbol->ISIN map from portfolio holdings
            for h in holdings:
                if h.security.isin:
                    # Extract symbol from name if present
                    name = h.security.name
                    sym_match = re.search(r"\(([A-Z0-9.]+)\)\s*$", name)
                    if sym_match:
                        symbol_isin_map[sym_match.group(1)] = h.security.isin
        else:
            holdings = []

        # 4. Resolve ISINs on dividend payments and trades
        self._resolve_dividend_isins(
            all_payments, symbol_isin_map, holdings, all_transactions,
        )

        # 5. Build security registry from all sources
        securities: dict[str, Security] = {}
        for h in holdings:
            if h.security.isin:
                securities[h.security.isin] = h.security
        for tx in all_transactions:
            if tx.security and tx.security.isin:
                securities.setdefault(tx.security.isin, tx.security)

        # 6. Annotate interest on cash balances
        self._annotate_interest(all_transactions, cash_balances)

        # 7. Annotate IBAN
        if account_number:
            iban = compute_swissquote_iban(account_number)
            for cb in cash_balances:
                cb.iban = iban

        logger.info(
            "Kontoauszug result: %d transactions, %d payments, %d expenses",
            len(all_transactions), len(all_payments), len(all_expenses),
        )

        return TaxStatement(
            tax_year=tax_year,
            period_from=date(tax_year, 1, 1),
            period_to=date(tax_year, 12, 31),
            taxpayer=taxpayer,
            institution=self.institution,
            holdings=holdings,
            cash_balances=cash_balances,
            security_payments=all_payments,
            transactions=all_transactions,
            expenses=all_expenses,
            account_number=account_number,
            portfolio_year=portfolio_year,
        )

    # ── Cover page parsing ────────────────────────────────────────────────

    @staticmethod
    def _parse_cover_page(
        text: str,
        *,
        period_re: re.Pattern | None = None,
        client_name_re: re.Pattern | None = None,
        kunde_re: re.Pattern | None = None,
        address_exclude_prefixes: list[str] | None = None,
    ) -> dict:
        """Extract client info and period from the cover/first page.

        When called without explicit regex parameters (e.g. from
        ``extract_client_info``), locale is auto-detected and regexes
        are built from labels.
        """
        # Auto-detect locale and build regexes if not provided
        if period_re is None or client_name_re is None or kunde_re is None:
            try:
                locale = _detect_kontoauszug_locale(text)
            except ValueError:
                return {}
            labels = KONTOAUSZUG_LABELS[locale]
            period_re = period_re or _build_period_re(labels)
            client_name_re = client_name_re or _build_client_name_re(labels)
            kunde_re = kunde_re or _build_kunde_re(labels)
            if address_exclude_prefixes is None:
                address_exclude_prefixes = labels["address_exclude_prefixes"]

        if address_exclude_prefixes is None:
            address_exclude_prefixes = []

        info: dict[str, str | None] = {}

        # Period
        period_match = period_re.search(text)
        if period_match:
            info["period_from"] = period_match.group(1)
            info["period_to"] = period_match.group(2)

        # Account number (Kunde / Client)
        kunde_match = kunde_re.search(text)
        if kunde_match:
            info["account_number"] = kunde_match.group(1)

        # IBAN
        iban_match = _RE_IBAN.search(text)
        if iban_match:
            info["iban"] = iban_match.group(1).replace(" ", "")

        # Client name -- look for salutation + name pattern
        # Take the first occurrence with exactly first+last name
        for match in client_name_re.finditer(text):
            parts = match.group(1).strip().split()
            if len(parts) >= 2:
                info["first_name"] = parts[0]
                info["last_name"] = " ".join(parts[1:])
                break

        # Address -- look for Swiss ZIP+city pattern on right side of page.
        # The address block on the cover page is heavily indented (right column).
        # Match lines with significant leading whitespace + 4-digit ZIP + city.
        exclude_pattern = "|".join(re.escape(p) for p in address_exclude_prefixes) if address_exclude_prefixes else ""
        for zip_match in re.finditer(
            r"^(\s{20,})(\d{4})\s+([A-ZÄÖÜa-zäöü]\S+.*?)$", text, re.MULTILINE
        ):
            city = zip_match.group(3).strip()
            # Skip false positives like "2024 bis ..."
            if re.search(r"\d{2}\.\d{2}\.\d{4}", city):
                continue
            info["zip_code"] = zip_match.group(2)
            info["city"] = city
            # Try to get street from the line before (same indentation)
            pos = zip_match.start()
            preceding = text[:pos].rstrip()
            last_line = preceding.rsplit("\n", 1)[-1].strip()
            if (
                last_line
                and len(last_line) < 60
                and (not exclude_pattern or not re.match(exclude_pattern, last_line))
            ):
                info["street"] = last_line
            break

        return info

    @staticmethod
    def extract_client_info(text: str) -> dict[str, str]:
        """Extract client info for UI auto-population (matches PortfolioParser API)."""
        info = SwissquoteKontoauszugParser._parse_cover_page(text)
        result: dict[str, str] = {}
        for key in ("first_name", "last_name", "street", "zip_code", "city", "account_number"):
            if info.get(key):
                result[key] = str(info[key])
        return result

    @staticmethod
    def extract_year(text: str) -> int | None:
        """Extract tax year from the Kontoauszug period."""
        try:
            locale = _detect_kontoauszug_locale(text)
        except ValueError:
            return None
        labels = KONTOAUSZUG_LABELS[locale]
        period_re = _build_period_re(labels)
        match = period_re.search(text)
        if match:
            try:
                return _parse_date(match.group(2)).year
            except (ValueError, IndexError):
                pass
        return None

    # ── Currency section splitting ────────────────────────────────────────

    @staticmethod
    def _split_currency_sections(
        text: str,
        currency_section_re: re.Pattern | None = None,
    ) -> list[tuple[str, str]]:
        """Split text into (currency, section_text) tuples."""
        # Auto-detect locale and build regex if not provided
        if currency_section_re is None:
            try:
                locale = _detect_kontoauszug_locale(text)
            except ValueError:
                return []
            labels = KONTOAUSZUG_LABELS[locale]
            currency_section_re = _build_currency_section_re(labels)

        sections: list[tuple[str, str]] = []
        matches = list(currency_section_re.finditer(text))

        if not matches:
            return sections

        for i, match in enumerate(matches):
            currency = match.group(1)
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            section_text = text[start:end]
            sections.append((currency, section_text))

        return sections

    # ── Block assembly ────────────────────────────────────────────────────

    @staticmethod
    def _assemble_blocks(
        section_text: str,
        *,
        page_footer_re: re.Pattern | None = None,
        page_header_re: re.Pattern | None = None,
        section_summary_re: re.Pattern | None = None,
        column_header_re: re.Pattern | None = None,
        saldo_label: str = "SALDO",
    ) -> list[tuple[str, list[str]]]:
        """Group lines into transaction blocks.

        Each block starts with a date line (DD.MM.YYYY ...) and includes
        all subsequent indented continuation lines until the next date line.

        Returns list of (first_line, continuation_lines).
        """
        # Build default regexes for backward compatibility (used by tests that
        # call _assemble_blocks directly without explicit regexes)
        if page_footer_re is None or page_header_re is None or section_summary_re is None or column_header_re is None:
            try:
                locale = _detect_kontoauszug_locale(section_text)
            except ValueError:
                # Fall back to German defaults for backward compatibility
                locale = "de"
            labels = KONTOAUSZUG_LABELS[locale]
            page_footer_re = page_footer_re or _build_page_footer_re(labels)
            page_header_re = page_header_re or _build_page_header_re(labels)
            section_summary_re = section_summary_re or _build_section_summary_re(labels)
            column_header_re = column_header_re or _build_column_header_re(labels)
            saldo_label = labels.get("saldo_label", "SALDO")

        blocks: list[tuple[str, list[str]]] = []
        current_first: str | None = None
        current_details: list[str] = []

        for line in section_text.splitlines():
            # Skip empty lines, page footers/headers, section summaries, column headers
            stripped = line.strip()
            if not stripped:
                continue
            if page_footer_re.search(stripped):
                continue
            if page_header_re.search(stripped):
                continue
            if section_summary_re.match(line):
                continue
            if column_header_re.match(stripped):
                continue
            # Skip the "SALDO (CCY)" / "BALANCE (CCY)" continuation of column header
            if re.match(rf"^\s*{re.escape(saldo_label)}\s*\(", stripped):
                continue

            # Check if this is a date-starting line
            date_match = _RE_DATE_LINE.match(line)
            if date_match:
                # Save previous block if any
                if current_first is not None:
                    blocks.append((current_first, current_details))
                current_first = line
                current_details = []
            elif current_first is not None:
                # Continuation line
                current_details.append(line)

        # Don't forget last block
        if current_first is not None:
            blocks.append((current_first, current_details))

        return blocks

    # ── Block dispatch ────────────────────────────────────────────────────

    def _parse_block(
        self,
        block: tuple[str, list[str]],
        currency: str,
        *,
        detail_kv_re: re.Pattern | None = None,
        detail_keys_map: dict[str, str] | None = None,
        page_footer_re: re.Pattern | None = None,
        page_header_re: re.Pattern | None = None,
        transaction_types: dict[str, str] | None = None,
        fee_keyword: str = "Depotgebühren",
    ) -> Transaction | SecurityPayment | CashBalance | None:
        """Dispatch a transaction block to the appropriate parser."""
        first_line, detail_lines = block
        date_match = _RE_DATE_LINE.match(first_line)
        if not date_match:
            return None

        # Default to German labels when called without explicit regexes.
        # This only happens in tests that call _parse_block() directly on
        # German fixture blocks.  The production path via parse() always
        # threads the correct locale-specific regexes.
        if detail_kv_re is None or detail_keys_map is None or page_footer_re is None or page_header_re is None:
            labels = KONTOAUSZUG_LABELS["de"]
            detail_kv_re = detail_kv_re or _build_detail_kv_re(labels)
            detail_keys_map = detail_keys_map or labels["detail_keys"]
            page_footer_re = page_footer_re or _build_page_footer_re(labels)
            page_header_re = page_header_re or _build_page_header_re(labels)
        if transaction_types is None:
            transaction_types = KONTOAUSZUG_LABELS["de"]["transaction_types"]

        raw_date = date_match.group(1)
        rest = date_match.group(2)

        # Detect transaction type from the INFORMATION column
        # The type keyword appears right after the date
        type_keyword = rest.strip().split()[0] if rest.strip() else ""

        # Look up canonical type from the first word
        canonical_type = transaction_types.get(type_keyword)

        # Parse the detail lines for all block types
        details = _parse_block_details(
            detail_lines,
            detail_kv_re=detail_kv_re,
            detail_keys_map=detail_keys_map,
            page_footer_re=page_footer_re,
            page_header_re=page_header_re,
        )

        try:
            tx_date = _parse_date(raw_date)
        except ValueError:
            logger.warning("Cannot parse date %r, skipping block", raw_date)
            return None

        if canonical_type == "DIVIDEND":
            return self._parse_dividend_block(tx_date, rest, details, currency)
        elif canonical_type in ("BUY", "SELL"):
            return self._parse_trade_block(
                tx_date, canonical_type, rest, details, currency
            )
        elif canonical_type == "CLOSING_BALANCE":
            return self._parse_schlussbilanz(tx_date, rest, details, currency)
        elif canonical_type == "OPENING_BALANCE":
            return None  # Opening balance -- informational only
        elif canonical_type == "INTEREST":
            return self._parse_interest_block(tx_date, rest, details, currency)
        elif canonical_type == "CUSTODY_FEE":
            return self._parse_fee_block(tx_date, rest, details, currency)
        elif canonical_type == "FX":
            return self._parse_fx_block(tx_date, rest, details, currency)
        elif canonical_type == "PAYMENT":
            return None  # Payment -- informational only
        else:
            # Fee detection fallback: check detail lines for fee keyword
            # (sometimes the type is on the continuation line, not the first line)
            if canonical_type is None:
                if details.get("security_name", "").strip() == "" and any(
                    fee_keyword in dl for dl in detail_lines
                ):
                    return self._parse_fee_block(tx_date, rest, details, currency)
                for dl in detail_lines:
                    if fee_keyword in dl:
                        return self._parse_fee_block(tx_date, rest, details, currency)
            logger.info("Unknown block type %r, skipping", type_keyword)
            return None

    # ── Type-specific parsers ─────────────────────────────────────────────

    def _parse_dividend_block(
        self,
        tx_date: date,
        first_line_rest: str,
        details: dict,
        currency: str,
    ) -> SecurityPayment | None:
        """Parse a dividend block into SecurityPayment."""
        # Extract reference number from first line
        ref_match = re.search(r"(\d{6,})", first_line_rest)
        order_number = ref_match.group(1) if ref_match else None

        # Quantity (canonical key)
        quantity = _parse_number(details.get("quantity"))

        # Gross from amount (explicit in Kontoauszug)
        betrag_raw = details.get("amount", "")
        # Strip currency prefix: "CHF 198.00" -> "198.00"
        betrag_value = re.sub(r"^[A-Z]{3}\s*", "", betrag_raw)
        gross_amount = _parse_number(betrag_value)

        # Net from total
        total_raw = details.get("total", "")
        total_value = re.sub(r"^[A-Z]{3}\s*", "", total_raw)
        net_amount = _parse_number(total_value)

        # WHT = gross - net (the actual total deduction).
        # Do NOT use the "tax" field -- for US securities it reports only the
        # treaty-rate portion (15%), not the full statutory rate (30%).  The
        # correct WHT for Swiss tax reporting (E-Steuerauszug / DA-1) is the
        # full amount withheld: gross minus credited net.
        if gross_amount > Decimal("0") and net_amount > Decimal("0"):
            withholding_tax = gross_amount - net_amount
        else:
            # Fallback to tax if gross or net unavailable
            taxen_raw = details.get("tax", "")
            taxen_value = re.sub(r"^[A-Z]{3}\s*", "", taxen_raw)
            withholding_tax = _parse_number(taxen_value)
            if net_amount == Decimal("0") and gross_amount > Decimal("0"):
                net_amount = gross_amount - withholding_tax

        # Security info
        sec_name = details.get("security_name", "Unknown")
        symbol = details.get("symbol")
        isin = details.get("isin")  # Usually not present on dividend rows

        security = Security(
            isin=isin or "",
            name=f"{sec_name} ({symbol})" if symbol else sec_name,
            currency=currency,
        )

        # Compute amount per unit
        amount_per_unit = None
        if quantity and quantity > 0:
            amount_per_unit = gross_amount / quantity

        payment = SecurityPayment(
            security=security,
            payment_date=tx_date,
            gross_amount=gross_amount,
            currency=currency,
            withholding_tax=withholding_tax,
            net_amount=net_amount,
            quantity=quantity if quantity > 0 else None,
            amount_per_unit=amount_per_unit,
        )
        # Stash symbol for ISIN resolution
        payment._symbol = symbol  # type: ignore[attr-defined]
        payment._order_number = order_number  # type: ignore[attr-defined]
        return payment

    def _parse_trade_block(
        self,
        tx_date: date,
        canonical_type: str,
        first_line_rest: str,
        details: dict,
        currency: str,
    ) -> Transaction | None:
        """Parse a buy/sell block into Transaction."""
        tx_type = TransactionType.BUY if canonical_type == "BUY" else TransactionType.SELL

        ref_match = re.search(r"(\d{6,})", first_line_rest)
        order_number = ref_match.group(1) if ref_match else None

        quantity = _parse_number(details.get("quantity"))
        isin = details.get("isin")
        sec_name = details.get("security_name", "Unknown")
        symbol = details.get("symbol")

        # Price (strip currency prefix)
        preis_raw = details.get("price", "")
        preis_value = re.sub(r"^[A-Z]{3}\s*", "", preis_raw)
        unit_price = _parse_number(preis_value)

        # Commission
        komm_raw = details.get("commission", "")
        komm_value = re.sub(r"^[A-Z]{3}\s*", "", komm_raw)
        commission = _parse_number(komm_value)

        # Tax (exchange fees, not WHT)
        taxen_raw = details.get("tax", "")
        taxen_value = re.sub(r"^[A-Z]{3}\s*", "", taxen_raw)
        taxen = _parse_number(taxen_value)

        costs = commission + taxen

        # Amount (gross trade amount before commissions)
        betrag_raw = details.get("amount", "")
        betrag_value = re.sub(r"^[A-Z]{3}\s*", "", betrag_raw)
        betrag = _parse_number(betrag_value)

        # Net amount: for BUY it's -(betrag + costs), for SELL it's betrag - costs
        if tx_type == TransactionType.BUY:
            net_amount = -(betrag + costs)
        else:
            net_amount = betrag - costs

        # Determine trade currency from price or amount prefix
        trade_currency = currency
        for raw in (details.get("price", ""), details.get("amount", "")):
            ccy_match = re.match(r"^([A-Z]{3})\s", raw)
            if ccy_match:
                trade_currency = ccy_match.group(1)
                break

        security = Security(
            isin=isin or "",
            name=f"{sec_name} ({symbol})" if symbol else sec_name,
            currency=trade_currency,
        )

        tx = Transaction(
            date=tx_date,
            type=tx_type,
            security=security,
            quantity=quantity if quantity > 0 else None,
            unit_price=unit_price if unit_price > 0 else None,
            costs=costs,
            net_amount=net_amount,
            currency=currency,
            order_number=order_number,
        )
        tx._symbol = symbol  # type: ignore[attr-defined]
        return tx

    def _parse_fee_block(
        self,
        tx_date: date,
        first_line_rest: str,
        details: dict,
        currency: str,
    ) -> Transaction:
        """Parse a custody fee block into Transaction."""
        # Fee amount: look for number in first line (BELASTUNG/DEBIT column)
        numbers = re.findall(r"[\d']+\.\d{2}", first_line_rest)
        amount = Decimal("0")
        if numbers:
            amount = _parse_number(numbers[0])

        return Transaction(
            date=tx_date,
            type=TransactionType.CUSTODY_FEE,
            net_amount=-amount,  # Fees are negative (debit)
            currency=currency,
        )

    def _parse_interest_block(
        self,
        tx_date: date,
        first_line_rest: str,
        details: dict,
        currency: str,
    ) -> Transaction:
        """Parse an interest block into Transaction."""
        # Interest amount: look for number in GUTSCHRIFT/CREDIT column
        numbers = re.findall(r"[\d']+\.\d{2}", first_line_rest)
        amount = Decimal("0")
        if numbers:
            # The interest amount is typically the first number (credit)
            amount = _parse_number(numbers[0])

        return Transaction(
            date=tx_date,
            type=TransactionType.INTEREST,
            net_amount=amount,
            currency=currency,
        )

    def _parse_fx_block(
        self,
        tx_date: date,
        first_line_rest: str,
        details: dict,
        currency: str,
    ) -> Transaction:
        """Parse an FX exchange block."""
        # Determine if this is a credit or debit for this currency section
        numbers = re.findall(r"[\d']+\.\d{2}", first_line_rest)
        amount = Decimal("0")
        if numbers:
            amount = _parse_number(numbers[0])

        ref_match = re.search(r"(\d{6,})", first_line_rest)

        # Determine credit vs debit: check fx_from vs section currency
        fx_from = details.get("fx_from", "")

        # If this currency section matches fx_from, this is a debit (selling)
        # If it matches fx_to, this is a credit (buying)
        if fx_from == currency:
            tx_type = TransactionType.FX_DEBIT
            net_amount = -amount
        else:
            tx_type = TransactionType.FX_CREDIT
            net_amount = amount

        return Transaction(
            date=tx_date,
            type=tx_type,
            net_amount=net_amount,
            currency=currency,
            order_number=ref_match.group(1) if ref_match else None,
        )

    def _parse_schlussbilanz(
        self,
        tx_date: date,
        first_line_rest: str,
        details: dict,
        currency: str,
    ) -> CashBalance:
        """Parse closing balance into CashBalance."""
        # The balance amount is in the SALDO column (last number on the line)
        numbers = re.findall(r"[\d']+\.\d{2}", first_line_rest)
        balance = Decimal("0")
        if numbers:
            balance = _parse_number(numbers[-1])

        return CashBalance(
            currency=currency,
            balance=balance,
        )

    # ── ISIN resolution ───────────────────────────────────────────────────

    @staticmethod
    def _resolve_dividend_isins(
        payments: list[SecurityPayment],
        symbol_isin_map: dict[str, str],
        holdings: list[Holding],
        transactions: list[Transaction] | None = None,
    ) -> None:
        """Resolve ISINs on dividend payments (and trades) lacking one.

        Kontoauszug dividend rows (and often trade rows) carry only a name
        and ticker, e.g. ``NOVARTIS N (NOVN)``.  Sources, in order:

        1. Ticker -> ISIN from trades in the same Kontoauszug that had an ISIN
        2. Name match against portfolio holdings and trades with an ISIN

        Name matching compares significant word tokens (legal-form and
        share-class words like AG, N, SA, INC, ORD are ignored), so
        ``NOVARTIS N`` matches ``NOVARTIS AG`` while ``ABB`` does not match
        ``ABBVIE``.  An exact token match wins; otherwise a subset match is
        accepted only if exactly one candidate qualifies.

        An unresolved Swiss dividend would lose its Verrechnungssteuer
        claim, so failures are logged as warnings.
        """
        candidates: dict[str, str] = {}  # ISIN -> name
        for h in holdings:
            if h.security.isin:
                candidates.setdefault(h.security.isin, h.security.name)
        for tx in transactions or []:
            if tx.security and tx.security.isin:
                candidates.setdefault(tx.security.isin, tx.security.name)

        def resolve(name: str, symbol: str | None) -> str | None:
            if symbol and symbol in symbol_isin_map:
                return symbol_isin_map[symbol]
            return _match_isin_by_name(name, candidates)

        for payment in payments:
            if payment.security.isin:
                continue
            symbol = getattr(payment, "_symbol", None)
            isin = resolve(payment.security.name, symbol)
            if isin:
                payment.security = Security(
                    isin=isin,
                    name=payment.security.name,
                    currency=payment.security.currency,
                )
            else:
                logger.warning(
                    "Could not resolve ISIN for dividend: %s (%s)",
                    payment.security.name,
                    symbol,
                )

        for tx in transactions or []:
            if tx.security is None or tx.security.isin:
                continue
            if tx.type not in (TransactionType.BUY, TransactionType.SELL):
                continue
            isin = resolve(tx.security.name, getattr(tx, "_symbol", None))
            if isin:
                tx.security = Security(
                    isin=isin,
                    name=tx.security.name,
                    currency=tx.security.currency,
                )
            else:
                logger.warning(
                    "Could not resolve ISIN for trade: %s", tx.security.name,
                )

    # ── Interest annotation (reused pattern from CsvParser) ───────────────

    @staticmethod
    def _annotate_interest(
        transactions: list[Transaction],
        cash_balances: list[CashBalance],
    ) -> None:
        """Add interest earned to the corresponding cash balance entries."""
        interest_by_currency: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for tx in transactions:
            if tx.type == TransactionType.INTEREST:
                interest_by_currency[tx.currency] += tx.net_amount

        balance_by_currency = {cb.currency: cb for cb in cash_balances}
        for ccy, interest in interest_by_currency.items():
            if ccy in balance_by_currency:
                balance_by_currency[ccy].interest_earned = interest
            else:
                cash_balances.append(
                    CashBalance(
                        currency=ccy,
                        balance=interest,
                        interest_earned=interest,
                    )
                )
