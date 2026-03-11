"""Tests for the Swissquote Kontoauszug (account statement) PDF parser."""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from src.models.cash import CashBalance
from src.models.enums import TransactionType
from src.models.security import Holding, Security, SecurityPayment
from src.models.taxpayer import TaxPayer
from src.models.transaction import Transaction
from src.parsers.swissquote.kontoauszug_parser import (
    SwissquoteKontoauszugParser,
    _clean_number,
    _detect_kontoauszug_locale,
    _parse_number,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def kontoauszug_text() -> str:
    path = os.path.join(FIXTURE_DIR, "swissquote_kontoauszug.txt")
    with open(path) as f:
        return f.read()


@pytest.fixture
def taxpayer() -> TaxPayer:
    return TaxPayer(
        first_name="Max",
        last_name="Muster",
        street="Musterstrasse 42",
        zip_code="8001",
        city="Zürich",
        canton="ZH",
    )


@pytest.fixture
def parser() -> SwissquoteKontoauszugParser:
    return SwissquoteKontoauszugParser()


@pytest.fixture
def portfolio_holdings() -> list[Holding]:
    """Simulate portfolio PDF holdings for ISIN resolution."""
    return [
        Holding(
            security=Security(
                isin="CH0012005267",
                name="NOVARTIS N",
                currency="CHF",
            ),
            quantity=Decimal("60"),
        ),
        Holding(
            security=Security(
                isin="US09075V1026",
                name="ALIBABA GROUP HOLDING ADR REP 8 ORD",
                currency="USD",
            ),
            quantity=Decimal("66"),
        ),
        Holding(
            security=Security(
                isin="US7170811035",
                name="PFIZER ORD",
                currency="USD",
            ),
            quantity=Decimal("100"),
        ),
        Holding(
            security=Security(
                isin="US0311621009",
                name="AMGEN ORD",
                currency="USD",
            ),
            quantity=Decimal("8"),
        ),
        Holding(
            security=Security(
                isin="DE0007164600",
                name="SAP ORD",
                currency="EUR",
            ),
            quantity=Decimal("50"),
        ),
    ]


@pytest.fixture
def portfolio_text(portfolio_holdings: list[Holding]) -> str:
    """Minimal portfolio PDF text for testing.

    We don't actually parse this — the test mocks PortfolioParser.parse().
    """
    return "dummy portfolio text"


# ── TestCleanNumber ───────────────────────────────────────────────────────────


class TestCleanNumber:
    """Test _clean_number() handling of pypdf artifacts."""

    def test_normal_number(self):
        assert _clean_number("128.70") == "128.70"

    def test_space_in_decimal(self):
        """pypdf artifact: "69. 3" should become "69.3"."""
        assert _clean_number("69. 3") == "69.3"

    def test_space_in_integer(self):
        """pypdf artifact: "1 28.70" should become "128.70"."""
        assert _clean_number("1 28.70") == "128.70"

    def test_multiple_spaces(self):
        assert _clean_number("2 132. 77") == "2132.77"

    def test_leading_trailing_spaces(self):
        assert _clean_number("  42.00  ") == "42.00"

    def test_empty_string(self):
        assert _clean_number("") == ""


class TestParseNumber:
    """Test _parse_number() with Swiss formatting and pypdf artifacts."""

    def test_simple(self):
        assert _parse_number("128.70") == Decimal("128.70")

    def test_swiss_thousands(self):
        assert _parse_number("2'132.77") == Decimal("2132.77")

    def test_pypdf_space_artifact(self):
        """69. 3 → 69.3 → Decimal("69.3")."""
        assert _parse_number("69. 3") == Decimal("69.3")

    def test_dash(self):
        assert _parse_number("-") == Decimal("0")

    def test_empty(self):
        assert _parse_number("") == Decimal("0")

    def test_none(self):
        assert _parse_number(None) == Decimal("0")

    def test_large_number_with_spaces(self):
        assert _parse_number("2 132. 77") == Decimal("2132.77")


# ── TestCoverPageParsing ─────────────────────────────────────────────────────


class TestCoverPageParsing:
    """Test cover page parsing."""

    def test_account_number(self, kontoauszug_text, parser):
        info = parser._parse_cover_page(kontoauszug_text)
        assert info["account_number"] == "123456"

    def test_period(self, kontoauszug_text, parser):
        info = parser._parse_cover_page(kontoauszug_text)
        assert info["period_from"] == "01.01.2024"
        assert info["period_to"] == "31.12.2024"

    def test_iban(self, kontoauszug_text, parser):
        info = parser._parse_cover_page(kontoauszug_text)
        assert info["iban"] == "CH1208781000012345600"

    def test_client_name(self, kontoauszug_text, parser):
        info = parser._parse_cover_page(kontoauszug_text)
        assert info["first_name"] == "Max"
        assert info["last_name"] == "Muster"


class TestExtractClientInfo:
    """Test extract_client_info() for UI auto-population."""

    def test_returns_dict(self, kontoauszug_text):
        info = SwissquoteKontoauszugParser.extract_client_info(kontoauszug_text)
        assert isinstance(info, dict)
        assert info["account_number"] == "123456"
        assert info["first_name"] == "Max"
        assert info["last_name"] == "Muster"

    def test_extract_year(self, kontoauszug_text):
        year = SwissquoteKontoauszugParser.extract_year(kontoauszug_text)
        assert year == 2024


# ── TestCurrencySections ─────────────────────────────────────────────────────


class TestCurrencySections:
    """Test currency section detection and splitting."""

    def test_three_sections(self, kontoauszug_text, parser):
        sections = parser._split_currency_sections(kontoauszug_text)
        currencies = [ccy for ccy, _ in sections]
        assert currencies == ["CHF", "USD", "EUR"]

    def test_section_content_not_empty(self, kontoauszug_text, parser):
        sections = parser._split_currency_sections(kontoauszug_text)
        for _, text in sections:
            assert len(text.strip()) > 0


# ── TestBlockAssembly ────────────────────────────────────────────────────────


class TestBlockAssembly:
    """Test multi-line block grouping."""

    def test_chf_section_blocks(self, kontoauszug_text, parser):
        sections = parser._split_currency_sections(kontoauszug_text)
        chf_section = next(text for ccy, text in sections if ccy == "CHF")
        blocks = parser._assemble_blocks(chf_section)
        # Expected: Anfangsbestand, Dividende, 4x Depotgebühren, Habenzinsen, Schlussbilanz
        assert len(blocks) >= 7

    def test_multi_line_dividend_block(self, kontoauszug_text, parser):
        sections = parser._split_currency_sections(kontoauszug_text)
        chf_section = next(text for ccy, text in sections if ccy == "CHF")
        blocks = parser._assemble_blocks(chf_section)
        # Second block should be the Dividende with detail lines
        div_block = blocks[1]
        first_line, details = div_block
        assert "Dividende" in first_line
        assert len(details) >= 4  # NOVARTIS, Anzahl, Betrag, Taxen, Total

    def test_multi_line_security_name(self, kontoauszug_text, parser):
        """ALIBABA GROUP HOLDING ADR REP 8 / ORD (BABA) wraps across lines."""
        sections = parser._split_currency_sections(kontoauszug_text)
        usd_section = next(text for ccy, text in sections if ccy == "USD")
        blocks = parser._assemble_blocks(usd_section)
        # First dividend in USD section should be ALIBABA
        div_block = blocks[1]  # After Anfangsbestand
        _, details = div_block
        combined = " ".join(d.strip() for d in details)
        assert "ALIBABA" in combined
        assert "BABA" in combined


# ── TestDividendParsing ──────────────────────────────────────────────────────


class TestDividendParsing:
    """Test dividend block parsing."""

    def test_novartis_dividend(self, kontoauszug_text, parser):
        sections = parser._split_currency_sections(kontoauszug_text)
        chf_section = next(text for ccy, text in sections if ccy == "CHF")
        blocks = parser._assemble_blocks(chf_section)

        # Find and parse the Dividende block
        for block in blocks:
            if "Dividende" in block[0]:
                result = parser._parse_block(block, "CHF")
                break
        else:
            pytest.fail("No Dividende block found")

        assert isinstance(result, SecurityPayment)
        assert result.gross_amount == Decimal("198.00")
        # WHT = gross - net = 198.00 - 128.70 = 69.30
        assert result.withholding_tax == Decimal("69.30")
        assert result.net_amount == Decimal("128.70")
        assert result.quantity == Decimal("60")
        assert result.currency == "CHF"
        assert "NOVARTIS" in result.security.name

    def test_wht_computed_from_gross_minus_net(self, kontoauszug_text, parser):
        """WHT should be gross - net, not the Taxen field (which may understate)."""
        sections = parser._split_currency_sections(kontoauszug_text)
        chf_section = next(text for ccy, text in sections if ccy == "CHF")
        blocks = parser._assemble_blocks(chf_section)

        for block in blocks:
            if "Dividende" in block[0]:
                result = parser._parse_block(block, "CHF")
                assert isinstance(result, SecurityPayment)
                # gross=198.00, net=128.70 → WHT=69.30
                assert result.withholding_tax == Decimal("69.30")
                break

    def test_alibaba_dividend_wht(self, kontoauszug_text, parser):
        """ALIBABA: Taxen=0.00 but gross-net=1.43 (correct WHT)."""
        sections = parser._split_currency_sections(kontoauszug_text)
        usd_section = next(text for ccy, text in sections if ccy == "USD")
        blocks = parser._assemble_blocks(usd_section)

        alibaba_div = None
        for block in blocks:
            if "Dividende" in block[0]:
                result = parser._parse_block(block, "USD")
                if isinstance(result, SecurityPayment) and "ALIBABA" in result.security.name:
                    alibaba_div = result
                    break

        assert alibaba_div is not None
        # WHT = gross - net = 66.00 - 64.57 = 1.43
        assert alibaba_div.withholding_tax == Decimal("1.43")
        assert alibaba_div.gross_amount == Decimal("66.00")
        assert alibaba_div.net_amount == Decimal("64.57")
        assert alibaba_div.quantity == Decimal("66")

    def test_dividend_amount_per_unit(self, kontoauszug_text, parser):
        """Amount per unit should be computed."""
        sections = parser._split_currency_sections(kontoauszug_text)
        chf_section = next(text for ccy, text in sections if ccy == "CHF")
        blocks = parser._assemble_blocks(chf_section)

        for block in blocks:
            if "Dividende" in block[0]:
                result = parser._parse_block(block, "CHF")
                assert isinstance(result, SecurityPayment)
                # 198.00 / 60 = 3.30
                assert result.amount_per_unit == Decimal("198.00") / Decimal("60")
                break


# ── TestBuySellParsing ───────────────────────────────────────────────────────


class TestBuySellParsing:
    """Test buy/sell block parsing."""

    def test_amgen_buy(self, kontoauszug_text, parser):
        sections = parser._split_currency_sections(kontoauszug_text)
        usd_section = next(text for ccy, text in sections if ccy == "USD")
        blocks = parser._assemble_blocks(usd_section)

        buy_tx = None
        for block in blocks:
            result = parser._parse_block(block, "USD")
            if isinstance(result, Transaction) and result.type == TransactionType.BUY:
                buy_tx = result
                break

        assert buy_tx is not None
        assert buy_tx.security is not None
        assert buy_tx.security.isin == "US0311621009"
        assert buy_tx.quantity == Decimal("8")
        assert buy_tx.unit_price == Decimal("262.34")
        assert "AMGEN" in buy_tx.security.name
        # Commission + Taxen
        assert buy_tx.costs == Decimal("30.85") + Decimal("3.15")

    def test_buy_net_amount_negative(self, kontoauszug_text, parser):
        """Buy transactions should have negative net_amount."""
        sections = parser._split_currency_sections(kontoauszug_text)
        usd_section = next(text for ccy, text in sections if ccy == "USD")
        blocks = parser._assemble_blocks(usd_section)

        for block in blocks:
            result = parser._parse_block(block, "USD")
            if isinstance(result, Transaction) and result.type == TransactionType.BUY:
                assert result.net_amount < 0
                # -(2098.77 + 34.00)
                expected = -(Decimal("2098.77") + Decimal("30.85") + Decimal("3.15"))
                assert result.net_amount == expected
                break


# ── TestSimpleEntries ────────────────────────────────────────────────────────


class TestSimpleEntries:
    """Test fees, interest, FX, opening/closing balance."""

    def test_custody_fees_chf(self, kontoauszug_text, parser):
        sections = parser._split_currency_sections(kontoauszug_text)
        chf_section = next(text for ccy, text in sections if ccy == "CHF")
        blocks = parser._assemble_blocks(chf_section)

        fees = []
        for block in blocks:
            result = parser._parse_block(block, "CHF")
            if isinstance(result, Transaction) and result.type == TransactionType.CUSTODY_FEE:
                fees.append(result)

        assert len(fees) == 4
        # Each fee is 27.03 CHF (except possibly different)
        for fee in fees:
            assert fee.net_amount < 0  # Fees are debits
            assert fee.currency == "CHF"

    def test_interest_chf(self, kontoauszug_text, parser):
        sections = parser._split_currency_sections(kontoauszug_text)
        chf_section = next(text for ccy, text in sections if ccy == "CHF")
        blocks = parser._assemble_blocks(chf_section)

        interest = None
        for block in blocks:
            result = parser._parse_block(block, "CHF")
            if isinstance(result, Transaction) and result.type == TransactionType.INTEREST:
                interest = result
                break

        assert interest is not None
        assert interest.net_amount == Decimal("0.03")
        assert interest.currency == "CHF"

    def test_schlussbilanz_chf(self, kontoauszug_text, parser):
        sections = parser._split_currency_sections(kontoauszug_text)
        chf_section = next(text for ccy, text in sections if ccy == "CHF")
        blocks = parser._assemble_blocks(chf_section)

        balance = None
        for block in blocks:
            result = parser._parse_block(block, "CHF")
            if isinstance(result, CashBalance):
                balance = result
                break

        assert balance is not None
        assert balance.balance == Decimal("20.78")
        assert balance.currency == "CHF"

    def test_fx_transaction_eur(self, kontoauszug_text, parser):
        """EUR section FX: EUR sold → FX_DEBIT."""
        sections = parser._split_currency_sections(kontoauszug_text)
        eur_section = next(text for ccy, text in sections if ccy == "EUR")
        blocks = parser._assemble_blocks(eur_section)

        fx_tx = None
        for block in blocks:
            result = parser._parse_block(block, "EUR")
            if isinstance(result, Transaction) and result.type in (
                TransactionType.FX_CREDIT, TransactionType.FX_DEBIT
            ):
                fx_tx = result
                break

        assert fx_tx is not None
        assert fx_tx.type == TransactionType.FX_DEBIT
        assert fx_tx.net_amount < 0

    def test_fx_transaction_usd(self, kontoauszug_text, parser):
        """USD section FX: USD received → FX_CREDIT."""
        sections = parser._split_currency_sections(kontoauszug_text)
        usd_section = next(text for ccy, text in sections if ccy == "USD")
        blocks = parser._assemble_blocks(usd_section)

        fx_tx = None
        for block in blocks:
            result = parser._parse_block(block, "USD")
            if isinstance(result, Transaction) and result.type in (
                TransactionType.FX_CREDIT, TransactionType.FX_DEBIT
            ):
                fx_tx = result
                break

        assert fx_tx is not None
        assert fx_tx.type == TransactionType.FX_CREDIT
        assert fx_tx.net_amount > 0


# ── TestIsinResolution ───────────────────────────────────────────────────────


class TestIsinResolution:
    """Test ISIN resolution from buy/sell registry and portfolio."""

    def test_resolve_from_holdings(self, parser, portfolio_holdings):
        """Resolve dividend ISIN from portfolio holdings by name match."""
        payments = [
            SecurityPayment(
                security=Security(isin="", name="NOVARTIS N (NOVN)", currency="CHF"),
                payment_date=__import__("datetime").date(2024, 3, 11),
                gross_amount=Decimal("198.00"),
                currency="CHF",
                withholding_tax=Decimal("69.30"),
                net_amount=Decimal("128.70"),
                quantity=Decimal("60"),
            ),
        ]
        payments[0]._symbol = "NOVN"  # type: ignore[attr-defined]

        parser._resolve_dividend_isins(payments, {}, portfolio_holdings)
        assert payments[0].security.isin == "CH0012005267"

    def test_resolve_from_symbol_map(self, parser):
        """Resolve dividend ISIN from buy/sell symbol→ISIN map."""
        payments = [
            SecurityPayment(
                security=Security(isin="", name="AMGEN ORD (AMGN)", currency="USD"),
                payment_date=__import__("datetime").date(2024, 3, 10),
                gross_amount=Decimal("19.04"),
                currency="USD",
                withholding_tax=Decimal("2.86"),
                net_amount=Decimal("13.32"),
                quantity=Decimal("8"),
            ),
        ]
        payments[0]._symbol = "AMGN"  # type: ignore[attr-defined]

        symbol_isin_map = {"AMGN": "US0311621009"}
        parser._resolve_dividend_isins(payments, symbol_isin_map, [])
        assert payments[0].security.isin == "US0311621009"


# ── TestFullParse ────────────────────────────────────────────────────────────


class TestFullParse:
    """Integration test: full text → TaxStatement."""

    def test_full_parse_without_portfolio(self, kontoauszug_text, parser, taxpayer):
        """Parse without portfolio — holdings will be empty, ISINs unresolved."""
        statement = parser.parse(
            kontoauszug_text,
            tax_year=2024,
            taxpayer=taxpayer,
            account_number="123456",
        )

        assert statement.tax_year == 2024
        assert statement.account_number == "123456"
        assert statement.institution.name == "Swissquote Bank SA"

        # Cash balances from Schlussbilanz
        assert len(statement.cash_balances) == 3
        ccy_balances = {cb.currency: cb.balance for cb in statement.cash_balances}
        assert ccy_balances["CHF"] == Decimal("20.78")
        assert ccy_balances["USD"] == Decimal("45.84")
        assert ccy_balances["EUR"] == Decimal("308.10")

    def test_dividends_count(self, kontoauszug_text, parser, taxpayer):
        statement = parser.parse(
            kontoauszug_text,
            tax_year=2024,
            taxpayer=taxpayer,
            account_number="123456",
        )
        # CHF: 1 (NOVARTIS), USD: 2 (ALIBABA, PFIZER), EUR: 1 (SAP) = 4
        assert len(statement.security_payments) == 4

    def test_expenses_count(self, kontoauszug_text, parser, taxpayer):
        statement = parser.parse(
            kontoauszug_text,
            tax_year=2024,
            taxpayer=taxpayer,
            account_number="123456",
        )
        # 4 quarterly Depotgebühren
        assert len(statement.expenses) == 4

    def test_interest_annotated(self, kontoauszug_text, parser, taxpayer):
        statement = parser.parse(
            kontoauszug_text,
            tax_year=2024,
            taxpayer=taxpayer,
            account_number="123456",
        )
        chf_balance = next(
            cb for cb in statement.cash_balances if cb.currency == "CHF"
        )
        assert chf_balance.interest_earned == Decimal("0.03")

        usd_balance = next(
            cb for cb in statement.cash_balances if cb.currency == "USD"
        )
        assert usd_balance.interest_earned == Decimal("2.98")

    def test_iban_annotated(self, kontoauszug_text, parser, taxpayer):
        statement = parser.parse(
            kontoauszug_text,
            tax_year=2024,
            taxpayer=taxpayer,
            account_number="123456",
        )
        for cb in statement.cash_balances:
            assert cb.iban is not None
            assert cb.iban.startswith("CH")

    def test_account_number_from_cover(self, kontoauszug_text, parser, taxpayer):
        """Account number auto-detected from cover page when not provided."""
        statement = parser.parse(
            kontoauszug_text,
            tax_year=2024,
            taxpayer=taxpayer,
        )
        assert statement.account_number == "123456"

    def test_buy_transaction_present(self, kontoauszug_text, parser, taxpayer):
        statement = parser.parse(
            kontoauszug_text,
            tax_year=2024,
            taxpayer=taxpayer,
            account_number="123456",
        )
        buys = [
            t for t in statement.transactions if t.type == TransactionType.BUY
        ]
        assert len(buys) == 1
        assert buys[0].security.isin == "US0311621009"

    def test_empty_input_raises(self, parser, taxpayer):
        with pytest.raises(ValueError, match="empty"):
            parser.parse("", tax_year=2024, taxpayer=taxpayer)

    def test_broker_properties(self, parser):
        assert parser.name == "Swissquote"
        assert parser.institution.uid == "CHE-106.787.734"
        assert parser.institution.clearing_number == "08750"


# ── English fixture ─────────────────────────────────────────────────────────


@pytest.fixture
def kontoauszug_text_en() -> str:
    path = os.path.join(FIXTURE_DIR, "swissquote_kontoauszug_en.txt")
    with open(path) as f:
        return f.read()


# ── TestDetectKontoauszugLocale ──────────────────────────────────────────────


class TestDetectKontoauszugLocale:
    def test_german(self, kontoauszug_text):
        assert _detect_kontoauszug_locale(kontoauszug_text) == "de"

    def test_english(self, kontoauszug_text_en):
        assert _detect_kontoauszug_locale(kontoauszug_text_en) == "en"

    def test_unsupported_raises(self):
        with pytest.raises(ValueError, match="Unsupported"):
            _detect_kontoauszug_locale("Du 01.01.2024 au 31.12.2024")

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="Unsupported"):
            _detect_kontoauszug_locale("")


# ── TestCoverPageParsingEnglish ──────────────────────────────────────────────


class TestCoverPageParsingEnglish:
    def test_account_number(self, kontoauszug_text_en, parser):
        info = parser._parse_cover_page(kontoauszug_text_en)
        assert info["account_number"] == "999999"

    def test_period(self, kontoauszug_text_en, parser):
        info = parser._parse_cover_page(kontoauszug_text_en)
        assert info["period_from"] == "01.01.2025"
        assert info["period_to"] == "31.12.2025"

    def test_client_name(self, kontoauszug_text_en, parser):
        info = parser._parse_cover_page(kontoauszug_text_en)
        assert info["first_name"] == "Jane"
        assert info["last_name"] == "Doe"


# ── TestExtractClientInfoEnglish ─────────────────────────────────────────────


class TestExtractClientInfoEnglish:
    def test_returns_dict(self, kontoauszug_text_en):
        info = SwissquoteKontoauszugParser.extract_client_info(kontoauszug_text_en)
        assert isinstance(info, dict)
        assert "account_number" in info

    def test_extract_year(self, kontoauszug_text_en):
        year = SwissquoteKontoauszugParser.extract_year(kontoauszug_text_en)
        assert year == 2025


# ── TestCurrencySectionsEnglish ──────────────────────────────────────────────


class TestCurrencySectionsEnglish:
    def test_sections_detected(self, kontoauszug_text_en, parser):
        sections = parser._split_currency_sections(kontoauszug_text_en)
        currencies = [ccy for ccy, _ in sections]
        assert len(currencies) == 3  # CHF, USD, EUR
        for ccy in currencies:
            assert len(ccy) == 3 and ccy.isalpha()


# ── TestDividendParsingEnglish ───────────────────────────────────────────────


class TestDividendParsingEnglish:
    def test_dividend_parsed(self, kontoauszug_text_en, parser, taxpayer):
        """At least one dividend should be parsed from the English fixture."""
        statement = parser.parse(
            kontoauszug_text_en,
            tax_year=2025,
            taxpayer=taxpayer,
            account_number="999999",
        )
        assert len(statement.security_payments) > 0
        for sp in statement.security_payments:
            assert sp.gross_amount > 0
            assert sp.currency in ("CHF", "USD", "EUR")


# ── TestTradeParsingEnglish ──────────────────────────────────────────────────


class TestTradeParsingEnglish:
    def test_sell_parsed(self, kontoauszug_text_en, parser, taxpayer):
        statement = parser.parse(
            kontoauszug_text_en,
            tax_year=2025,
            taxpayer=taxpayer,
            account_number="999999",
        )
        sells = [t for t in statement.transactions if t.type == TransactionType.SELL]
        assert len(sells) > 0
        for sell in sells:
            assert sell.security is not None
            assert sell.security.isin != ""


# ── TestFullParseEnglish ─────────────────────────────────────────────────────


class TestFullParseEnglish:
    def test_full_parse(self, kontoauszug_text_en, parser, taxpayer):
        statement = parser.parse(
            kontoauszug_text_en,
            tax_year=2025,
            taxpayer=taxpayer,
            account_number="999999",
        )
        assert statement.tax_year == 2025
        assert len(statement.cash_balances) >= 2
        assert len(statement.security_payments) >= 1

    def test_dividends_count(self, kontoauszug_text_en, parser, taxpayer):
        statement = parser.parse(
            kontoauszug_text_en,
            tax_year=2025,
            taxpayer=taxpayer,
            account_number="999999",
        )
        # EN fixture has: 2x Novo Nordisk (CHF), 3x Eli Lilly (USD), 1x BMW (EUR), 1x VW (EUR) = 7
        assert len(statement.security_payments) == 7

    def test_cash_balances(self, kontoauszug_text_en, parser, taxpayer):
        statement = parser.parse(
            kontoauszug_text_en,
            tax_year=2025,
            taxpayer=taxpayer,
            account_number="999999",
        )
        ccy_balances = {cb.currency: cb.balance for cb in statement.cash_balances}
        assert ccy_balances["CHF"] == Decimal("0.00")
        assert ccy_balances["USD"] == Decimal("18602.99")
        assert ccy_balances["EUR"] == Decimal("395.38")

    def test_expenses_count(self, kontoauszug_text_en, parser, taxpayer):
        statement = parser.parse(
            kontoauszug_text_en,
            tax_year=2025,
            taxpayer=taxpayer,
            account_number="999999",
        )
        # 4 quarterly Safecustody Fees
        assert len(statement.expenses) == 4

    def test_sell_transactions(self, kontoauszug_text_en, parser, taxpayer):
        statement = parser.parse(
            kontoauszug_text_en,
            tax_year=2025,
            taxpayer=taxpayer,
            account_number="999999",
        )
        sells = [t for t in statement.transactions if t.type == TransactionType.SELL]
        # Boeing x2 + Eli Lilly x1 = 3 sells
        assert len(sells) == 3
