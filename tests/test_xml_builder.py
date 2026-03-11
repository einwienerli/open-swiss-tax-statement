"""Tests for eCH-0196 XML builder."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree

from src.generators.ech0196.constants import NS_ECH0097, NS_ECH0196
from src.generators.ech0196.xml_builder import (
    _chf_or_original,
    _compute_totals,
    _determine_country,
    _determine_income_category,
    _determine_security_category,
    _format_date,
    _format_decimal,
    _parse_uid,
    _truncate,
    build_xml,
    serialize,
)
from src.models import (
    CashBalance,
    Holding,
    IncomeCategory,
    Institution,
    Security,
    SecurityPayment,
    TaxPayer,
    TaxStatement,
    Transaction,
    TransactionType,
)
from src.parsers.swissquote.constants import compute_swissquote_iban
from src.parsers.swissquote.csv_parser import SwissquoteCsvParser

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _find_security_by_isin(depot, isin: str):
    """Find a <security> element by ISIN within a depot."""
    for sec in depot.findall(f"{{{NS_ECH0196}}}security"):
        if sec.get("isin") == isin:
            return sec
    raise ValueError(f"No security with ISIN {isin} found in depot")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def taxpayer():
    return TaxPayer(
        first_name="Max",
        last_name="Muster",
        street="Bahnhofstrasse 1",
        zip_code="8001",
        city="Zurich",
        canton="ZH",
        ahv_number="756.1234.5678.90",
    )


@pytest.fixture
def institution():
    return Institution(
        name="Swissquote Bank SA",
        uid="CHE-106.787.734",
        clearing_number="08750",
    )


@pytest.fixture
def empty_statement(taxpayer, institution):
    """A minimal statement with no holdings/payments/expenses."""
    return TaxStatement(
        tax_year=2025,
        period_from=date(2025, 1, 1),
        period_to=date(2025, 12, 31),
        taxpayer=taxpayer,
        institution=institution,
        account_number="123456",
    )


@pytest.fixture
def nestle_security():
    return Security(
        isin="CH0038863350",
        name="NESTLE SA",
        currency="CHF",
        valor_number="3886335",
    )


@pytest.fixture
def apple_security():
    return Security(isin="US0378331005", name="APPLE INC", currency="USD")


@pytest.fixture
def vanguard_security():
    return Security(
        isin="IE00B3XXRP09",
        name="VANGUARD FTSE ALL-WORLD UCITS ETF",
        currency="EUR",
    )


@pytest.fixture
def rich_statement(taxpayer, institution, nestle_security, apple_security, vanguard_security):
    """A statement with multiple holdings, payments, cash balances, and expenses."""
    roche = Security(isin="CH0012032048", name="ROCHE HOLDING AG", currency="CHF")

    holdings = [
        Holding(security=nestle_security, quantity=Decimal("70"), tax_value_chf=Decimal("7000")),
        Holding(security=apple_security, quantity=Decimal("50"), tax_value_chf=Decimal("8000")),
        Holding(security=roche, quantity=Decimal("25"), tax_value_chf=Decimal("6250")),
        Holding(security=vanguard_security, quantity=Decimal("200"), tax_value_chf=Decimal("17060")),
    ]
    cash_balances = [
        CashBalance(currency="CHF", balance=Decimal("5000"), tax_value_chf=Decimal("5000"), interest_earned=Decimal("12.50")),
        CashBalance(currency="USD", balance=Decimal("3000"), tax_value_chf=Decimal("2700"), interest_earned=Decimal("0.85")),
        CashBalance(currency="EUR", balance=Decimal("-4520"), tax_value_chf=Decimal("0")),
    ]
    payments = [
        SecurityPayment(
            security=nestle_security,
            payment_date=date(2025, 3, 15),
            gross_amount=Decimal("300"),
            currency="CHF",
            withholding_tax=Decimal("105"),
            net_amount=Decimal("195"),
            quantity=Decimal("70"),
            income_category=IncomeCategory.RUBRIK_A,
        ),
        SecurityPayment(
            security=apple_security,
            payment_date=date(2025, 5, 10),
            gross_amount=Decimal("19.50"),
            currency="USD",
            withholding_tax=Decimal("5.85"),
            net_amount=Decimal("13.65"),
            quantity=Decimal("50"),
        ),
        SecurityPayment(
            security=apple_security,
            payment_date=date(2025, 11, 15),
            gross_amount=Decimal("19.50"),
            currency="USD",
            withholding_tax=Decimal("5.85"),
            net_amount=Decimal("13.65"),
            quantity=Decimal("50"),
        ),
    ]
    transactions = [
        Transaction(
            date=date(2025, 1, 15),
            type=TransactionType.BUY,
            security=nestle_security,
            quantity=Decimal("100"),
            unit_price=Decimal("95.50"),
            currency="CHF",
        ),
        Transaction(
            date=date(2025, 6, 1),
            type=TransactionType.SELL,
            security=nestle_security,
            quantity=Decimal("30"),
            unit_price=Decimal("98.20"),
            currency="CHF",
        ),
        Transaction(
            date=date(2025, 2, 20),
            type=TransactionType.BUY,
            security=apple_security,
            quantity=Decimal("50"),
            unit_price=Decimal("182.30"),
            currency="USD",
        ),
    ]
    expenses = [
        Transaction(
            date=date(2025, 9, 15),
            type=TransactionType.CUSTODY_FEE,
            net_amount=Decimal("-45"),
            currency="CHF",
        ),
    ]

    return TaxStatement(
        tax_year=2025,
        period_from=date(2025, 1, 1),
        period_to=date(2025, 12, 31),
        taxpayer=taxpayer,
        institution=institution,
        holdings=holdings,
        cash_balances=cash_balances,
        security_payments=payments,
        transactions=transactions,
        expenses=expenses,
        account_number="123456",
    )


# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------


class TestParseUid:
    def test_valid_uid(self):
        cat, num = _parse_uid("CHE-106.787.734")
        assert cat == "CHE"
        assert num == 106787734

    def test_adm_uid(self):
        cat, num = _parse_uid("ADM-100.000.001")
        assert cat == "ADM"
        assert num == 100000001

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid UID"):
            _parse_uid("INVALID")


class TestFormatDecimal:
    def test_none_returns_default(self):
        assert _format_decimal(None) == "0"

    def test_custom_default(self):
        assert _format_decimal(None, "N/A") == "N/A"

    def test_normal_value(self):
        assert _format_decimal(Decimal("123.45")) == "123.45"

    def test_zero(self):
        assert _format_decimal(Decimal("0")) == "0.00"

    def test_rounds_to_two_decimal_places(self):
        assert _format_decimal(Decimal("15.44888")) == "15.45"

    def test_trailing_zeros_preserved(self):
        assert _format_decimal(Decimal("100")) == "100.00"


class TestFormatDate:
    def test_iso_format(self):
        assert _format_date(date(2025, 3, 15)) == "2025-03-15"

    def test_year_end(self):
        assert _format_date(date(2025, 12, 31)) == "2025-12-31"


class TestDetermineSecurityCategory:
    def test_plain_stock(self):
        assert _determine_security_category(Security("CH0038863350", "NESTLE SA", "CHF")) == "SHARE"

    def test_etf_keyword(self):
        assert _determine_security_category(Security("IE00B3XXRP09", "VANGUARD FTSE ALL-WORLD UCITS ETF", "EUR")) == "FUND"

    def test_ishares(self):
        assert _determine_security_category(Security("IE00B4L5Y983", "ISHARES CORE MSCI WORLD", "USD")) == "FUND"

    def test_fund_keyword(self):
        assert _determine_security_category(Security("LU0000000001", "SWISSCANTO EQUITY FUND", "CHF")) == "FUND"

    def test_case_insensitive(self):
        assert _determine_security_category(Security("IE00B3XXRP09", "vanguard etf thing", "EUR")) == "FUND"


class TestDetermineCountry:
    def test_swiss(self):
        assert _determine_country("CH0038863350") == "CH"

    def test_us(self):
        assert _determine_country("US0378331005") == "US"

    def test_ireland(self):
        assert _determine_country("IE00B3XXRP09") == "IE"

    def test_short_isin(self):
        assert _determine_country("X") == "CH"


class TestDetermineIncomeCategory:
    def test_explicit_rubrik_a(self):
        sec = Security("CH0038863350", "NESTLE", "CHF")
        p = SecurityPayment(sec, date(2025, 1, 1), Decimal("100"), "CHF", Decimal("35"), Decimal("65"),
                            income_category=IncomeCategory.RUBRIK_A)
        assert _determine_income_category(p) == IncomeCategory.RUBRIK_A

    def test_explicit_rubrik_b(self):
        sec = Security("US0378331005", "APPLE", "USD")
        p = SecurityPayment(sec, date(2025, 1, 1), Decimal("100"), "USD", Decimal("15"), Decimal("85"),
                            income_category=IncomeCategory.RUBRIK_B)
        assert _determine_income_category(p) == IncomeCategory.RUBRIK_B

    def test_heuristic_swiss_with_wht(self):
        sec = Security("CH0038863350", "NESTLE", "CHF")
        p = SecurityPayment(sec, date(2025, 1, 1), Decimal("100"), "CHF", Decimal("35"), Decimal("65"))
        assert _determine_income_category(p) == IncomeCategory.RUBRIK_A

    def test_heuristic_foreign(self):
        sec = Security("US0378331005", "APPLE", "USD")
        p = SecurityPayment(sec, date(2025, 1, 1), Decimal("100"), "USD", Decimal("15"), Decimal("85"))
        assert _determine_income_category(p) == IncomeCategory.RUBRIK_B

    def test_security_level_category_used(self):
        sec = Security("US0378331005", "APPLE", "USD", income_category=IncomeCategory.RUBRIK_A)
        p = SecurityPayment(sec, date(2025, 1, 1), Decimal("100"), "USD", Decimal("15"), Decimal("85"))
        assert _determine_income_category(p) == IncomeCategory.RUBRIK_A


class TestTruncate:
    def test_short_string_unchanged(self):
        assert _truncate("hello", 60) == "hello"

    def test_long_string_truncated(self):
        assert _truncate("A" * 100, 60) == "A" * 60

    def test_exact_length(self):
        assert _truncate("A" * 60, 60) == "A" * 60


# ---------------------------------------------------------------------------
# Integration tests: XML structure
# ---------------------------------------------------------------------------


class TestComputeSwissquoteIban:
    def test_iban_length(self):
        assert len(compute_swissquote_iban("123456")) == 21

    def test_iban_starts_with_ch12(self):
        assert compute_swissquote_iban("123456").startswith("CH12")

    def test_known_account(self):
        assert compute_swissquote_iban("123456") == "CH1208781000012345600"

    def test_account_zero_padded(self):
        iban = compute_swissquote_iban("123")
        assert iban == "CH1208781000000012300"
        assert len(iban) == 21


class TestBuildXmlEmpty:
    """Tests for an empty statement (no holdings/payments)."""

    def test_produces_valid_xml(self, empty_statement):
        root = build_xml(empty_statement)
        xml = serialize(root)
        assert xml.startswith("<?xml version='1.0' encoding='UTF-8'?>")

    def test_root_tag(self, empty_statement):
        root = build_xml(empty_statement)
        assert root.tag == f"{{{NS_ECH0196}}}taxStatement"

    def test_root_attributes(self, empty_statement):
        root = build_xml(empty_statement)
        assert root.get("taxPeriod") == "2025"
        assert root.get("canton") == "ZH"
        assert root.get("country") == "CH"
        assert root.get("periodFrom") == "2025-01-01"
        assert root.get("periodTo") == "2025-12-31"
        assert root.get("minorVersion") == "22"
        assert root.get("totalTaxValue") == "0.00"
        assert root.get("totalGrossRevenueA") == "0.00"
        assert root.get("totalGrossRevenueB") == "0.00"
        assert root.get("totalWithHoldingTaxClaim") == "0.00"

    def test_schema_location(self, empty_statement):
        root = build_xml(empty_statement)
        xsi_ns = "http://www.w3.org/2001/XMLSchema-instance"
        loc = root.get(f"{{{xsi_ns}}}schemaLocation")
        assert loc is not None
        assert "eCH-0196-2-2.xsd" in loc
        assert "eCH-0097-4-0.xsd" in loc

    def test_only_required_namespaces(self, empty_statement):
        root = build_xml(empty_statement)
        xml = serialize(root)
        assert "eCH-0007" not in xml
        assert "eCH-0008" not in xml
        assert "eCH-0010" not in xml
        assert "eCH-0097" in xml

    def test_statement_id_format(self, empty_statement):
        root = build_xml(empty_statement)
        stmt_id = root.get("id")
        # CH + clearing(5) + account(14) + stichtag(8) + seq(2) = 31 chars
        assert stmt_id == "CH08750000000001234562025123101"
        assert len(stmt_id) == 31

    def test_client_number_is_account(self, empty_statement):
        root = build_xml(empty_statement)
        client = root.find(f"{{{NS_ECH0196}}}client")
        assert client.get("clientNumber") == "123456"

    def test_institution_present(self, empty_statement):
        root = build_xml(empty_statement)
        inst = root.find(f"{{{NS_ECH0196}}}institution")
        assert inst is not None
        assert inst.get("name") == "Swissquote Bank SA"

    def test_uid_structure(self, empty_statement):
        root = build_xml(empty_statement)
        inst = root.find(f"{{{NS_ECH0196}}}institution")
        # uid element is in eCH-0196 namespace, children in eCH-0097
        uid = inst.find(f"{{{NS_ECH0196}}}uid")
        assert uid is not None
        cat = uid.find(f"{{{NS_ECH0097}}}uidOrganisationIdCategorie")
        assert cat.text == "CHE"
        num = uid.find(f"{{{NS_ECH0097}}}uidOrganisationId")
        assert num.text == "106787734"

    def test_client_present(self, empty_statement):
        root = build_xml(empty_statement)
        client = root.find(f"{{{NS_ECH0196}}}client")
        assert client is not None
        assert client.get("firstName") == "Max"
        assert client.get("lastName") == "Muster"
        # AHV number (13 digits stripped) exceeds TIN maxLength=11, so not emitted
        assert client.get("tin") is None

    def test_no_optional_sections(self, empty_statement):
        root = build_xml(empty_statement)
        assert root.find(f"{{{NS_ECH0196}}}listOfBankAccounts") is None
        assert root.find(f"{{{NS_ECH0196}}}listOfExpenses") is None
        assert root.find(f"{{{NS_ECH0196}}}listOfSecurities") is None


class TestBuildXmlRich:
    """Tests for a fully populated statement."""

    def test_bank_accounts_present(self, rich_statement):
        root = build_xml(rich_statement)
        ba = root.find(f"{{{NS_ECH0196}}}listOfBankAccounts")
        assert ba is not None
        accounts = ba.findall(f"{{{NS_ECH0196}}}bankAccount")
        assert len(accounts) == 3

    def test_bank_account_attributes(self, rich_statement):
        root = build_xml(rich_statement)
        ba = root.find(f"{{{NS_ECH0196}}}listOfBankAccounts")
        acct = ba.findall(f"{{{NS_ECH0196}}}bankAccount")[0]
        assert acct.get("bankAccountCurrency") == "CHF"
        assert acct.get("bankAccountCountry") == "CH"
        assert acct.get("bankAccountName") == "Cash CHF"

    def test_bank_account_iban_emitted(self, taxpayer, institution):
        stmt = TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
            account_number="123456",
            cash_balances=[
                CashBalance("CHF", Decimal("5000"), Decimal("5000"),
                            iban="CH1208781000012345600"),
            ],
        )
        root = build_xml(stmt)
        ba = root.find(f"{{{NS_ECH0196}}}listOfBankAccounts")
        acct = ba.find(f"{{{NS_ECH0196}}}bankAccount")
        assert acct.get("iban") == "CH1208781000012345600"

    def test_bank_account_no_iban_when_none(self, rich_statement):
        root = build_xml(rich_statement)
        ba = root.find(f"{{{NS_ECH0196}}}listOfBankAccounts")
        acct = ba.findall(f"{{{NS_ECH0196}}}bankAccount")[0]
        assert acct.get("iban") is None  # rich_statement doesn't set iban

    def test_bank_account_tax_value(self, rich_statement):
        root = build_xml(rich_statement)
        ba = root.find(f"{{{NS_ECH0196}}}listOfBankAccounts")
        acct = ba.findall(f"{{{NS_ECH0196}}}bankAccount")[0]
        tv = acct.find(f"{{{NS_ECH0196}}}taxValue")
        assert tv is not None
        assert tv.get("referenceDate") == "2025-12-31"
        assert tv.get("balance") == "5000.00"

    def test_bank_account_interest_payment(self, rich_statement):
        root = build_xml(rich_statement)
        ba = root.find(f"{{{NS_ECH0196}}}listOfBankAccounts")
        chf_acct = ba.findall(f"{{{NS_ECH0196}}}bankAccount")[0]
        pmt = chf_acct.find(f"{{{NS_ECH0196}}}payment")
        assert pmt is not None
        assert pmt.get("grossRevenueB") == "12.50"  # already 2dp

    def test_expenses_present(self, rich_statement):
        root = build_xml(rich_statement)
        exp = root.find(f"{{{NS_ECH0196}}}listOfExpenses")
        assert exp is not None
        assert exp.get("totalExpenses") == "45.00"
        assert exp.get("totalExpensesDeductible") == "45.00"
        expenses = exp.findall(f"{{{NS_ECH0196}}}expense")
        assert len(expenses) == 1
        assert expenses[0].get("expenseType") == "4"
        assert expenses[0].get("expenses") == "45.00"
        assert expenses[0].get("expensesDeductible") == "45.00"

    def test_securities_present(self, rich_statement):
        root = build_xml(rich_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        assert los is not None
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        assert depot is not None
        securities = depot.findall(f"{{{NS_ECH0196}}}security")
        assert len(securities) == 4

    def test_security_attributes(self, rich_statement):
        root = build_xml(rich_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        sec = _find_security_by_isin(depot, "CH0038863350")
        assert sec.get("country") == "CH"
        assert sec.get("currency") == "CHF"
        assert sec.get("securityCategory") == "SHARE"
        assert sec.get("securityName") == "NESTLE SA"
        assert sec.get("quotationType") == "PIECE"
        assert sec.get("valorNumber") == "3886335"

    def test_vanguard_is_fund(self, rich_statement):
        root = build_xml(rich_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        securities = depot.findall(f"{{{NS_ECH0196}}}security")
        vanguard = [s for s in securities if s.get("isin") == "IE00B3XXRP09"]
        assert len(vanguard) == 1
        assert vanguard[0].get("securityCategory") == "FUND"

    def test_nestle_has_payment(self, rich_statement):
        root = build_xml(rich_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        nestle = _find_security_by_isin(depot, "CH0038863350")
        payments = nestle.findall(f"{{{NS_ECH0196}}}payment")
        assert len(payments) == 1
        assert payments[0].get("name") == "Bardividende"
        assert payments[0].get("paymentDate") == "2025-03-15"
        assert payments[0].get("grossRevenueA") == "300.00"
        assert payments[0].get("withHoldingTaxClaim") == "105.00"

    def test_apple_has_two_payments(self, rich_statement):
        root = build_xml(rich_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        apple = _find_security_by_isin(depot, "US0378331005")
        payments = apple.findall(f"{{{NS_ECH0196}}}payment")
        assert len(payments) == 2

    def test_nestle_has_stock_mutations(self, rich_statement):
        root = build_xml(rich_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        nestle = _find_security_by_isin(depot, "CH0038863350")
        stocks = nestle.findall(f"{{{NS_ECH0196}}}stock")
        assert len(stocks) == 3  # opening + buy + sell
        # Opening balance (70 - 100 + 30 = 0)
        opening = stocks[0]
        assert opening.get("mutation") == "0"
        assert opening.get("referenceDate") == "2025-01-01"
        assert opening.get("quantity") == "0.00"
        # Buy mutation
        buy = [s for s in stocks if s.get("name") == "Buy"][0]
        assert buy.get("quantity") == "100.00"
        assert buy.get("mutation") == "1"
        # Sell mutation
        sell = [s for s in stocks if s.get("name") == "Sell"][0]
        assert sell.get("quantity") == "-30.00"

    def test_opening_balance_with_prior_holding(self, rich_statement):
        """Securities held before any intra-year trades get correct opening quantity."""
        # Modify Nestle: year-end=70, only sell 30 → opening = 70 - (-30) = 100
        rich_statement.transactions = [
            t for t in rich_statement.transactions
            if not (t.security and t.security.isin == "CH0038863350" and t.type == TransactionType.BUY)
        ]
        root = build_xml(rich_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        nestle = _find_security_by_isin(depot, "CH0038863350")
        stocks = nestle.findall(f"{{{NS_ECH0196}}}stock")
        assert len(stocks) == 2  # opening + sell
        opening = stocks[0]
        assert opening.get("mutation") == "0"
        assert opening.get("referenceDate") == "2025-01-01"
        assert opening.get("quantity") == "100.00"  # 70 - (-30) = 100
        assert opening.get("balanceCurrency") == "CHF"
        assert opening.get("name") is None

    def test_opening_balance_zero_when_bought_during_year(self, rich_statement):
        """Apple: year-end=50, bought 50 → opening=0, still emitted."""
        root = build_xml(rich_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        apple = _find_security_by_isin(depot, "US0378331005")
        stocks = apple.findall(f"{{{NS_ECH0196}}}stock")
        assert len(stocks) == 2  # opening (qty=0) + buy
        opening = stocks[0]
        assert opening.get("mutation") == "0"
        assert opening.get("quantity") == "0.00"

    def test_opening_balance_for_no_transactions(self, rich_statement):
        """Roche: held all year, no transactions → opening = year-end qty."""
        root = build_xml(rich_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        roche = _find_security_by_isin(depot, "CH0012032048")
        stocks = roche.findall(f"{{{NS_ECH0196}}}stock")
        assert len(stocks) == 1  # only opening
        opening = stocks[0]
        assert opening.get("mutation") == "0"
        assert opening.get("referenceDate") == "2025-01-01"
        assert opening.get("quantity") == "25.00"

    def test_list_of_securities_totals(self, rich_statement):
        root = build_xml(rich_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        assert Decimal(los.get("totalLumpSumTaxCredit")) == Decimal("0")
        assert Decimal(los.get("totalNonRecoverableTax")) == Decimal("0")
        assert los.get("totalAdditionalWithHoldingTaxUSA") == "0"
        assert los.get("totalGrossRevenueIUP") == "0"
        assert los.get("totalGrossRevenueConversion") == "0"

    def test_security_tax_value(self, rich_statement):
        root = build_xml(rich_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        nestle = _find_security_by_isin(depot, "CH0038863350")
        tv = nestle.find(f"{{{NS_ECH0196}}}taxValue")
        assert tv is not None
        assert tv.get("quantity") == "70.00"
        assert tv.get("balanceCurrency") == "CHF"
        assert tv.get("value") == "7000.00"

    def test_depot_number_is_account(self, rich_statement):
        root = build_xml(rich_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        assert depot.get("depotNumber") == "123456"


class TestComputeTotals:
    def test_empty_statement(self, empty_statement):
        totals = _compute_totals(empty_statement)
        assert totals["total_tax_value"] == Decimal("0")
        assert totals["total_revenue_a"] == Decimal("0")
        assert totals["total_revenue_b"] == Decimal("0")
        assert totals["total_wht"] == Decimal("0")

    def test_rich_statement(self, rich_statement):
        totals = _compute_totals(rich_statement)
        assert totals["sec_tax_value"] == Decimal("38310")  # 7000+8000+6250+17060
        assert totals["cash_tax_value"] == Decimal("7700")  # 5000+2700+0
        assert totals["total_tax_value"] == Decimal("46010")
        assert totals["sec_revenue_a"] == Decimal("300")  # NESTLE dividend
        assert totals["sec_wht"] == Decimal("105")

    def test_precomputed_totals_used(self, empty_statement):
        empty_statement.total_tax_value = Decimal("999")
        totals = _compute_totals(empty_statement)
        assert totals["total_tax_value"] == Decimal("999")


class TestSerialize:
    def test_xml_declaration(self, empty_statement):
        root = build_xml(empty_statement)
        xml = serialize(root)
        assert xml.startswith("<?xml version='1.0' encoding='UTF-8'?>")

    def test_pretty_print(self, empty_statement):
        root = build_xml(empty_statement)
        xml = serialize(root, pretty_print=True)
        assert "\n" in xml

    def test_compact(self, empty_statement):
        root = build_xml(empty_statement)
        xml = serialize(root, pretty_print=False)
        assert xml.startswith("<?xml")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_security_name_truncated_to_60(self, taxpayer, institution):
        long_name = "A" * 100
        sec = Security("CH0000000001", long_name, "CHF")
        stmt = TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
            holdings=[Holding(sec, Decimal("10"))],
            account_number="123456",
        )
        root = build_xml(stmt)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        sec_el = depot.find(f"{{{NS_ECH0196}}}security")
        assert len(sec_el.get("securityName")) == 60

    def test_none_tax_values_default_to_zero(self, taxpayer, institution):
        sec = Security("CH0000000001", "TEST", "CHF")
        stmt = TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
            holdings=[Holding(sec, Decimal("10"))],  # tax_value_chf=None
            cash_balances=[CashBalance("CHF", Decimal("100"))],  # tax_value_chf=None
            account_number="123456",
        )
        root = build_xml(stmt)
        assert root.get("totalTaxValue") == "0.00"

    def test_missing_valor_number_omitted(self, taxpayer, institution):
        sec = Security("CH0000000001", "TEST STOCK", "CHF")
        stmt = TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
            holdings=[Holding(sec, Decimal("10"))],
            account_number="123456",
        )
        root = build_xml(stmt)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        sec_el = depot.find(f"{{{NS_ECH0196}}}security")
        assert sec_el.get("valorNumber") is None  # Not present


# ---------------------------------------------------------------------------
# CSV → XML integration test
# ---------------------------------------------------------------------------


class TestCsvToXmlIntegration:
    """Parse a real Swissquote CSV, build XML, check structure."""

    @pytest.fixture
    def statement_from_csv(self, taxpayer):
        csv = (FIXTURES_DIR / "swissquote_transactions.csv").read_text(encoding="utf-8")
        parser = SwissquoteCsvParser()
        return parser.parse(csv, 2025, taxpayer)

    def test_root_attributes(self, statement_from_csv):
        root = build_xml(statement_from_csv)
        assert root.get("taxPeriod") == "2025"
        assert root.get("canton") == "ZH"
        assert root.get("periodFrom") == "2025-01-01"
        assert root.get("periodTo") == "2025-12-31"

    def test_institution_with_uid(self, statement_from_csv):
        root = build_xml(statement_from_csv)
        inst = root.find(f"{{{NS_ECH0196}}}institution")
        assert inst.get("name") == "Swissquote Bank SA"
        uid = inst.find(f"{{{NS_ECH0196}}}uid")
        assert uid is not None

    def test_client_name(self, statement_from_csv):
        root = build_xml(statement_from_csv)
        client = root.find(f"{{{NS_ECH0196}}}client")
        assert client.get("firstName") == "Max"
        assert client.get("lastName") == "Muster"

    def test_bank_accounts_count(self, statement_from_csv):
        root = build_xml(statement_from_csv)
        ba = root.find(f"{{{NS_ECH0196}}}listOfBankAccounts")
        accounts = ba.findall(f"{{{NS_ECH0196}}}bankAccount")
        assert len(accounts) >= 3  # CHF, USD, EUR

    def test_expenses_present(self, statement_from_csv):
        root = build_xml(statement_from_csv)
        exp = root.find(f"{{{NS_ECH0196}}}listOfExpenses")
        assert exp is not None

    def test_five_securities_including_payment_only(self, statement_from_csv):
        """4 holdings + 1 payment-only (Zurich Insurance, sold before year-end)."""
        root = build_xml(statement_from_csv)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        securities = depot.findall(f"{{{NS_ECH0196}}}security")
        assert len(securities) == 5
        # Zurich Insurance has no taxValue (payment-only)
        zurich = _find_security_by_isin(depot, "CH0011075394")
        assert zurich.find(f"{{{NS_ECH0196}}}taxValue") is None
        assert zurich.find(f"{{{NS_ECH0196}}}payment") is not None

    def test_serialize_produces_valid_string(self, statement_from_csv):
        root = build_xml(statement_from_csv)
        xml = serialize(root)
        assert isinstance(xml, str)
        assert "taxStatement" in xml
        # Re-parse to verify well-formed XML
        etree.fromstring(xml.encode("utf-8"))


# ---------------------------------------------------------------------------
# CHF conversion / exchangeRate tests
# ---------------------------------------------------------------------------


class TestChfOrOriginal:
    def test_returns_chf_when_available(self):
        assert _chf_or_original(Decimal("100"), Decimal("200")) == Decimal("100")

    def test_returns_original_when_none(self):
        assert _chf_or_original(None, Decimal("200")) == Decimal("200")


class TestEnrichedXml:
    """Tests for XML output with enriched (CHF-converted) data."""

    @pytest.fixture
    def enriched_statement(self, taxpayer, institution):
        """A statement with enriched CHF values and exchange rates."""
        apple = Security("US0378331005", "APPLE INC", "USD", valor_number="908440",
                         tax_value_per_unit_chf=Decimal("201.60"))
        nestle = Security("CH0038863350", "NESTLE SA", "CHF", valor_number="3886335",
                          tax_value_per_unit_chf=Decimal("74.52"))

        return TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
            holdings=[
                Holding(nestle, Decimal("70"), Decimal("5216.40")),
                Holding(apple, Decimal("50"), Decimal("10080"),
                        exchange_rate=Decimal("0.792250")),
            ],
            cash_balances=[
                CashBalance("CHF", Decimal("5000"), Decimal("5000"),
                            Decimal("12.50"), Decimal("12.50")),
                CashBalance("USD", Decimal("3000"), Decimal("2376.75"),
                            Decimal("5"), Decimal("3.96125"),
                            exchange_rate=Decimal("0.792250")),
            ],
            security_payments=[
                SecurityPayment(
                    nestle, date(2025, 3, 15), Decimal("300"), "CHF",
                    Decimal("105"), Decimal("195"),
                    quantity=Decimal("70"),
                    income_category=IncomeCategory.RUBRIK_A,
                    gross_amount_chf=Decimal("300"),
                    withholding_tax_chf=Decimal("105"),
                ),
                SecurityPayment(
                    apple, date(2025, 5, 10), Decimal("19.50"), "USD",
                    Decimal("5.85"), Decimal("13.65"),
                    quantity=Decimal("50"),
                    gross_amount_chf=Decimal("15.44888"),
                    withholding_tax_chf=Decimal("4.63466"),
                    exchange_rate=Decimal("0.792250"),
                ),
            ],
            account_number="123456",
        )

    def test_foreign_security_tax_value_chf_native(self, enriched_statement):
        """Foreign security taxValue: balanceCurrency=CHF, unitPrice in CHF, no exchangeRate."""
        root = build_xml(enriched_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        apple = _find_security_by_isin(depot, "US0378331005")
        tv = apple.find(f"{{{NS_ECH0196}}}taxValue")
        assert tv.get("balanceCurrency") == "CHF"
        assert tv.get("unitPrice") == "201.60"
        assert tv.get("exchangeRate") is None  # Not needed when everything is CHF

    def test_chf_security_tax_value_chf_native(self, enriched_statement):
        """CHF security taxValue: same CHF-native approach, no exchangeRate."""
        root = build_xml(enriched_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        nestle = _find_security_by_isin(depot, "CH0038863350")
        tv = nestle.find(f"{{{NS_ECH0196}}}taxValue")
        assert tv.get("balanceCurrency") == "CHF"
        assert tv.get("unitPrice") == "74.52"
        assert tv.get("exchangeRate") is None

    def test_payment_has_exchange_rate(self, enriched_statement):
        root = build_xml(enriched_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        apple = _find_security_by_isin(depot, "US0378331005")
        pmt = apple.find(f"{{{NS_ECH0196}}}payment")
        assert pmt.get("exchangeRate") == "0.79225"

    def test_payment_uses_chf_gross(self, enriched_statement):
        root = build_xml(enriched_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        apple = _find_security_by_isin(depot, "US0378331005")
        pmt = apple.find(f"{{{NS_ECH0196}}}payment")
        assert pmt.get("grossRevenueB") == "15.45"

    def test_bank_account_exchange_rate(self, enriched_statement):
        root = build_xml(enriched_statement)
        ba = root.find(f"{{{NS_ECH0196}}}listOfBankAccounts")
        usd_acct = ba.findall(f"{{{NS_ECH0196}}}bankAccount")[1]
        tv = usd_acct.find(f"{{{NS_ECH0196}}}taxValue")
        assert tv.get("exchangeRate") == "0.79225"

    def test_bank_account_interest_uses_chf(self, enriched_statement):
        root = build_xml(enriched_statement)
        ba = root.find(f"{{{NS_ECH0196}}}listOfBankAccounts")
        usd_acct = ba.findall(f"{{{NS_ECH0196}}}bankAccount")[1]
        pmt = usd_acct.find(f"{{{NS_ECH0196}}}payment")
        assert pmt.get("grossRevenueB") == "3.96"

    def test_chf_bank_account_no_exchange_rate(self, enriched_statement):
        root = build_xml(enriched_statement)
        ba = root.find(f"{{{NS_ECH0196}}}listOfBankAccounts")
        chf_acct = ba.findall(f"{{{NS_ECH0196}}}bankAccount")[0]
        tv = chf_acct.find(f"{{{NS_ECH0196}}}taxValue")
        assert tv.get("exchangeRate") is None

    def test_totals_use_chf_values(self, enriched_statement):
        totals = _compute_totals(enriched_statement)
        # Revenue A: NESTLE CHF 300 (already CHF)
        assert totals["sec_revenue_a"] == Decimal("300")
        # Revenue B: APPLE USD 19.50 → CHF 15.44888
        assert totals["sec_revenue_b"] == Decimal("15.44888")
        # WHT: NESTLE CHF 105
        assert totals["sec_wht"] == Decimal("105")
        # Bank interest: CHF 12.50 + USD 3.96125
        assert totals["bank_revenue_b"] == Decimal("12.50") + Decimal("3.96125")


# ---------------------------------------------------------------------------
# Payment-only securities (core bugfix tests)
# ---------------------------------------------------------------------------


class TestPaymentOnlySecurities:
    """Tests for securities that have payments but no year-end holding."""

    @pytest.fixture
    def payment_only_statement(self, taxpayer, institution):
        """Statement with only payments, no holdings."""
        zurich = Security("CH0011075394", "ZURICH INSURANCE GROUP AG", "CHF")
        return TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
            account_number="123456",
            security_payments=[
                SecurityPayment(
                    security=zurich,
                    payment_date=date(2025, 6, 20),
                    gross_amount=Decimal("123.50"),
                    currency="CHF",
                    withholding_tax=Decimal("43.23"),
                    net_amount=Decimal("80.27"),
                    quantity=Decimal("10"),
                ),
            ],
        )

    @pytest.fixture
    def mixed_statement(self, taxpayer, institution):
        """Statement with both holdings+payments and payment-only securities."""
        nestle = Security("CH0038863350", "NESTLE SA", "CHF", valor_number="3886335")
        zurich = Security("CH0011075394", "ZURICH INSURANCE GROUP AG", "CHF")
        return TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
            account_number="123456",
            holdings=[
                Holding(nestle, Decimal("70"), Decimal("7000")),
            ],
            security_payments=[
                SecurityPayment(
                    security=nestle,
                    payment_date=date(2025, 3, 15),
                    gross_amount=Decimal("300"),
                    currency="CHF",
                    withholding_tax=Decimal("105"),
                    net_amount=Decimal("195"),
                    quantity=Decimal("70"),
                    income_category=IncomeCategory.RUBRIK_A,
                ),
                SecurityPayment(
                    security=zurich,
                    payment_date=date(2025, 6, 20),
                    gross_amount=Decimal("123.50"),
                    currency="CHF",
                    withholding_tax=Decimal("43.23"),
                    net_amount=Decimal("80.27"),
                    quantity=Decimal("10"),
                ),
            ],
        )

    def test_payment_only_emits_list_of_securities(self, payment_only_statement):
        """No holdings but payments → listOfSecurities still emitted."""
        root = build_xml(payment_only_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        assert los is not None
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        securities = depot.findall(f"{{{NS_ECH0196}}}security")
        assert len(securities) == 1

    def test_payment_only_no_tax_value(self, payment_only_statement):
        """Payment-only security has no <taxValue> element."""
        root = build_xml(payment_only_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        sec = depot.find(f"{{{NS_ECH0196}}}security")
        assert sec.find(f"{{{NS_ECH0196}}}taxValue") is None

    def test_payment_only_has_payment(self, payment_only_statement):
        """Payment-only security has <payment> element."""
        root = build_xml(payment_only_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        sec = depot.find(f"{{{NS_ECH0196}}}security")
        pmt = sec.find(f"{{{NS_ECH0196}}}payment")
        assert pmt is not None
        assert pmt.get("paymentDate") == "2025-06-20"
        assert pmt.get("amount") == "123.50"  # already 2dp

    def test_payment_quantity_from_dividend(self, payment_only_statement):
        """Payment quantity comes from the dividend transaction, not holding."""
        root = build_xml(payment_only_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        sec = depot.find(f"{{{NS_ECH0196}}}security")
        pmt = sec.find(f"{{{NS_ECH0196}}}payment")
        assert pmt.get("quantity") == "10.00"

    def test_empty_holdings_empty_payments_no_list(self, empty_statement):
        """Empty holdings + empty payments → no listOfSecurities."""
        root = build_xml(empty_statement)
        assert root.find(f"{{{NS_ECH0196}}}listOfSecurities") is None

    def test_mixed_securities_count(self, mixed_statement):
        """Mixed: 1 holding+payment + 1 payment-only → 2 securities."""
        root = build_xml(mixed_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        securities = depot.findall(f"{{{NS_ECH0196}}}security")
        assert len(securities) == 2

    def test_mixed_holding_has_tax_value(self, mixed_statement):
        """Nestle (has holding) should have taxValue."""
        root = build_xml(mixed_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        nestle = _find_security_by_isin(depot, "CH0038863350")
        assert nestle.find(f"{{{NS_ECH0196}}}taxValue") is not None

    def test_mixed_payment_only_no_tax_value(self, mixed_statement):
        """Zurich (payment-only) should have no taxValue."""
        root = build_xml(mixed_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        zurich = _find_security_by_isin(depot, "CH0011075394")
        assert zurich.find(f"{{{NS_ECH0196}}}taxValue") is None
        pmt = zurich.find(f"{{{NS_ECH0196}}}payment")
        assert pmt is not None

    def test_payment_uses_own_quantity_not_holding(self, mixed_statement):
        """Nestle payment uses quantity=70 from dividend, not from holding."""
        root = build_xml(mixed_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        nestle = _find_security_by_isin(depot, "CH0038863350")
        pmt = nestle.find(f"{{{NS_ECH0196}}}payment")
        assert pmt.get("quantity") == "70.00"

    def test_payment_has_amount_per_unit(self, taxpayer, institution):
        """amountPerUnit is emitted when set on SecurityPayment."""
        sec = Security("CH0038863350", "NESTLE SA", "CHF")
        stmt = TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
            account_number="123456",
            security_payments=[
                SecurityPayment(
                    security=sec,
                    payment_date=date(2025, 3, 15),
                    gross_amount=Decimal("300"),
                    currency="CHF",
                    withholding_tax=Decimal("105"),
                    net_amount=Decimal("195"),
                    quantity=Decimal("100"),
                    amount_per_unit=Decimal("3.00"),
                ),
            ],
        )
        root = build_xml(stmt)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        sec_el = depot.find(f"{{{NS_ECH0196}}}security")
        pmt = sec_el.find(f"{{{NS_ECH0196}}}payment")
        assert pmt.get("amountPerUnit") == "3.00"

    def test_payment_has_ex_date(self, taxpayer, institution):
        """exDate is emitted when set on SecurityPayment."""
        sec = Security("CH0038863350", "NESTLE SA", "CHF")
        stmt = TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
            account_number="123456",
            security_payments=[
                SecurityPayment(
                    security=sec,
                    payment_date=date(2025, 3, 15),
                    gross_amount=Decimal("300"),
                    currency="CHF",
                    withholding_tax=Decimal("105"),
                    net_amount=Decimal("195"),
                    quantity=Decimal("100"),
                    ex_date=date(2025, 3, 7),
                ),
            ],
        )
        root = build_xml(stmt)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        sec_el = depot.find(f"{{{NS_ECH0196}}}security")
        pmt = sec_el.find(f"{{{NS_ECH0196}}}payment")
        assert pmt.get("exDate") == "2025-03-07"

    def test_payment_omits_ex_date_when_none(self, payment_only_statement):
        """exDate is omitted when not set."""
        root = build_xml(payment_only_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        sec = depot.find(f"{{{NS_ECH0196}}}security")
        pmt = sec.find(f"{{{NS_ECH0196}}}payment")
        assert pmt.get("exDate") is None


class TestFullySoldPositions:
    """Tests for securities bought and sold during the year with no year-end holding."""

    @pytest.fixture
    def sold_only_statement(self, taxpayer, institution):
        """Security bought in Jan, sold in Jun — no holding, no dividend."""
        sec = Security("CH0000000001", "SOLD STOCK AG", "CHF")
        return TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
            account_number="123456",
            transactions=[
                Transaction(
                    date=date(2025, 1, 15), type=TransactionType.BUY,
                    security=sec, quantity=Decimal("50"),
                    unit_price=Decimal("100"), currency="CHF",
                ),
                Transaction(
                    date=date(2025, 6, 15), type=TransactionType.SELL,
                    security=sec, quantity=Decimal("50"),
                    unit_price=Decimal("110"), currency="CHF",
                ),
            ],
        )

    @pytest.fixture
    def sold_with_dividend_statement(self, taxpayer, institution):
        """Dividend in Q1, sold in Q2 — no holding, but has payment + trades."""
        sec = Security("CH0011075394", "ZURICH INSURANCE GROUP AG", "CHF")
        return TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
            account_number="123456",
            security_payments=[
                SecurityPayment(
                    security=sec, payment_date=date(2025, 4, 10),
                    gross_amount=Decimal("260"), currency="CHF",
                    withholding_tax=Decimal("91"), net_amount=Decimal("169"),
                    quantity=Decimal("10"),
                ),
            ],
            transactions=[
                Transaction(
                    date=date(2025, 1, 10), type=TransactionType.BUY,
                    security=sec, quantity=Decimal("10"),
                    unit_price=Decimal("500"), currency="CHF",
                ),
                Transaction(
                    date=date(2025, 6, 20), type=TransactionType.SELL,
                    security=sec, quantity=Decimal("10"),
                    unit_price=Decimal("520"), currency="CHF",
                ),
            ],
        )

    def test_sold_only_emits_list_of_securities(self, sold_only_statement):
        """Trades only (no holdings, no payments) → listOfSecurities still emitted."""
        root = build_xml(sold_only_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        assert los is not None

    def test_sold_only_has_security_element(self, sold_only_statement):
        root = build_xml(sold_only_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        securities = depot.findall(f"{{{NS_ECH0196}}}security")
        assert len(securities) == 1
        assert securities[0].get("isin") == "CH0000000001"

    def test_sold_only_has_zero_tax_value(self, sold_only_statement):
        """Fully sold position gets taxValue with quantity=0 and value=0."""
        root = build_xml(sold_only_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        sec = depot.find(f"{{{NS_ECH0196}}}security")
        tv = sec.find(f"{{{NS_ECH0196}}}taxValue")
        assert tv is not None
        assert tv.get("quantity") == "0.00"
        assert tv.get("value") == "0.00"

    def test_sold_only_has_stock_mutations(self, sold_only_statement):
        root = build_xml(sold_only_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        sec = depot.find(f"{{{NS_ECH0196}}}security")
        stocks = sec.findall(f"{{{NS_ECH0196}}}stock")
        assert len(stocks) == 3  # opening (qty=0) + buy + sell

    def test_sold_only_no_payment(self, sold_only_statement):
        root = build_xml(sold_only_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        sec = depot.find(f"{{{NS_ECH0196}}}security")
        assert sec.find(f"{{{NS_ECH0196}}}payment") is None

    def test_sold_with_dividend_has_both(self, sold_with_dividend_statement):
        """Q1 dividend + Q2 sell: payment, zero taxValue, and stock mutations."""
        root = build_xml(sold_with_dividend_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        sec = depot.find(f"{{{NS_ECH0196}}}security")
        # Has zero-quantity taxValue
        tv = sec.find(f"{{{NS_ECH0196}}}taxValue")
        assert tv is not None
        assert tv.get("quantity") == "0.00"
        assert tv.get("value") == "0.00"
        # Has payment
        pmt = sec.find(f"{{{NS_ECH0196}}}payment")
        assert pmt is not None
        assert pmt.get("amount") == "260.00"
        # Has stock mutations
        stocks = sec.findall(f"{{{NS_ECH0196}}}stock")
        assert len(stocks) == 3  # opening (qty=0) + buy + sell

    def test_no_trades_no_holdings_no_list(self, empty_statement):
        """Still no listOfSecurities when there's nothing at all."""
        root = build_xml(empty_statement)
        assert root.find(f"{{{NS_ECH0196}}}listOfSecurities") is None


class TestDa1Attributes:
    """Tests for DA-1 rate attributes on Rubrik B payments."""

    @pytest.fixture
    def da1_statement(self, taxpayer, institution):
        """Statement with a Rubrik B payment that has DA-1 enrichment data."""
        apple = Security("US0378331005", "APPLE INC", "USD", valor_number="908440",
                         tax_value_per_unit_chf=Decimal("201.60"))
        return TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
            account_number="123456",
            holdings=[
                Holding(apple, Decimal("50"), Decimal("10080"),
                        exchange_rate=Decimal("0.90")),
            ],
            security_payments=[
                SecurityPayment(
                    security=apple,
                    payment_date=date(2025, 5, 10),
                    gross_amount=Decimal("19.50"),
                    currency="USD",
                    withholding_tax=Decimal("5.85"),
                    net_amount=Decimal("13.65"),
                    quantity=Decimal("50"),
                    gross_amount_chf=Decimal("17.55"),
                    withholding_tax_chf=Decimal("5.27"),
                    exchange_rate=Decimal("0.90"),
                    non_recoverable_tax_pct=Decimal("15"),
                    non_recoverable_tax_chf=Decimal("2.63"),
                    lump_sum_tax_credit_pct=Decimal("15"),
                    lump_sum_tax_credit_chf=Decimal("2.63"),
                ),
            ],
        )

    def test_rubrik_b_has_lump_sum_credit_flag(self, da1_statement):
        root = build_xml(da1_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        apple = _find_security_by_isin(depot, "US0378331005")
        pmt = apple.find(f"{{{NS_ECH0196}}}payment")
        assert pmt.get("lumpSumTaxCredit") == "1"

    def test_rubrik_b_has_lump_sum_percent(self, da1_statement):
        root = build_xml(da1_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        apple = _find_security_by_isin(depot, "US0378331005")
        pmt = apple.find(f"{{{NS_ECH0196}}}payment")
        assert pmt.get("lumpSumTaxCreditPercent") == "15.00"

    def test_rubrik_b_has_lump_sum_amount(self, da1_statement):
        root = build_xml(da1_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        apple = _find_security_by_isin(depot, "US0378331005")
        pmt = apple.find(f"{{{NS_ECH0196}}}payment")
        assert pmt.get("lumpSumTaxCreditAmount") == "2.63"

    def test_rubrik_b_has_non_recoverable_percent(self, da1_statement):
        root = build_xml(da1_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        apple = _find_security_by_isin(depot, "US0378331005")
        pmt = apple.find(f"{{{NS_ECH0196}}}payment")
        assert pmt.get("nonRecoverableTaxPercent") == "15.00"

    def test_rubrik_b_has_non_recoverable_amount(self, da1_statement):
        root = build_xml(da1_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        apple = _find_security_by_isin(depot, "US0378331005")
        pmt = apple.find(f"{{{NS_ECH0196}}}payment")
        assert pmt.get("nonRecoverableTaxAmount") == "2.63"

    def test_rubrik_b_has_withholding_tax_claim(self, da1_statement):
        root = build_xml(da1_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        apple = _find_security_by_isin(depot, "US0378331005")
        pmt = apple.find(f"{{{NS_ECH0196}}}payment")
        assert pmt.get("withHoldingTaxClaim") == "5.27"

    def test_rubrik_b_gross_revenue_b(self, da1_statement):
        root = build_xml(da1_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        apple = _find_security_by_isin(depot, "US0378331005")
        pmt = apple.find(f"{{{NS_ECH0196}}}payment")
        assert pmt.get("grossRevenueB") == "17.55"
        assert pmt.get("grossRevenueA") is None

    def test_totals_include_da1_sums(self, da1_statement):
        root = build_xml(da1_statement)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        assert Decimal(los.get("totalLumpSumTaxCredit")) == Decimal("2.63")
        assert Decimal(los.get("totalNonRecoverableTax")) == Decimal("2.63")

    def test_rubrik_a_has_no_da1_attributes(self, taxpayer, institution):
        """Rubrik A payments should NOT have DA-1 attributes."""
        nestle = Security("CH0038863350", "NESTLE SA", "CHF", valor_number="3886335")
        stmt = TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
            account_number="123456",
            security_payments=[
                SecurityPayment(
                    security=nestle,
                    payment_date=date(2025, 3, 15),
                    gross_amount=Decimal("300"),
                    currency="CHF",
                    withholding_tax=Decimal("105"),
                    net_amount=Decimal("195"),
                    quantity=Decimal("70"),
                    income_category=IncomeCategory.RUBRIK_A,
                ),
            ],
        )
        root = build_xml(stmt)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        nestle_el = _find_security_by_isin(depot, "CH0038863350")
        pmt = nestle_el.find(f"{{{NS_ECH0196}}}payment")
        assert pmt.get("lumpSumTaxCredit") is None
        assert pmt.get("lumpSumTaxCreditPercent") is None
        assert pmt.get("nonRecoverableTaxPercent") is None
        assert pmt.get("grossRevenueA") == "300.00"

    def test_rubrik_b_without_da1_data(self, taxpayer, institution):
        """Rubrik B payment without DA-1 enrichment has no DA-1 attributes."""
        apple = Security("US0378331005", "APPLE INC", "USD")
        stmt = TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
            account_number="123456",
            security_payments=[
                SecurityPayment(
                    security=apple,
                    payment_date=date(2025, 5, 10),
                    gross_amount=Decimal("19.50"),
                    currency="USD",
                    withholding_tax=Decimal("5.85"),
                    net_amount=Decimal("13.65"),
                    quantity=Decimal("50"),
                ),
            ],
        )
        root = build_xml(stmt)
        los = root.find(f"{{{NS_ECH0196}}}listOfSecurities")
        depot = los.find(f"{{{NS_ECH0196}}}depot")
        apple_el = _find_security_by_isin(depot, "US0378331005")
        pmt = apple_el.find(f"{{{NS_ECH0196}}}payment")
        assert pmt.get("lumpSumTaxCredit") is None
        assert pmt.get("nonRecoverableTaxPercent") is None
        assert pmt.get("grossRevenueB") == "19.50"
