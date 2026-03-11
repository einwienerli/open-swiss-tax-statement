"""Tests for the Swissquote Portfolio PDF text parser."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.parsers.swissquote.portfolio_parser import (
    PortfolioParser,
    _detect_portfolio_locale,
    _parse_amount,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestParseAmount:
    def test_simple(self):
        assert _parse_amount("1234.56") == Decimal("1234.56")

    def test_apostrophe_thousands(self):
        assert _parse_amount("6'576.00") == Decimal("6576.00")

    def test_large_number(self):
        assert _parse_amount("37'383.95") == Decimal("37383.95")

    def test_negative(self):
        assert _parse_amount("-1'234.56") == Decimal("-1234.56")

    def test_whitespace(self):
        assert _parse_amount("  1234.56  ") == Decimal("1234.56")

    def test_invalid_returns_zero(self):
        assert _parse_amount("abc") == Decimal("0")


class TestPortfolioParser:
    @pytest.fixture
    def parser(self):
        return PortfolioParser()

    @pytest.fixture
    def portfolio_text(self) -> str:
        return (FIXTURES_DIR / "swissquote_portfolio.txt").read_text(encoding="utf-8")

    def test_holdings_count(self, parser, portfolio_text):
        holdings, _, _ = parser.parse(portfolio_text)
        assert len(holdings) == 5

    def test_nestle_holding(self, parser, portfolio_text):
        holdings, _, _ = parser.parse(portfolio_text)
        nestle = next(h for h in holdings if h.security.isin == "CH0038863350")
        assert nestle.quantity == Decimal("70")
        assert nestle.security.name == "NESTLE SA"
        assert nestle.security.currency == "CHF"
        assert nestle.tax_value_chf == Decimal("7000.00")

    def test_roche_holding(self, parser, portfolio_text):
        holdings, _, _ = parser.parse(portfolio_text)
        roche = next(h for h in holdings if h.security.isin == "CH0012032048")
        assert roche.quantity == Decimal("25")
        assert roche.security.currency == "CHF"
        assert roche.tax_value_chf == Decimal("6250.00")

    def test_apple_holding(self, parser, portfolio_text):
        holdings, _, _ = parser.parse(portfolio_text)
        apple = next(h for h in holdings if h.security.isin == "US0378331005")
        assert apple.quantity == Decimal("50")
        assert apple.security.currency == "USD"
        assert apple.tax_value_chf == Decimal("8325.00")

    def test_vanguard_holding(self, parser, portfolio_text):
        holdings, _, _ = parser.parse(portfolio_text)
        vanguard = next(h for h in holdings if h.security.isin == "IE00B3XXRP09")
        assert vanguard.quantity == Decimal("200")
        assert vanguard.security.currency == "EUR"
        assert vanguard.tax_value_chf == Decimal("16625.00")

    def test_zurich_holding(self, parser, portfolio_text):
        holdings, _, _ = parser.parse(portfolio_text)
        zurich = next(h for h in holdings if h.security.isin == "CH0011075394")
        assert zurich.quantity == Decimal("10")
        assert zurich.security.currency == "EUR"

    def test_cash_balances_count(self, parser, portfolio_text):
        _, cash, _ = parser.parse(portfolio_text)
        assert len(cash) == 3

    def test_chf_cash(self, parser, portfolio_text):
        _, cash, _ = parser.parse(portfolio_text)
        chf = next(cb for cb in cash if cb.currency == "CHF")
        assert chf.balance == Decimal("37383.95")
        assert chf.tax_value_chf == Decimal("37383.95")

    def test_usd_cash(self, parser, portfolio_text):
        _, cash, _ = parser.parse(portfolio_text)
        usd = next(cb for cb in cash if cb.currency == "USD")
        assert usd.balance == Decimal("909.95")
        assert usd.tax_value_chf == Decimal("818.96")

    def test_eur_cash_negative(self, parser, portfolio_text):
        _, cash, _ = parser.parse(portfolio_text)
        eur = next(cb for cb in cash if cb.currency == "EUR")
        assert eur.balance == Decimal("-21594.90")
        assert eur.tax_value_chf == Decimal("-20515.16")

    def test_empty_text(self, parser):
        holdings, cash, year = parser.parse("")
        assert holdings == []
        assert cash == []
        assert year is None

    def test_no_securities_only_cash_new_format(self, parser):
        """New Swissquote cash format: currency + reference + description + numbers."""
        text = """Liquidität
      Währung    Referenz             Beschreibung                Wert    Wechselkurs    Bewertung in CHF       %
         CHF     12345600             Kontokorrent (CHF)      10'000.00       1.00000           10'000.00  100.00
         USD     12345601             Kontokorrent (USD)         500.00       0.90000              450.00    4.50
"""
        holdings, cash, _ = parser.parse(text)
        assert len(holdings) == 0
        assert len(cash) == 2
        chf = next(cb for cb in cash if cb.currency == "CHF")
        assert chf.balance == Decimal("10000.00")
        assert chf.tax_value_chf == Decimal("10000.00")

    def test_no_securities_only_cash_old_format(self, parser):
        """Old/simple cash format: Konto in CHF  balance  chf_value."""
        text = """Liquidität

Konto in CHF    10'000.00    10'000.00
Konto in USD    500.00    450.00
"""
        holdings, cash, _ = parser.parse(text)
        assert len(holdings) == 0
        assert len(cash) == 2

    def test_skips_total_lines(self, parser):
        """Total summary lines should not be parsed as securities or cash."""
        text = """Aktien in CHF
                 Anzahl Wertpapier                                 ISIN           Durchschnittspreis   Marktpreis         Kursdatum                                         Bewertung in CHF      %

                     70 NESTLE SA                                  CH0038863350            100.000      100.000          31.12.2025                                                7'000.00   11.47

                                                                                                             Total Aktien in CHF                                                  7'000.00   100.00
"""
        holdings, cash, _ = parser.parse(text)
        assert len(holdings) == 1
        assert holdings[0].security.isin == "CH0038863350"

    def test_multi_line_name_only_first_line_parsed(self, parser):
        """Securities with wrapped names: only the first line (with ISIN) is parsed."""
        text = """Aktien in USD
                 Anzahl Wertpapier                                 ISIN           Durchschnittspreis   Marktpreis         Kursdatum      Bewertung in USD   Wechselkurs     Bewertung in CHF      %

                     66 ALIBABA GROUP HOLDING ADR REP US01609W1027                        181.468       146.580          31.12.2025             9'674.28                           7'666.19    7.60
                        8 ORD
"""
        holdings, cash, _ = parser.parse(text)
        assert len(holdings) == 1
        assert holdings[0].security.isin == "US01609W1027"
        assert holdings[0].quantity == Decimal("66")
        assert holdings[0].security.name == "ALIBABA GROUP HOLDING ADR REP"
        assert holdings[0].tax_value_chf == Decimal("7666.19")


class TestExtractYear:
    def test_extracts_year_from_fixture(self):
        text = (FIXTURES_DIR / "swissquote_portfolio.txt").read_text(encoding="utf-8")
        assert PortfolioParser.extract_year(text) == 2025

    def test_extracts_year_from_header_line(self):
        text = "Portfolio-Wertentwicklung per 31.12.2024\n\nSome content"
        assert PortfolioParser.extract_year(text) == 2024

    def test_returns_none_for_missing_header(self):
        text = "Some random text\nwithout the portfolio header"
        assert PortfolioParser.extract_year(text) is None

    def test_returns_none_for_empty_text(self):
        assert PortfolioParser.extract_year("") is None

    def test_parse_returns_year(self):
        parser = PortfolioParser()
        text = (FIXTURES_DIR / "swissquote_portfolio.txt").read_text(encoding="utf-8")
        _, _, year = parser.parse(text)
        assert year == 2025


class TestDetectPortfolioLocale:
    def test_german(self):
        text = "Portfolio-Wertentwicklung per 31.12.2025\nsome content"
        assert _detect_portfolio_locale(text) == "de"

    def test_english(self):
        text = "Portfolio performance at 31.12.2025\nsome content"
        assert _detect_portfolio_locale(text) == "en"

    def test_unsupported_raises(self):
        with pytest.raises(ValueError, match="Unsupported"):
            _detect_portfolio_locale("Performance du portefeuille au 31.12.2025")

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="Unsupported"):
            _detect_portfolio_locale("")


class TestExtractYearEnglish:
    def test_extracts_year(self):
        text = "Portfolio performance at 31.12.2025\nSome content"
        assert PortfolioParser.extract_year(text) == 2025

    def test_returns_none_for_unsupported_locale(self):
        text = "Unknown format without header"
        assert PortfolioParser.extract_year(text) is None


class TestPortfolioParserEnglish:
    @pytest.fixture
    def parser(self):
        return PortfolioParser()

    @pytest.fixture
    def portfolio_text_en(self) -> str:
        return (FIXTURES_DIR / "swissquote_portfolio_en.txt").read_text(encoding="utf-8")

    def test_holdings_count(self, parser, portfolio_text_en):
        holdings, _, _ = parser.parse(portfolio_text_en)
        assert len(holdings) == 3  # BMW, VW, NOVO NORDISK

    def test_bmw_holding(self, parser, portfolio_text_en):
        holdings, _, _ = parser.parse(portfolio_text_en)
        bmw = next(h for h in holdings if h.security.isin == "DE0005190003")
        assert bmw.quantity == Decimal("100")
        assert bmw.security.name == "BMW ORD"
        assert bmw.security.currency == "EUR"
        assert bmw.tax_value_chf == Decimal("8688.83")

    def test_vw_holding(self, parser, portfolio_text_en):
        holdings, _, _ = parser.parse(portfolio_text_en)
        vw = next(h for h in holdings if h.security.isin == "DE0007664039")
        assert vw.quantity == Decimal("80")
        assert vw.security.name == "VOLKSWAGEN NV PRF"
        assert vw.security.currency == "EUR"
        assert vw.tax_value_chf == Decimal("7720.98")

    def test_novo_holding(self, parser, portfolio_text_en):
        holdings, _, _ = parser.parse(portfolio_text_en)
        novo = next(h for h in holdings if h.security.isin == "DK0062498333")
        assert novo.quantity == Decimal("221")
        assert novo.security.name == "NOVO NORDISK ORD"
        assert novo.security.currency == "DKK"
        assert novo.tax_value_chf == Decimal("8958.22")

    def test_cash_balances(self, parser, portfolio_text_en):
        _, cash, _ = parser.parse(portfolio_text_en)
        assert len(cash) == 2  # EUR, USD

    def test_eur_cash(self, parser, portfolio_text_en):
        _, cash, _ = parser.parse(portfolio_text_en)
        eur = next(cb for cb in cash if cb.currency == "EUR")
        assert eur.balance == Decimal("395.38")
        assert eur.tax_value_chf == Decimal("368.04")

    def test_usd_cash(self, parser, portfolio_text_en):
        _, cash, _ = parser.parse(portfolio_text_en)
        usd = next(cb for cb in cash if cb.currency == "USD")
        assert usd.balance == Decimal("18602.99")
        assert usd.tax_value_chf == Decimal("14741.57")

    def test_year(self, parser, portfolio_text_en):
        _, _, year = parser.parse(portfolio_text_en)
        assert year == 2025

    def test_extract_client_info_en(self):
        text = (FIXTURES_DIR / "swissquote_portfolio_en.txt").read_text(encoding="utf-8")
        info = PortfolioParser.extract_client_info(text)
        assert info["account_number"] == "999999"
        assert info["first_name"] == "Jane"
        assert info["last_name"] == "Doe"

    def test_extract_client_info_address(self):
        text = (FIXTURES_DIR / "swissquote_portfolio_en.txt").read_text(encoding="utf-8")
        info = PortfolioParser.extract_client_info(text)
        assert info["street"] == "Teststrasse 1"
        assert info["zip_code"] == "8000"
        assert info["city"] == "Zurich"
