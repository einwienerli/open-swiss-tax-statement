from __future__ import annotations

from enum import Enum


class TransactionType(Enum):
    """Classification of financial transactions."""

    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    INTEREST = "INTEREST"
    CUSTODY_FEE = "CUSTODY_FEE"
    TAX = "TAX"
    FX_CREDIT = "FX_CREDIT"
    FX_DEBIT = "FX_DEBIT"
    OTHER = "OTHER"


class IncomeCategory(Enum):
    """Swiss tax income categories (Rubrik).

    RUBRIK_A: Subject to 35% Swiss withholding tax (Verrechnungssteuer).
    RUBRIK_B: Not subject to Swiss withholding tax.
    """

    RUBRIK_A = "A"
    RUBRIK_B = "B"


class Currency(Enum):
    """ISO 4217 currency codes commonly used in Swiss tax statements."""

    CHF = "CHF"
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    JPY = "JPY"
    CAD = "CAD"
    AUD = "AUD"
    SEK = "SEK"
    NOK = "NOK"
    DKK = "DKK"
    HKD = "HKD"
    SGD = "SGD"
