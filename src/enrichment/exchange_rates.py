"""ESTV official year-end exchange rates for CHF conversion."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

_DATA_FILE = Path(__file__).parent / "data" / "estv_rates.json"
_cache: dict[int, dict[str, Decimal]] | None = None


class MissingRateError(Exception):
    """Raised when no exchange rate is available for a currency/year."""


def _load_all() -> dict[int, dict[str, Decimal]]:
    """Load all rates from the bundled JSON file."""
    global _cache
    if _cache is not None:
        return _cache
    with _DATA_FILE.open() as f:
        raw = json.load(f)
    _cache = {
        int(year): {cur: Decimal(rate) for cur, rate in rates.items()}
        for year, rates in raw.items()
    }
    return _cache


def has_rates(year: int) -> bool:
    """Check if bundled FX rates exist for a given year."""
    return year in _load_all()


def load_rates(year: int) -> dict[str, Decimal]:
    """Load year-end FX rates for a given year.

    Returns a dict mapping currency code to CHF rate (per 1 unit).
    CHF is always implicitly 1.
    """
    all_rates = _load_all()
    if year not in all_rates:
        raise MissingRateError(f"No exchange rates available for year {year}")
    return all_rates[year]


def get_rate(currency: str, year: int) -> Decimal:
    """Get the CHF exchange rate for a single currency and year.

    CHF returns Decimal("1") without requiring bundled data.
    """
    if currency == "CHF":
        return Decimal("1")
    rates = load_rates(year)
    if currency not in rates:
        raise MissingRateError(
            f"No exchange rate for {currency} in year {year}"
        )
    return rates[currency]
