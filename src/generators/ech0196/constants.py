"""eCH-0196 XML constants: namespaces, schema paths, and enum values."""

from __future__ import annotations

from pathlib import Path

# --- Namespaces ---

NS_ECH0196 = "http://www.ech.ch/xmlns/eCH-0196/2"
NS_ECH0097 = "http://www.ech.ch/xmlns/eCH-0097/4"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"

NSMAP = {
    None: NS_ECH0196,  # default namespace
    "eCH-0097": NS_ECH0097,
    "xsi": NS_XSI,
}

# xsi:schemaLocation value (matches TrueWealth reference)
SCHEMA_LOCATION = (
    "http://www.ech.ch/xmlns/eCH-0196/2.2/eCH-0196-2-2.xsd "
    "http://www.ech.ch/xmlns/eCH-0097/4/eCH-0097-4-0.xsd"
)

# --- Schema paths ---

SCHEMA_DIR = Path(__file__).parent / "schema"
MAIN_XSD = SCHEMA_DIR / "eCH-0196-2-2.xsd"

# --- eCH-0196 enum values ---

# Quotation types
QUOTATION_PIECE = "PIECE"
QUOTATION_PERCENT = "PERCENT"

# Security categories
CATEGORY_SHARE = "SHARE"
CATEGORY_FUND = "FUND"
CATEGORY_BOND = "BOND"
CATEGORY_OTHER = "OTHER"

# Expense types (subset used by MVP)
EXPENSE_TYPE_CUSTODY_FEE = "4"  # Depotgebühren

# Minor version (eCH-0196 v2.2)
MINOR_VERSION = 22

# Fund name keywords for security category heuristic
FUND_KEYWORDS = frozenset({
    "ETF", "VANGUARD", "ISHARES", "FUND", "TRACKER", "INDEX",
    "SPDR", "LYXOR", "AMUNDI", "UCITS", "UBS ETF", "SWISSCANTO",
})
