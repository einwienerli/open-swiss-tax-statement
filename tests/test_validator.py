"""Tests for eCH-0196 XSD validator."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from src.generators.ech0196 import build_xml, serialize, validate_xml
from src.generators.ech0196.validator import ValidationResult
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
from src.parsers.swissquote.csv_parser import SwissquoteCsvParser

FIXTURES_DIR = Path(__file__).parent / "fixtures"


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


class TestValidateXml:
    def test_empty_statement_validates(self, taxpayer, institution):
        stmt = TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
        )
        root = build_xml(stmt)
        result = validate_xml(root)
        assert result.is_valid
        assert result.errors == []

    def test_accepts_element_tree(self, taxpayer, institution):
        stmt = TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
        )
        root = build_xml(stmt)
        result = validate_xml(root)
        assert result.is_valid

    def test_accepts_string(self, taxpayer, institution):
        stmt = TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
        )
        xml = serialize(build_xml(stmt))
        result = validate_xml(xml)
        assert result.is_valid

    def test_accepts_bytes(self, taxpayer, institution):
        stmt = TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
        )
        xml_bytes = serialize(build_xml(stmt)).encode("utf-8")
        result = validate_xml(xml_bytes)
        assert result.is_valid

    def test_invalid_xml_fails(self):
        invalid_xml = '<taxStatement xmlns="http://www.ech.ch/xmlns/eCH-0196/2"/>'
        result = validate_xml(invalid_xml)
        assert not result.is_valid
        assert len(result.errors) > 0

    def test_validation_result_dataclass(self):
        r = ValidationResult(is_valid=True, errors=[])
        assert r.is_valid
        assert r.errors == []


class TestCsvToXmlValidation:
    """The critical integration test: CSV -> parse -> build -> validate -> passes."""

    def test_german_csv_validates(self, taxpayer):
        csv = (FIXTURES_DIR / "swissquote_transactions.csv").read_text(encoding="utf-8")
        parser = SwissquoteCsvParser()
        stmt = parser.parse(csv, 2025, taxpayer)
        root = build_xml(stmt)
        xml = serialize(root)
        result = validate_xml(xml)
        assert result.is_valid, f"Validation errors: {result.errors}"


class TestRichStatementValidation:
    """Validates a manually constructed rich statement against XSD."""

    def test_full_statement_validates(self, taxpayer, institution):
        nestle = Security("CH0038863350", "NESTLE SA", "CHF", valor_number="3886335")
        apple = Security("US0378331005", "APPLE INC", "USD")
        vanguard = Security("IE00B3XXRP09", "VANGUARD FTSE ALL-WORLD UCITS ETF", "EUR")

        stmt = TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
            holdings=[
                Holding(nestle, Decimal("70"), Decimal("7000")),
                Holding(apple, Decimal("50"), Decimal("8000")),
                Holding(vanguard, Decimal("200"), Decimal("17060")),
            ],
            cash_balances=[
                CashBalance("CHF", Decimal("5000"), Decimal("5000"), Decimal("12.50")),
                CashBalance("USD", Decimal("3000"), Decimal("2700"), Decimal("0.85")),
            ],
            security_payments=[
                SecurityPayment(
                    nestle, date(2025, 3, 15), Decimal("300"), "CHF",
                    Decimal("105"), Decimal("195"),
                    quantity=Decimal("70"),
                    income_category=IncomeCategory.RUBRIK_A,
                ),
                SecurityPayment(
                    apple, date(2025, 5, 10), Decimal("19.50"), "USD",
                    Decimal("5.85"), Decimal("13.65"),
                    quantity=Decimal("50"),
                ),
            ],
            transactions=[
                Transaction(
                    date=date(2025, 1, 15), type=TransactionType.BUY,
                    security=nestle, quantity=Decimal("100"),
                    unit_price=Decimal("95.50"), currency="CHF",
                ),
                Transaction(
                    date=date(2025, 6, 1), type=TransactionType.SELL,
                    security=nestle, quantity=Decimal("30"),
                    unit_price=Decimal("98.20"), currency="CHF",
                ),
            ],
            expenses=[
                Transaction(
                    date=date(2025, 9, 15), type=TransactionType.CUSTODY_FEE,
                    net_amount=Decimal("-45"), currency="CHF",
                ),
            ],
        )
        root = build_xml(stmt)
        xml = serialize(root)
        result = validate_xml(xml)
        assert result.is_valid, f"Validation errors: {result.errors}"


class TestEnrichedStatementValidation:
    """Validates an enriched multi-currency statement with exchangeRate attributes."""

    def test_enriched_multicurrency_validates(self, taxpayer, institution):
        nestle = Security("CH0038863350", "NESTLE SA", "CHF",
                          valor_number="3886335", tax_value_per_unit=Decimal("74.52"))
        apple = Security("US0378331005", "APPLE INC", "USD",
                         valor_number="908440", tax_value_per_unit=Decimal("254.49"))

        stmt = TaxStatement(
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
                            Decimal("5"), Decimal("3.96"),
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
                    gross_amount_chf=Decimal("15.45"),
                    withholding_tax_chf=Decimal("4.63"),
                    exchange_rate=Decimal("0.792250"),
                ),
            ],
            transactions=[
                Transaction(
                    date=date(2025, 1, 15), type=TransactionType.BUY,
                    security=nestle, quantity=Decimal("100"),
                    unit_price=Decimal("95.50"), currency="CHF",
                ),
            ],
            expenses=[
                Transaction(
                    date=date(2025, 9, 15), type=TransactionType.CUSTODY_FEE,
                    net_amount=Decimal("-45"), currency="CHF",
                ),
            ],
        )
        root = build_xml(stmt)
        xml = serialize(root)
        result = validate_xml(xml)
        assert result.is_valid, f"Validation errors: {result.errors}"


class TestPaymentOnlyValidation:
    """Validates that payment-only securities pass XSD validation."""

    def test_payment_only_validates(self, taxpayer, institution):
        """Security with payment but no holding should produce valid XML."""
        zurich = Security("CH0011075394", "ZURICH INSURANCE GROUP AG", "CHF")
        stmt = TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
            security_payments=[
                SecurityPayment(
                    zurich, date(2025, 6, 20), Decimal("123.50"), "CHF",
                    Decimal("43.23"), Decimal("80.27"),
                    quantity=Decimal("10"),
                ),
            ],
        )
        root = build_xml(stmt)
        xml = serialize(root)
        result = validate_xml(xml)
        assert result.is_valid, f"Validation errors: {result.errors}"

    def test_mixed_holdings_and_payment_only_validates(self, taxpayer, institution):
        """Mixed: some securities with holdings, some payment-only."""
        nestle = Security("CH0038863350", "NESTLE SA", "CHF")
        zurich = Security("CH0011075394", "ZURICH INSURANCE GROUP AG", "CHF")
        stmt = TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
            holdings=[
                Holding(nestle, Decimal("70"), Decimal("7000")),
            ],
            security_payments=[
                SecurityPayment(
                    nestle, date(2025, 3, 15), Decimal("300"), "CHF",
                    Decimal("105"), Decimal("195"),
                    quantity=Decimal("70"),
                    income_category=IncomeCategory.RUBRIK_A,
                ),
                SecurityPayment(
                    zurich, date(2025, 6, 20), Decimal("123.50"), "CHF",
                    Decimal("43.23"), Decimal("80.27"),
                    quantity=Decimal("10"),
                ),
            ],
        )
        root = build_xml(stmt)
        xml = serialize(root)
        result = validate_xml(xml)
        assert result.is_valid, f"Validation errors: {result.errors}"
