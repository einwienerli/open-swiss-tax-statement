"""Constants for the Swissquote parsers."""

from __future__ import annotations

# ── Institution details ──────────────────────────────────────────────────────
INSTITUTION_NAME = "Swissquote Bank SA"
INSTITUTION_UID = "CHE-106.787.734"
CLEARING_NUMBER = "08750"

# ── IBAN ─────────────────────────────────────────────────────────────────────
# Swissquote IBAN format: CH12 0878 1 + account number (zero-padded to 10) + 00
# Total: 4 (country+check) + 5 (clearing) + 10 (account) + 2 (suffix) = 21 chars
# Example: account 123456 → CH12 0878 1000 0123 4560 0
#
# OPEN POINT: For longer account numbers (>7 digits), we assume the "00" suffix
# stays constant and the leading "0" of the prefix becomes part of the account
# number field. This needs validation against real Swissquote IBANs with longer
# account numbers.
SWISSQUOTE_IBAN_PREFIX = "CH1208781"
SWISSQUOTE_IBAN_SUFFIX = "00"
# Available digits between prefix and suffix: 21 - 9 - 2 = 10
_IBAN_ACCOUNT_WIDTH = 21 - len(SWISSQUOTE_IBAN_PREFIX) - len(SWISSQUOTE_IBAN_SUFFIX)


def compute_swissquote_iban(account_number: str) -> str:
    """Build Swissquote IBAN from account number.

    Format: CH12 0878 + account number zero-padded to 11 digits + 00 = 21 chars.
    The same IBAN is used for all cash accounts (CHF, EUR, USD).
    Verified against real Swissquote account statements.
    """
    return f"{SWISSQUOTE_IBAN_PREFIX}{account_number.zfill(_IBAN_ACCOUNT_WIDTH)}{SWISSQUOTE_IBAN_SUFFIX}"

# ── Header name → logical field mapping ─────────────────────────────────────
# Covers all known Swissquote CSV variants (German v1/v2, English).
# Used by _build_column_map() to resolve header names to logical fields.
HEADER_TO_FIELD: dict[str, str] = {
    # German
    "Datum": "date",
    "Auftrag #": "order_number",
    "Transaktionen": "transaction_type",
    "Name": "name",
    "ISIN": "isin",
    "Symbol": "symbol",
    "Anzahl": "quantity",
    "Stückpreis": "unit_price",
    "Währung Stückpreis": "unit_price_currency",
    "Kosten": "costs",
    "Aufgelaufene Zinsen": "accrued_interest",
    "Nettobetrag": "net_amount",
    "Währung Nettobetrag": "currency",
    "Nettobetrag in der Währung des Kontos": "net_amount_account_currency",
    "Saldo": "balance",
    "Währung Saldo": "balance_currency",
    "Währung": "currency",
    # English
    "Date": "date",
    "Order #": "order_number",
    "Transactions": "transaction_type",
    "Quantity": "quantity",
    "Unit Price": "unit_price",
    "Unit Price Currency": "unit_price_currency",
    "Costs": "costs",
    "Accrued Interest": "accrued_interest",
    "Net Amount": "net_amount",
    "Net Amount Currency": "currency",
    "Balance": "balance",
    "Balance Currency": "balance_currency",
    "Currency": "currency",
}

# Required logical fields for parsing a row
REQUIRED_FIELDS = {
    "date", "order_number", "transaction_type", "name", "isin",
    "quantity", "unit_price", "costs", "accrued_interest",
    "net_amount", "currency",
}

# ── Transaction type mapping ── English → canonical ─────────────────────────
ENGLISH_TRANSACTION_TYPES: dict[str, str] = {
    "Buy": "BUY",
    "Sell": "SELL",
    "Dividend": "DIVIDEND",
    "Interest": "INTEREST",
    "Interest on deposits": "INTEREST",
    "Custody Fee": "CUSTODY_FEE",
    "Custody Fees": "CUSTODY_FEE",
    "Tax": "TAX",
    "FX Credit": "FX_CREDIT",
    "FX Debit": "FX_DEBIT",
    "Reversal (Sale)": "OTHER",
    "Sale (Reversed)": "OTHER",
    "Rights Issuance": "OTHER",
}

# ── Transaction type mapping ── German → canonical ──────────────────────────
GERMAN_TRANSACTION_TYPES: dict[str, str] = {
    "Kauf": "BUY",
    "Verkauf": "SELL",
    "Dividende": "DIVIDEND",
    "Zins": "INTEREST",
    "Zinsen auf Einlagen": "INTEREST",
    "Depotgebühren": "CUSTODY_FEE",
    "Steuer": "TAX",
    "Devisengutschrift": "FX_CREDIT",
    "Devisenbelastung": "FX_DEBIT",
    "Fx-Gutschrift Comp.": "FX_CREDIT",
    "Fx-Belastung Comp.": "FX_DEBIT",
    "Storno (Verkauf)": "OTHER",
    "Verkauf (Storniert)": "OTHER",
    "Ausgabe von Anrechten": "OTHER",
}

# ── Portfolio PDF labels ─────────────────────────────────────────────────────
PORTFOLIO_LABELS: dict[str, dict] = {
    "de": {
        "title": "Portfolio-Wertentwicklung",
        "title_date_prefix": "per",
        "account_number_label": "Kontonummer",
        "account_owner_label": "Kontoinhaber",
        "salutations": ["Herrn", "Herr", "Frau"],
        "cash_header": "Liquidität",
        "cash_account_prefix": "Konto",
        "section_types": {
            "Aktien": "shares",
            "Anlagefonds": "funds",
            "Obligationen": "bonds",
            "Strukturierte Produkte": "structured",
            "ETF": "etf",
        },
    },
    "en": {
        "title": "Portfolio performance",                # confirmed from User 2
        "title_date_prefix": "at",                       # confirmed: "at 31.12.2025"
        "account_number_label": "Account number",        # confirmed from User 2
        "account_owner_label": "Account owner",          # confirmed from User 2
        "salutations": ["Mr.", "Mrs.", "Ms."],           # confirmed: "Mrs." from User 2
        "cash_header": "Cash",                           # confirmed from User 2
        "cash_account_prefix": "Current Account",        # unverified
        "section_types": {
            "Shares": "shares",                          # confirmed from User 2
            "Investment funds": "funds",                 # unverified
            "Bonds": "bonds",                            # unverified
            "Structured products": "structured",         # unverified
            "ETF": "etf",                                # likely unchanged (acronym)
        },
    },
}

# Validation status of portfolio section type mappings.
# "confirmed" = verified against a real Swissquote export.
# "unverified" = added from documentation, not yet seen in a real file.
PORTFOLIO_SECTION_VALIDATION: dict[str, dict[str, str]] = {
    "de": {
        "Aktien": "confirmed",
        "Anlagefonds": "confirmed",
        "Obligationen": "confirmed",
        "Strukturierte Produkte": "unverified",
        "ETF": "confirmed",
    },
    "en": {
        "Shares": "confirmed",
        "Investment funds": "unverified",
        "Bonds": "unverified",
        "Structured products": "unverified",
        "ETF": "confirmed",
    },
}

# ── Kontoauszug PDF labels ───────────────────────────────────────────────────
KONTOAUSZUG_LABELS: dict[str, dict] = {
    "de": {
        "period_pattern": r"vom\s+(\d{2}\.\d{2}\.\d{4})\s+bis\s+(\d{2}\.\d{2}\.\d{4})",
        "account_number_label": "Kunde",
        "salutations": ["Herrn", "Herr", "Frau"],
        "currency_section_prefix": "Kontoauszug",
        "page_footer_patterns": [
            r"Seite \d+ / \d+",
            r"Dieser Transaktionsbeleg",
            r"Bescheid ohne Unterschrift",
        ],
        "page_header_patterns": [
            r"Dokument erstellt am",
            r"Herrn\s+\w+",
            r"Vom \d{2}\.\d{2}\.\d{4} bis",
        ],
        "section_summary_prefixes": ["Saldo per", "Total Belastung", "Total Gutschrift"],
        "column_header_pattern": r"DATUM\s+INFORMATION\s+REFERENZ",
        "saldo_label": "SALDO",
        "address_exclude_prefixes": ["Herrn", "Frau", "Dokument", "Kontoauszug"],
        "transaction_types": {
            "Dividende": "DIVIDEND",
            "Kauf": "BUY",
            "Verkauf": "SELL",
            "Schlussbilanz": "CLOSING_BALANCE",
            "Anfangsbestand": "OPENING_BALANCE",
            "Habenzinsen": "INTEREST",
            "Depotgebühren": "CUSTODY_FEE",
            "Automatisierter": "FX",
            "Auszahlung": "PAYMENT",                     # German equivalent of "Payment to"
        },
        "detail_keys": {
            "Anzahl": "quantity",
            "Betrag": "amount",
            "Taxen": "tax",
            "Total": "total",
            "Preis": "price",
            "Kommission": "commission",
            "ISIN": "isin",
            "Handelsplatz": "exchange",
            "Handelsplätze": "exchange",
        },
        "fee_keyword": "Depotgebühren",
    },
    "en": {
        "period_pattern": r"from\s+(\d{2}\.\d{2}\.\d{4})\s+to\s+(\d{2}\.\d{2}\.\d{4})",
        "account_number_label": "Client",                # confirmed from User 2
        "salutations": ["Mr.", "Mrs.", "Ms."],           # confirmed: "Mrs." from User 2
        "currency_section_prefix": "Account statement",  # confirmed from User 2
        "page_footer_patterns": [
            r"Page \d+ / \d+",                           # confirmed from User 2
            r"This (?:notification|transaction)",         # confirmed from User 2
            r"Advice without signature",                  # confirmed from User 2
        ],
        "page_header_patterns": [
            r"Document (?:created|generated) on",        # confirmed from User 2
            r"(?:Mr\.|Mrs\.|Ms\.)\s+\w+",
            r"From \d{2}\.\d{2}\.\d{4} to",
        ],
        "section_summary_prefixes": [
            "Balance at",                                # confirmed from User 2 (not "Balance as of")
            "Totaldebit",                                # confirmed from User 2 (no space)
            "Totalcredit",                               # confirmed from User 2 (no space)
            "Opening balance",                           # confirmed from User 2
            "Closing balance",                           # confirmed from User 2
        ],
        "column_header_pattern": r"DATE\s+INFORMATION\s+REFERENCE",  # confirmed from User 2
        "saldo_label": "BALANCE",                        # confirmed from User 2
        "address_exclude_prefixes": ["Mr.", "Mrs.", "Ms.", "Document", "Account"],
        "transaction_types": {
            "Dividend": "DIVIDEND",                      # confirmed from User 2
            "Buy": "BUY",                                # unverified for kontoauszug
            "Sell": "SELL",                              # confirmed from User 2
            "Closing": "CLOSING_BALANCE",                # confirmed from User 2
            "Opening": "OPENING_BALANCE",                # confirmed from User 2
            "Credit": "INTEREST",                        # unverified (no interest rows in User 2)
            "Safecustody": "CUSTODY_FEE",                # confirmed from User 2 ("Safecustody Fees debit")
            "Automated": "FX",                           # confirmed from User 2
            "Payment": "PAYMENT",                        # confirmed from User 2 ("Payment to")
        },
        "detail_keys": {
            "Quantity": "quantity",                       # confirmed from User 2
            "Amount": "amount",                          # confirmed from User 2
            "Tax": "tax",                                # confirmed from User 2
            "Taxes": "tax",                              # variant (not seen in User 2, kept for safety)
            "Total": "total",                            # same in both languages
            "Price": "price",                            # confirmed from User 2
            "Commission": "commission",                  # confirmed from User 2
            "ISIN": "isin",
            "Exchange": "exchange",                      # unverified
            "Trading Venue": "exchange",                 # confirmed from User 2 (capital V)
        },
        "fee_keyword": "Safecustody",                    # confirmed from User 2 ("Safecustody Fees debit")
    },
}
