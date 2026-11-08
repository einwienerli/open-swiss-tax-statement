"""Tests for the Swissquote CSV parser (15-column and 13-column formats)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.models.enums import TransactionType
from src.models.taxpayer import TaxPayer
from src.parsers.base import BrokerParser
from src.parsers.swissquote.constants import (
    ENGLISH_TRANSACTION_TYPES,
    GERMAN_TRANSACTION_TYPES,
)
from src.parsers.swissquote.csv_parser import (
    SwissquoteCsvParser,
    _classify_transaction,
    _detect_locale,
    _parse_date,
    _parse_number,
)

# ── Helper parsing tests ─────────────────────────────────────────────────────


class TestParseDate:
    def test_real_format_with_time(self):
        assert _parse_date("02-01-2025 09:00:42") == date(2025, 1, 2)

    def test_dash_format_without_time(self):
        assert _parse_date("02-01-2025") == date(2025, 1, 2)

    def test_legacy_dot_format(self):
        assert _parse_date("15.03.2024") == date(2024, 3, 15)

    def test_iso_format(self):
        assert _parse_date("2024-03-15") == date(2024, 3, 15)

    def test_whitespace(self):
        assert _parse_date("  02-01-2025 09:00:42  ") == date(2025, 1, 2)

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Unable to parse date"):
            _parse_date("not-a-date")


class TestParseNumber:
    def test_simple(self):
        assert _parse_number("1234.56") == Decimal("1234.56")

    def test_swiss_apostrophe(self):
        assert _parse_number("9'559.85") == Decimal("9559.85")

    def test_large_swiss_number(self):
        assert _parse_number("1'234'567.89") == Decimal("1234567.89")

    def test_negative(self):
        assert _parse_number("-9'559.85") == Decimal("-9559.85")

    def test_dash_returns_zero(self):
        assert _parse_number("-") == Decimal("0")

    def test_empty_returns_zero(self):
        assert _parse_number("") == Decimal("0")
        assert _parse_number(None) == Decimal("0")
        assert _parse_number("   ") == Decimal("0")

    def test_zero(self):
        assert _parse_number("0") == Decimal("0")
        assert _parse_number("0.00") == Decimal("0.00")

    def test_invalid_returns_zero(self):
        assert _parse_number("abc") == Decimal("0")


class TestClassifyTransaction:
    def test_german_buy(self):
        assert _classify_transaction("Kauf", GERMAN_TRANSACTION_TYPES) == TransactionType.BUY

    def test_german_sell(self):
        assert _classify_transaction("Verkauf", GERMAN_TRANSACTION_TYPES) == TransactionType.SELL

    def test_german_dividend(self):
        assert _classify_transaction("Dividende", GERMAN_TRANSACTION_TYPES) == TransactionType.DIVIDEND

    def test_german_interest_on_deposits(self):
        assert _classify_transaction("Zinsen auf Einlagen", GERMAN_TRANSACTION_TYPES) == TransactionType.INTEREST

    def test_german_custody_fee(self):
        assert _classify_transaction("Depotgebühren", GERMAN_TRANSACTION_TYPES) == TransactionType.CUSTODY_FEE

    def test_german_storno_maps_to_other(self):
        assert _classify_transaction("Storno (Verkauf)", GERMAN_TRANSACTION_TYPES) == TransactionType.OTHER

    def test_german_reversed_maps_to_other(self):
        assert _classify_transaction("Verkauf (Storniert)", GERMAN_TRANSACTION_TYPES) == TransactionType.OTHER

    def test_german_rights_issuance_maps_to_other(self):
        assert _classify_transaction("Ausgabe von Anrechten", GERMAN_TRANSACTION_TYPES) == TransactionType.OTHER

    def test_english_buy(self):
        assert _classify_transaction("Buy", ENGLISH_TRANSACTION_TYPES) == TransactionType.BUY

    def test_english_dividend(self):
        assert _classify_transaction("Dividend", ENGLISH_TRANSACTION_TYPES) == TransactionType.DIVIDEND

    def test_unknown_maps_to_other(self):
        assert _classify_transaction("SomethingWeird", GERMAN_TRANSACTION_TYPES) == TransactionType.OTHER

    def test_empty_maps_to_other(self):
        assert _classify_transaction("", GERMAN_TRANSACTION_TYPES) == TransactionType.OTHER


class TestDetectLocale:
    def test_german_15col(self):
        header = "Datum;Auftrag #;Transaktionen;Name;ISIN;Symbol;Anzahl;Stückpreis;Währung Stückpreis;Kosten;Aufgelaufene Zinsen;Nettobetrag;Währung Nettobetrag;Saldo;Währung Saldo"
        result = _detect_locale(header)
        assert result == GERMAN_TRANSACTION_TYPES

    def test_english_15col(self):
        header = "Date;Order #;Transactions;Name;ISIN;Symbol;Quantity;Unit Price;Unit Price Currency;Costs;Accrued Interest;Net Amount;Net Amount Currency;Balance;Balance Currency"
        result = _detect_locale(header)
        assert result == ENGLISH_TRANSACTION_TYPES

    def test_bom_handling(self):
        header = "\ufeffDatum;Auftrag #;Transaktionen;Name;ISIN;Symbol;Anzahl;Stückpreis;Währung Stückpreis;Kosten;Aufgelaufene Zinsen;Nettobetrag;Währung Nettobetrag;Saldo;Währung Saldo"
        result = _detect_locale(header)
        assert result == GERMAN_TRANSACTION_TYPES

    def test_fuzzy_german(self):
        header = " Datum ; Auftrag # ; Transaktionen ; Name ; ISIN ; Symbol ; Anzahl ; Stückpreis ; Währung Stückpreis ; Kosten ; Aufgelaufene Zinsen ; Nettobetrag ; Währung Nettobetrag ; Saldo ; Währung Saldo "
        result = _detect_locale(header)
        assert result == GERMAN_TRANSACTION_TYPES

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unable to detect"):
            _detect_locale("Col1;Col2;Col3")


# ── Full parser integration tests ────────────────────────────────────────────


class TestSwissquoteCsvParserInterface:
    def test_is_broker_parser(self):
        parser = SwissquoteCsvParser()
        assert isinstance(parser, BrokerParser)

    def test_name(self):
        parser = SwissquoteCsvParser()
        assert parser.name == "Swissquote"

    def test_institution(self):
        parser = SwissquoteCsvParser()
        inst = parser.institution
        assert inst.name == "Swissquote Bank SA"
        assert inst.uid == "CHE-106.787.734"
        assert inst.clearing_number == "08750"


class TestSwissquoteCsvParser15Col:
    """Integration tests using the 15-column German fixture."""

    @pytest.fixture
    def statement(self, sample_csv: str, taxpayer: TaxPayer):
        parser = SwissquoteCsvParser()
        return parser.parse(sample_csv, tax_year=2025, taxpayer=taxpayer)

    def test_basic_metadata(self, statement):
        assert statement.tax_year == 2025
        assert statement.period_from == date(2025, 1, 1)
        assert statement.period_to == date(2025, 12, 31)
        assert statement.taxpayer.first_name == "Max"
        assert statement.institution.uid == "CHE-106.787.734"

    def test_holdings_count(self, statement):
        # NESTLE: 100 bought - 30 sold = 70
        # APPLE: 50 bought = 50
        # ROCHE: 25 bought = 25
        # VANGUARD: 200 bought = 200
        # SOME STOCK: removed by storno (100 sold + 100 storno cancel)
        # ZURICH INSURANCE: no buy/sell, only dividend → 0 holdings
        assert len(statement.holdings) == 4

    def test_nestle_holding(self, statement):
        nestle = next(
            h for h in statement.holdings if h.security.isin == "CH0038863350"
        )
        assert nestle.quantity == Decimal("70")
        assert nestle.security.name == "NESTLE SA"

    def test_apple_holding(self, statement):
        apple = next(
            h for h in statement.holdings if h.security.isin == "US0378331005"
        )
        assert apple.quantity == Decimal("50")

    def test_roche_holding(self, statement):
        roche = next(
            h for h in statement.holdings if h.security.isin == "CH0012032048"
        )
        assert roche.quantity == Decimal("25")

    def test_vanguard_holding(self, statement):
        vanguard = next(
            h for h in statement.holdings if h.security.isin == "IE00B3XXRP09"
        )
        assert vanguard.quantity == Decimal("200")

    def test_storno_removes_all_order_transactions(self, statement):
        """Storno row with ORD006 should remove both the sell and the storno."""
        some_stock = [
            h for h in statement.holdings if h.security.isin == "CH0000000099"
        ]
        assert len(some_stock) == 0

        # Also verify no transactions for that order number
        ord006_txs = [t for t in statement.transactions if t.order_number == "ORD006"]
        assert len(ord006_txs) == 0

    def test_security_payments_count(self, statement):
        # NESTLE dividend + 2 APPLE dividends + ZURICH INSURANCE dividend = 4
        assert len(statement.security_payments) == 4

    def test_nestle_dividend_wht_from_kosten(self, statement):
        nestle_divs = [
            p for p in statement.security_payments
            if p.security.isin == "CH0038863350"
        ]
        assert len(nestle_divs) == 1
        div = nestle_divs[0]
        assert div.payment_date == date(2025, 3, 3)
        assert div.net_amount == Decimal("300.00")
        assert div.withholding_tax == Decimal("105.00")  # abs(Kosten)
        assert div.gross_amount == Decimal("405.00")  # 300 + 105
        assert div.currency == "CHF"

    def test_apple_dividend_wht_from_kosten(self, statement):
        apple_divs = [
            p for p in statement.security_payments
            if p.security.isin == "US0378331005"
        ]
        assert len(apple_divs) == 2
        may_div = next(d for d in apple_divs if d.payment_date == date(2025, 5, 10))
        assert may_div.net_amount == Decimal("19.50")
        assert may_div.withholding_tax == Decimal("5.85")
        assert may_div.gross_amount == Decimal("25.35")

    def test_zurich_insurance_dividend(self, statement):
        zurich_divs = [
            p for p in statement.security_payments
            if p.security.isin == "CH0011075394"
        ]
        assert len(zurich_divs) == 1
        div = zurich_divs[0]
        assert div.net_amount == Decimal("123.50")
        assert div.withholding_tax == Decimal("66.50")
        assert div.gross_amount == Decimal("190.00")
        assert div.currency == "EUR"

    def test_cash_balances(self, statement):
        currencies = {cb.currency for cb in statement.cash_balances}
        assert "CHF" in currencies
        assert "USD" in currencies
        assert "EUR" in currencies

    def test_no_interest_without_portfolio(self, statement):
        """Without portfolio, CSV INTEREST rows are NOT used (wrong year)."""
        chf_balance = next(
            cb for cb in statement.cash_balances if cb.currency == "CHF"
        )
        assert chf_balance.interest_earned == Decimal("0")

        usd_balance = next(
            cb for cb in statement.cash_balances if cb.currency == "USD"
        )
        assert usd_balance.interest_earned == Decimal("0")

    def test_expenses(self, statement):
        assert len(statement.expenses) == 1
        assert statement.expenses[0].type == TransactionType.CUSTODY_FEE
        assert statement.expenses[0].net_amount == Decimal("-45.00")

    def test_rights_issuance_filtered_as_other(self, statement):
        """Ausgabe von Anrechten should be parsed as OTHER and included."""
        other_txs = [t for t in statement.transactions if t.type == TransactionType.OTHER]
        assert len(other_txs) == 1  # Just the rights issuance

    def test_date_with_time_parsed(self, statement):
        """Verify the DD-MM-YYYY HH:MM:SS format is correctly parsed."""
        buy_txs = [t for t in statement.transactions if t.type == TransactionType.BUY]
        nestle_buy = next(t for t in buy_txs if t.security and t.security.isin == "CH0038863350")
        assert nestle_buy.date == date(2025, 1, 2)

    def test_dash_nettobetrag_treated_as_zero(self, statement):
        """The rights issuance row has "-" in Nettobetrag → should be 0."""
        other_txs = [t for t in statement.transactions if t.type == TransactionType.OTHER]
        rights_tx = other_txs[0]
        assert rights_tx.net_amount == Decimal("0")

    def test_dividend_amount_per_unit(self, statement):
        """amount_per_unit should be gross_amount / quantity."""
        nestle_div = next(
            p for p in statement.security_payments if p.security.isin == "CH0038863350"
        )
        expected = nestle_div.gross_amount / nestle_div.quantity
        assert nestle_div.amount_per_unit == expected

    def test_dividend_quantity_reconstructed_from_holdings(self, statement):
        """Dividend quantity should be reconstructed from year-end holdings.

        NESTLE: 100 bought (Jan), 30 sold (Jun), dividend (Mar) → qty at div = 100
        (year-end=70, sells_after_div=30 → 70+30=100).
        """
        nestle_div = next(
            p for p in statement.security_payments if p.security.isin == "CH0038863350"
        )
        assert nestle_div.quantity == Decimal("100")

    def test_dividend_quantity_no_trades_after(self, statement):
        """When no trades happen after dividend, quantity = year-end holding.

        APPLE: 50 bought (Jan), dividend (May, Nov) → qty at div = 50.
        """
        apple_divs = [
            p for p in statement.security_payments if p.security.isin == "US0378331005"
        ]
        for div in apple_divs:
            assert div.quantity == Decimal("50")

    def test_iban_set_with_account_number(self, sample_csv, taxpayer):
        parser = SwissquoteCsvParser()
        stmt = parser.parse(sample_csv, 2025, taxpayer, account_number="123456")
        for cb in stmt.cash_balances:
            assert cb.iban is not None
            assert cb.iban.startswith("CH")
            assert len(cb.iban) == 21

    def test_iban_not_set_without_account_number(self, statement):
        """Without account_number, IBAN should not be set."""
        for cb in statement.cash_balances:
            assert cb.iban is None


class TestSwissquoteCsvParserWithPortfolio:
    """Integration tests using CSV + portfolio PDF text."""

    @pytest.fixture
    def statement(self, sample_csv: str, sample_portfolio_text: str, taxpayer: TaxPayer):
        parser = SwissquoteCsvParser()
        return parser.parse(
            sample_csv, tax_year=2025, taxpayer=taxpayer,
            portfolio_content=sample_portfolio_text,
        )

    def test_holdings_from_portfolio(self, statement):
        # Portfolio has 5 securities: NESTLE, ROCHE, APPLE, ZURICH, VANGUARD
        assert len(statement.holdings) == 5

    def test_portfolio_nestle_has_tax_value(self, statement):
        nestle = next(
            h for h in statement.holdings if h.security.isin == "CH0038863350"
        )
        assert nestle.quantity == Decimal("70")
        assert nestle.tax_value_chf == Decimal("7000.00")

    def test_portfolio_apple_has_tax_value(self, statement):
        apple = next(
            h for h in statement.holdings if h.security.isin == "US0378331005"
        )
        assert apple.quantity == Decimal("50")
        assert apple.tax_value_chf == Decimal("8325.00")

    def test_cash_from_portfolio(self, statement):
        currencies = {cb.currency for cb in statement.cash_balances}
        assert "CHF" in currencies
        assert "USD" in currencies
        assert "EUR" in currencies

    def test_chf_cash_balance(self, statement):
        chf = next(cb for cb in statement.cash_balances if cb.currency == "CHF")
        assert chf.balance == Decimal("37383.95")
        assert chf.tax_value_chf == Decimal("37383.95")

    def test_interest_from_portfolio_minus_saldo(self, statement):
        """With portfolio, interest = portfolio_balance - last_csv_saldo."""
        # CHF: portfolio=37'383.95, last saldo=42'383.95 (Oct FX credit) → negative → 0
        chf = next(cb for cb in statement.cash_balances if cb.currency == "CHF")
        assert chf.interest_earned == Decimal("0")

        # USD: portfolio=909.95, last saldo=909.95 (Nov dividend) → 0
        usd = next(cb for cb in statement.cash_balances if cb.currency == "USD")
        assert usd.interest_earned == Decimal("0")

    def test_dividends_still_from_csv(self, statement):
        """Security payments come from CSV regardless of portfolio source."""
        assert len(statement.security_payments) == 4

    def test_dividend_quantity_from_portfolio_holdings(self, statement):
        """Dividend quantity reconstructed using portfolio-sourced holdings.

        NESTLE: portfolio says 70 shares, sold 30 after Mar div → qty at div = 100.
        """
        nestle_div = next(
            p for p in statement.security_payments if p.security.isin == "CH0038863350"
        )
        assert nestle_div.quantity == Decimal("100")
        assert nestle_div.amount_per_unit == nestle_div.gross_amount / Decimal("100")


# ── Edge case tests ──────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_csv_raises(self, taxpayer):
        parser = SwissquoteCsvParser()
        with pytest.raises(ValueError, match="CSV file is empty"):
            parser.parse("", tax_year=2025, taxpayer=taxpayer)

    def test_header_only_csv(self, taxpayer):
        csv_content = "Datum;Auftrag #;Transaktionen;Name;ISIN;Symbol;Anzahl;Stückpreis;Währung Stückpreis;Kosten;Aufgelaufene Zinsen;Nettobetrag;Währung Nettobetrag;Saldo;Währung Saldo\n"
        parser = SwissquoteCsvParser()
        stmt = parser.parse(csv_content, tax_year=2025, taxpayer=taxpayer)
        assert stmt.holdings == []
        assert stmt.transactions == []

    def test_different_tax_year_filters(self, taxpayer):
        csv_content = (
            "Datum;Auftrag #;Transaktionen;Name;ISIN;Symbol;Anzahl;Stückpreis;Währung Stückpreis;Kosten;Aufgelaufene Zinsen;Nettobetrag;Währung Nettobetrag;Saldo;Währung Saldo\n"
            "15-01-2024 09:00:00;ORD001;Kauf;TEST AG;CH0000000001;TEST;10;100.00;CHF;5.00;;-1'005.00;CHF;-1'005.00;CHF\n"
            "15-01-2025 09:00:00;ORD002;Kauf;TEST AG;CH0000000001;TEST;20;100.00;CHF;5.00;;-2'005.00;CHF;-3'010.00;CHF\n"
        )
        parser = SwissquoteCsvParser()
        stmt_2025 = parser.parse(csv_content, tax_year=2025, taxpayer=taxpayer)
        assert len(stmt_2025.transactions) == 1
        assert stmt_2025.holdings[0].quantity == Decimal("20")

        stmt_2024 = parser.parse(csv_content, tax_year=2024, taxpayer=taxpayer)
        assert len(stmt_2024.transactions) == 1
        assert stmt_2024.holdings[0].quantity == Decimal("10")

    def test_fully_sold_security_not_in_holdings(self, taxpayer):
        csv_content = (
            "Datum;Auftrag #;Transaktionen;Name;ISIN;Symbol;Anzahl;Stückpreis;Währung Stückpreis;Kosten;Aufgelaufene Zinsen;Nettobetrag;Währung Nettobetrag;Saldo;Währung Saldo\n"
            "15-01-2025 09:00:00;ORD001;Kauf;TEST AG;CH0000000001;TEST;10;100.00;CHF;5.00;;-1'005.00;CHF;-1'005.00;CHF\n"
            "15-06-2025 09:00:00;ORD002;Verkauf;TEST AG;CH0000000001;TEST;10;110.00;CHF;5.00;;1'095.00;CHF;90.00;CHF\n"
        )
        parser = SwissquoteCsvParser()
        stmt = parser.parse(csv_content, tax_year=2025, taxpayer=taxpayer)
        assert len(stmt.holdings) == 0

    def test_dividend_without_wht(self, taxpayer):
        csv_content = (
            "Datum;Auftrag #;Transaktionen;Name;ISIN;Symbol;Anzahl;Stückpreis;Währung Stückpreis;Kosten;Aufgelaufene Zinsen;Nettobetrag;Währung Nettobetrag;Saldo;Währung Saldo\n"
            "15-03-2025 10:00:00;;Dividende;TEST AG;CH0000000001;TEST;10;;;;;50.00;CHF;50.00;CHF\n"
        )
        parser = SwissquoteCsvParser()
        stmt = parser.parse(csv_content, tax_year=2025, taxpayer=taxpayer)
        assert len(stmt.security_payments) == 1
        assert stmt.security_payments[0].withholding_tax == Decimal("0")
        assert stmt.security_payments[0].gross_amount == Decimal("50.00")

    def test_multiple_currencies_cash_balances(self, taxpayer):
        csv_content = (
            "Datum;Auftrag #;Transaktionen;Name;ISIN;Symbol;Anzahl;Stückpreis;Währung Stückpreis;Kosten;Aufgelaufene Zinsen;Nettobetrag;Währung Nettobetrag;Saldo;Währung Saldo\n"
            "15-01-2025 09:00:00;ORD001;Kauf;TEST AG;CH0000000001;TEST;10;100.00;CHF;5.00;;-1'005.00;CHF;-1'005.00;CHF\n"
            "20-01-2025 09:00:00;ORD002;Kauf;US TEST;US0000000001;USTE;5;200.00;USD;10.00;;-1'010.00;USD;-1'010.00;USD\n"
            "01-02-2025 09:00:00;;Devisengutschrift;;;;;;;;;5'000.00;CHF;3'995.00;CHF\n"
            "01-02-2025 09:00:01;;Devisenbelastung;;;;;;;;;-4'500.00;EUR;-4'500.00;EUR\n"
        )
        parser = SwissquoteCsvParser()
        stmt = parser.parse(csv_content, tax_year=2025, taxpayer=taxpayer)
        currencies = {cb.currency for cb in stmt.cash_balances}
        assert "CHF" in currencies
        assert "USD" in currencies
        assert "EUR" in currencies


# ── 13-column CSV variant tests ─────────────────────────────────────────────


class TestSwissquoteCsvParser13Col:
    """Integration tests using the 13-column German fixture (2024 format)."""

    @pytest.fixture
    def statement(self, sample_csv_13col: str, taxpayer: TaxPayer):
        parser = SwissquoteCsvParser()
        return parser.parse(sample_csv_13col, tax_year=2024, taxpayer=taxpayer)

    def test_parses_successfully(self, statement):
        assert statement.tax_year == 2024

    def test_transaction_count(self, statement):
        # 2 buys + 2 dividends + 1 interest + 1 custody fee + 2 FX + 1 sell = 9
        assert len(statement.transactions) == 9

    def test_holdings_count(self, statement):
        # NESTLE: 50 bought - 20 sold = 30
        # APPLE: 30 bought = 30
        assert len(statement.holdings) == 2

    def test_nestle_holding(self, statement):
        nestle = next(
            h for h in statement.holdings if h.security.isin == "CH0038863350"
        )
        assert nestle.quantity == Decimal("30")

    def test_apple_holding(self, statement):
        apple = next(
            h for h in statement.holdings if h.security.isin == "US0378331005"
        )
        assert apple.quantity == Decimal("30")

    def test_currencies_correct(self, statement):
        """Verify the single Währung column is correctly used as currency."""
        buy_txs = [t for t in statement.transactions if t.type == TransactionType.BUY]
        chf_buy = next(t for t in buy_txs if t.security.isin == "CH0038863350")
        usd_buy = next(t for t in buy_txs if t.security.isin == "US0378331005")
        assert chf_buy.currency == "CHF"
        assert usd_buy.currency == "USD"

    def test_nestle_dividend_wht(self, statement):
        nestle_divs = [
            p for p in statement.security_payments
            if p.security.isin == "CH0038863350"
        ]
        assert len(nestle_divs) == 1
        div = nestle_divs[0]
        assert div.net_amount == Decimal("150.00")
        assert div.withholding_tax == Decimal("52.50")
        assert div.gross_amount == Decimal("202.50")

    def test_apple_dividend_wht(self, statement):
        apple_divs = [
            p for p in statement.security_payments
            if p.security.isin == "US0378331005"
        ]
        assert len(apple_divs) == 1
        div = apple_divs[0]
        assert div.net_amount == Decimal("11.70")
        assert div.withholding_tax == Decimal("3.51")
        assert div.gross_amount == Decimal("15.21")

    def test_fx_credit_comp(self, statement):
        fx_credits = [
            t for t in statement.transactions if t.type == TransactionType.FX_CREDIT
        ]
        assert len(fx_credits) == 1
        assert fx_credits[0].net_amount == Decimal("3000.00")
        assert fx_credits[0].currency == "CHF"

    def test_fx_debit_comp(self, statement):
        fx_debits = [
            t for t in statement.transactions if t.type == TransactionType.FX_DEBIT
        ]
        assert len(fx_debits) == 1
        assert fx_debits[0].net_amount == Decimal("-2700.00")
        assert fx_debits[0].currency == "EUR"

    def test_interest(self, statement):
        interest_txs = [
            t for t in statement.transactions if t.type == TransactionType.INTEREST
        ]
        assert len(interest_txs) == 1
        assert interest_txs[0].net_amount == Decimal("8.25")

    def test_custody_fee(self, statement):
        assert len(statement.expenses) == 1
        assert statement.expenses[0].net_amount == Decimal("-25.00")

    def test_cash_balances(self, statement):
        currencies = {cb.currency for cb in statement.cash_balances}
        assert "CHF" in currencies
        assert "USD" in currencies
        assert "EUR" in currencies


class TestDetectLocale13Col:
    def test_german_13col(self):
        header = "Datum;Auftrag #;Transaktionen;Name;ISIN;Symbol;Anzahl;Stückpreis;Kosten;Aufgelaufene Zinsen;Nettobetrag;Saldo;Währung"
        result = _detect_locale(header)
        assert result == GERMAN_TRANSACTION_TYPES

    def test_german_13col_builds_column_map(self):
        from src.parsers.swissquote.csv_parser import _build_column_map
        header = "Datum;Auftrag #;Transaktionen;Name;ISIN;Symbol;Anzahl;Stückpreis;Kosten;Aufgelaufene Zinsen;Nettobetrag;Saldo;Währung"
        col_map = _build_column_map(header)
        assert col_map["currency"] == 12  # Währung column
        assert "unit_price_currency" not in col_map  # Not present in 13-col


# ── Interest accrual tests ───────────────────────────────────────────────────


class TestExtractLastSaldo:
    """Tests for _extract_last_saldo() static method."""

    def test_extracts_last_saldo_per_currency(self, taxpayer):
        """Last saldo per currency by chronological date."""
        from src.parsers.swissquote.csv_parser import _build_column_map

        csv_content = (
            "Datum;Auftrag #;Transaktionen;Name;ISIN;Symbol;Anzahl;Stückpreis;"
            "Währung Stückpreis;Kosten;Aufgelaufene Zinsen;Nettobetrag;"
            "Währung Nettobetrag;Saldo;Währung Saldo\n"
            "02-01-2025 09:00:00;ORD001;Kauf;TEST;CH0000000001;TEST;10;100.00;CHF;5.00;;-1'005.00;CHF;8'995.00;CHF\n"
            "15-06-2025 09:00:00;;Zinsen auf Einlagen;;;;;;;;;12.50;CHF;9'007.50;CHF\n"
            "15-03-2025 09:00:00;;Dividende;TEST;US0000000001;TEST;50;;;-5.00;;20.00;USD;520.00;USD\n"
        )
        lines = csv_content.splitlines()
        col_map = _build_column_map(lines[0])
        result = SwissquoteCsvParser._extract_last_saldo(lines, col_map, 2025)

        assert result["CHF"] == Decimal("9007.50")
        assert result["USD"] == Decimal("520.00")

    def test_ignores_other_years(self, taxpayer):
        from src.parsers.swissquote.csv_parser import _build_column_map

        csv_content = (
            "Datum;Auftrag #;Transaktionen;Name;ISIN;Symbol;Anzahl;Stückpreis;"
            "Währung Stückpreis;Kosten;Aufgelaufene Zinsen;Nettobetrag;"
            "Währung Nettobetrag;Saldo;Währung Saldo\n"
            "02-01-2024 09:00:00;ORD001;Kauf;TEST;CH0000000001;TEST;10;100.00;CHF;5.00;;-1'005.00;CHF;8'995.00;CHF\n"
            "02-01-2025 09:00:00;ORD002;Kauf;TEST;CH0000000001;TEST;10;100.00;CHF;5.00;;-1'005.00;CHF;7'990.00;CHF\n"
        )
        lines = csv_content.splitlines()
        col_map = _build_column_map(lines[0])
        result = SwissquoteCsvParser._extract_last_saldo(lines, col_map, 2025)

        assert result["CHF"] == Decimal("7990.00")

    def test_picks_latest_date_not_file_order(self, taxpayer):
        """When rows are not in chronological order, picks by latest date."""
        from src.parsers.swissquote.csv_parser import _build_column_map

        csv_content = (
            "Datum;Auftrag #;Transaktionen;Name;ISIN;Symbol;Anzahl;Stückpreis;"
            "Währung Stückpreis;Kosten;Aufgelaufene Zinsen;Nettobetrag;"
            "Währung Nettobetrag;Saldo;Währung Saldo\n"
            # Later date first in file
            "15-06-2025 09:00:00;;Zinsen auf Einlagen;;;;;;;;;12.50;CHF;9'012.50;CHF\n"
            # Earlier date second in file
            "02-01-2025 09:00:00;ORD001;Kauf;TEST;CH0000000001;TEST;10;100.00;CHF;5.00;;-1'005.00;CHF;9'000.00;CHF\n"
        )
        lines = csv_content.splitlines()
        col_map = _build_column_map(lines[0])
        result = SwissquoteCsvParser._extract_last_saldo(lines, col_map, 2025)

        # Should pick the Jun row (later date), not the Jan row
        assert result["CHF"] == Decimal("9012.50")


class TestInterestAccrualFromPortfolio:
    """Tests for portfolio-based interest inference."""

    def test_positive_interest(self, taxpayer):
        """interest = portfolio_balance - last_csv_saldo when positive."""
        from src.models.cash import CashBalance

        cash_balances = [
            CashBalance(currency="CHF", balance=Decimal("10020.78")),
            CashBalance(currency="USD", balance=Decimal("545.84")),
        ]
        last_saldo = {
            "CHF": Decimal("10020.75"),
            "USD": Decimal("542.86"),
        }
        SwissquoteCsvParser._annotate_interest_from_portfolio(cash_balances, last_saldo)

        chf = next(cb for cb in cash_balances if cb.currency == "CHF")
        assert chf.interest_earned == Decimal("0.03")

        usd = next(cb for cb in cash_balances if cb.currency == "USD")
        assert usd.interest_earned == Decimal("2.98")

    def test_negative_diff_yields_zero(self, taxpayer):
        """If portfolio < saldo (e.g. due to fees), interest = 0."""
        from src.models.cash import CashBalance

        cash_balances = [
            CashBalance(currency="CHF", balance=Decimal("9000.00")),
        ]
        last_saldo = {"CHF": Decimal("10000.00")}
        SwissquoteCsvParser._annotate_interest_from_portfolio(cash_balances, last_saldo)

        chf = next(cb for cb in cash_balances if cb.currency == "CHF")
        assert chf.interest_earned == Decimal("0")

    def test_currency_not_in_saldo(self, taxpayer):
        """Currency in portfolio but not in CSV → interest not set."""
        from src.models.cash import CashBalance

        cash_balances = [
            CashBalance(currency="GBP", balance=Decimal("500.00")),
        ]
        last_saldo = {}  # No GBP in CSV
        SwissquoteCsvParser._annotate_interest_from_portfolio(cash_balances, last_saldo)

        gbp = next(cb for cb in cash_balances if cb.currency == "GBP")
        assert gbp.interest_earned == Decimal("0")  # Default


class TestDividendQuantityReconstruction:
    HEADER = "Datum;Auftrag #;Transaktionen;Name;ISIN;Symbol;Anzahl;Stückpreis;Währung Stückpreis;Kosten;Aufgelaufene Zinsen;Nettobetrag;Währung Nettobetrag;Saldo;Währung Saldo\n"

    def test_position_from_prior_year_sold_after_dividend(self, taxpayer):
        # 100 shares held since 2024, dividend in April (CSV quantity 1),
        # all sold in June -> no year-end holding. Quantity at the dividend
        # date must be 100, not the raw CSV value.
        csv_content = self.HEADER + (
            "15-04-2025 08:00:00;;Dividende;NESTLE SA;CH0038863350;NESN;1;;;-105.00;;195.00;CHF;195.00;CHF\n"
            "10-06-2025 09:00:00;ORD1;Verkauf;NESTLE SA;CH0038863350;NESN;100;90.00;CHF;-10.00;;8'990.00;CHF;9'185.00;CHF\n"
        )
        stmt = SwissquoteCsvParser().parse(csv_content, tax_year=2025, taxpayer=taxpayer)
        assert stmt.holdings == []
        (payment,) = stmt.security_payments
        assert payment.quantity == Decimal("100")
        assert payment.amount_per_unit == Decimal("3")
        assert payment.withholding_tax == Decimal("105")

    def test_bought_and_sold_within_year(self, taxpayer):
        csv_content = self.HEADER + (
            "10-02-2025 09:00:00;ORD1;Kauf;NESTLE SA;CH0038863350;NESN;40;90.00;CHF;-10.00;;-3'610.00;CHF;-3'610.00;CHF\n"
            "15-04-2025 08:00:00;;Dividende;NESTLE SA;CH0038863350;NESN;1;;;-42.00;;78.00;CHF;-3'532.00;CHF\n"
            "10-06-2025 09:00:00;ORD2;Verkauf;NESTLE SA;CH0038863350;NESN;40;95.00;CHF;-10.00;;3'790.00;CHF;258.00;CHF\n"
        )
        stmt = SwissquoteCsvParser().parse(csv_content, tax_year=2025, taxpayer=taxpayer)
        (payment,) = stmt.security_payments
        assert payment.quantity == Decimal("40")
