"""Swissquote Bank SA parsers (CSV and Kontoauszug PDF)."""

from src.parsers.swissquote.csv_parser import SwissquoteCsvParser
from src.parsers.swissquote.kontoauszug_parser import SwissquoteKontoauszugParser
from src.parsers.swissquote.portfolio_parser import PortfolioParser

__all__ = ["SwissquoteCsvParser", "SwissquoteKontoauszugParser", "PortfolioParser"]
