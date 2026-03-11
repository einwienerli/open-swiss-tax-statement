"""Tests for the domain model dataclasses."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.models import (
    CashBalance,
    Currency,
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


class TestEnums:
    def test_transaction_types(self):
        assert TransactionType.BUY.value == "BUY"
        assert TransactionType.SELL.value == "SELL"
        assert TransactionType.DIVIDEND.value == "DIVIDEND"
        assert TransactionType.CUSTODY_FEE.value == "CUSTODY_FEE"
        assert TransactionType.TAX.value == "TAX"
        assert TransactionType.FX_CREDIT.value == "FX_CREDIT"
        assert TransactionType.FX_DEBIT.value == "FX_DEBIT"
        assert TransactionType.OTHER.value == "OTHER"

    def test_income_categories(self):
        assert IncomeCategory.RUBRIK_A.value == "A"
        assert IncomeCategory.RUBRIK_B.value == "B"

    def test_currencies(self):
        assert Currency.CHF.value == "CHF"
        assert Currency.USD.value == "USD"
        assert Currency.EUR.value == "EUR"


class TestTaxPayer:
    def test_required_fields(self):
        tp = TaxPayer(
            first_name="Max",
            last_name="Muster",
            street="Bahnhofstr. 1",
            zip_code="8001",
            city="Zurich",
            canton="ZH",
        )
        assert tp.first_name == "Max"
        assert tp.canton == "ZH"
        assert tp.ahv_number is None

    def test_with_ahv(self):
        tp = TaxPayer(
            first_name="Max",
            last_name="Muster",
            street="Bahnhofstr. 1",
            zip_code="8001",
            city="Zurich",
            canton="ZH",
            ahv_number="756.1234.5678.90",
        )
        assert tp.ahv_number == "756.1234.5678.90"


class TestInstitution:
    def test_creation(self):
        inst = Institution(
            name="Swissquote Bank SA",
            uid="CHE-106.787.734",
            clearing_number="08750",
        )
        assert inst.uid == "CHE-106.787.734"
        assert inst.clearing_number == "08750"


class TestSecurity:
    def test_minimal(self):
        sec = Security(isin="CH0038863350", name="NESTLE SA", currency="CHF")
        assert sec.valor_number is None
        assert sec.tax_value_per_unit is None
        assert sec.income_category is None

    def test_enriched(self):
        sec = Security(
            isin="CH0038863350",
            name="NESTLE SA",
            currency="CHF",
            valor_number="3886335",
            tax_value_per_unit=Decimal("95.50"),
            income_category=IncomeCategory.RUBRIK_A,
        )
        assert sec.valor_number == "3886335"
        assert sec.income_category == IncomeCategory.RUBRIK_A


class TestHolding:
    def test_creation(self):
        sec = Security(isin="CH0038863350", name="NESTLE SA", currency="CHF")
        h = Holding(security=sec, quantity=Decimal("100"))
        assert h.quantity == Decimal("100")
        assert h.tax_value_chf is None

    def test_with_tax_value(self):
        sec = Security(isin="CH0038863350", name="NESTLE SA", currency="CHF")
        h = Holding(
            security=sec,
            quantity=Decimal("100"),
            tax_value_chf=Decimal("9550.00"),
        )
        assert h.tax_value_chf == Decimal("9550.00")


class TestTransaction:
    def test_buy(self):
        sec = Security(isin="CH0038863350", name="NESTLE SA", currency="CHF")
        tx = Transaction(
            date=date(2024, 1, 15),
            type=TransactionType.BUY,
            security=sec,
            quantity=Decimal("100"),
            unit_price=Decimal("95.50"),
            costs=Decimal("9.85"),
            net_amount=Decimal("-9559.85"),
            currency="CHF",
            order_number="ORD001",
        )
        assert tx.type == TransactionType.BUY
        assert tx.net_amount == Decimal("-9559.85")

    def test_defaults(self):
        tx = Transaction(date=date(2024, 1, 1), type=TransactionType.OTHER)
        assert tx.security is None
        assert tx.costs == Decimal("0")
        assert tx.currency == "CHF"


class TestSecurityPayment:
    def test_new_fields_default_none(self):
        sec = Security(isin="US0378331005", name="APPLE", currency="USD")
        p = SecurityPayment(
            security=sec, payment_date=date(2025, 5, 10),
            gross_amount=Decimal("19.50"), currency="USD",
            withholding_tax=Decimal("5.85"), net_amount=Decimal("13.65"),
        )
        assert p.gross_amount_chf is None
        assert p.withholding_tax_chf is None
        assert p.exchange_rate is None

    def test_with_chf_fields(self):
        sec = Security(isin="US0378331005", name="APPLE", currency="USD")
        p = SecurityPayment(
            security=sec, payment_date=date(2025, 5, 10),
            gross_amount=Decimal("19.50"), currency="USD",
            withholding_tax=Decimal("5.85"), net_amount=Decimal("13.65"),
            gross_amount_chf=Decimal("15.45"),
            withholding_tax_chf=Decimal("4.63"),
            exchange_rate=Decimal("0.792250"),
        )
        assert p.gross_amount_chf == Decimal("15.45")
        assert p.exchange_rate == Decimal("0.792250")


class TestHoldingNewFields:
    def test_exchange_rate_defaults_none(self):
        sec = Security(isin="US0378331005", name="APPLE", currency="USD")
        h = Holding(security=sec, quantity=Decimal("50"))
        assert h.exchange_rate is None

    def test_with_exchange_rate(self):
        sec = Security(isin="US0378331005", name="APPLE", currency="USD")
        h = Holding(security=sec, quantity=Decimal("50"),
                    exchange_rate=Decimal("0.90"))
        assert h.exchange_rate == Decimal("0.90")


class TestCashBalance:
    def test_creation(self):
        cb = CashBalance(currency="CHF", balance=Decimal("5000.00"))
        assert cb.interest_earned == Decimal("0")
        assert cb.tax_value_chf is None

    def test_new_fields_default_none(self):
        cb = CashBalance(currency="USD", balance=Decimal("3000"))
        assert cb.interest_earned_chf is None
        assert cb.exchange_rate is None

    def test_with_enriched_fields(self):
        cb = CashBalance(
            currency="USD", balance=Decimal("3000"),
            tax_value_chf=Decimal("2376.75"),
            interest_earned=Decimal("5"),
            interest_earned_chf=Decimal("3.96"),
            exchange_rate=Decimal("0.792250"),
        )
        assert cb.interest_earned_chf == Decimal("3.96")
        assert cb.exchange_rate == Decimal("0.792250")


class TestTaxStatement:
    def test_empty_lists_default(self):
        tp = TaxPayer(
            first_name="Max", last_name="Muster",
            street="Str 1", zip_code="8001", city="Zurich", canton="ZH",
        )
        inst = Institution(name="Test", uid="CHE-000", clearing_number="00000")
        stmt = TaxStatement(
            tax_year=2024,
            period_from=date(2024, 1, 1),
            period_to=date(2024, 12, 31),
            taxpayer=tp,
            institution=inst,
        )
        assert stmt.holdings == []
        assert stmt.cash_balances == []
        assert stmt.security_payments == []
        assert stmt.transactions == []
        assert stmt.expenses == []
        assert stmt.total_tax_value is None

    def test_no_shared_list_mutation(self):
        """Verify list defaults are independent instances."""
        tp = TaxPayer(
            first_name="Max", last_name="Muster",
            street="Str 1", zip_code="8001", city="Zurich", canton="ZH",
        )
        inst = Institution(name="Test", uid="CHE-000", clearing_number="00000")
        stmt1 = TaxStatement(
            tax_year=2024, period_from=date(2024, 1, 1),
            period_to=date(2024, 12, 31), taxpayer=tp, institution=inst,
        )
        stmt2 = TaxStatement(
            tax_year=2024, period_from=date(2024, 1, 1),
            period_to=date(2024, 12, 31), taxpayer=tp, institution=inst,
        )
        stmt1.holdings.append(
            Holding(
                security=Security(isin="X", name="X", currency="CHF"),
                quantity=Decimal("1"),
            )
        )
        assert len(stmt2.holdings) == 0
