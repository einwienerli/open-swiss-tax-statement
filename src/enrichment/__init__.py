"""Enrichment: FX rates, Kursliste data, and CHF conversion."""

from src.enrichment.enricher import enrich
from src.enrichment.exchange_rates import (
    MissingRateError,
    get_rate,
    has_rates,
    load_rates,
)
from src.enrichment.kursliste_parser import (
    KurslisteEntry,
    load_bundled_kursliste,
    parse_kursliste,
    parse_kursliste_all,
    parse_kursliste_indexed,
)
from src.enrichment.rounding import round_to_005

__all__ = [
    "MissingRateError",
    "KurslisteEntry",
    "enrich",
    "get_rate",
    "has_rates",
    "load_rates",
    "load_bundled_kursliste",
    "parse_kursliste",
    "parse_kursliste_all",
    "parse_kursliste_indexed",
    "round_to_005",
]
