"""Tests for the enrichment orchestrator."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from src.enrichment.enricher import enrich
from src.enrichment.exchange_rates import MissingRateError
from src.enrichment.kursliste_parser import Da1Rate, KurslisteEntry, load_bundled_kursliste
from src.models import (
    CashBalance,
    Holding,
    IncomeCategory,
    Institution,
    Security,
    SecurityPayment,
    TaxPayer,
    TaxStatement,
)


@pytest.fixture
def taxpayer():
    return TaxPayer(
        first_name="Max", last_name="Muster",
        street="Str 1", zip_code="8001", city="Zurich", canton="ZH",
    )


@pytest.fixture
def institution():
    return Institution(
        name="Swissquote Bank SA",
        uid="CHE-106.787.734",
        clearing_number="08750",
    )


@pytest.fixture
def rates():
    return {
        "USD": Decimal("0.90"),
        "EUR": Decimal("0.95"),
    }


@pytest.fixture
def kursliste():
    return {
        "CH0038863350": KurslisteEntry(
            valor_number="3886335",
            isin="CH0038863350",
            security_group="SHARE",
            security_type="SHARE.NOMINAL",
            currency="CHF",
            country="CH",
            year_end_price=Decimal("74.52"),
            quotation_type="PIECE",
            tax_value_chf=Decimal("74.52"),
        ),
        "US0378331005": KurslisteEntry(
            valor_number="908440",
            isin="US0378331005",
            security_group="SHARE",
            security_type="SHARE.NOMINAL",
            currency="USD",
            country="US",
            year_end_price=Decimal("254.49"),
            quotation_type="PIECE",
            tax_value_chf=Decimal("228.97"),
        ),
    }


class TestEnrichHoldings:
    def test_chf_holding_no_exchange_rate(self, taxpayer, institution, rates, kursliste):
        sec = Security("CH0038863350", "NESTLE SA", "CHF")
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            holdings=[Holding(sec, Decimal("100"))],
        )
        enrich(stmt, rates, kursliste)
        h = stmt.holdings[0]
        assert h.exchange_rate is None  # CHF has no exchange rate
        assert h.security.valor_number == "3886335"
        assert h.security.tax_value_per_unit == Decimal("74.52")
        assert h.tax_value_chf == Decimal("100") * Decimal("74.52")

    def test_usd_holding_uses_kursliste_tax_value_chf(self, taxpayer, institution, rates, kursliste):
        """V2.2: enricher uses pre-computed taxValueCHF instead of price*fx."""
        sec = Security("US0378331005", "APPLE INC", "USD")
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            holdings=[Holding(sec, Decimal("50"))],
        )
        enrich(stmt, rates, kursliste)
        h = stmt.holdings[0]
        assert h.exchange_rate == Decimal("0.90")
        assert h.security.valor_number == "908440"
        # Uses pre-computed CHF value from kursliste (228.97), not price*fx (254.49*0.90)
        assert h.tax_value_chf == Decimal("50") * Decimal("228.97")

    def test_preserves_existing_tax_value_chf(self, taxpayer, institution, rates, kursliste):
        sec = Security("CH0038863350", "NESTLE SA", "CHF")
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            holdings=[Holding(sec, Decimal("100"), tax_value_chf=Decimal("9999"))],
        )
        enrich(stmt, rates, kursliste)
        assert stmt.holdings[0].tax_value_chf == Decimal("9999")

    def test_preserves_existing_valor_number(self, taxpayer, institution, rates, kursliste):
        sec = Security("CH0038863350", "NESTLE SA", "CHF", valor_number="EXISTING")
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            holdings=[Holding(sec, Decimal("100"))],
        )
        enrich(stmt, rates, kursliste)
        assert stmt.holdings[0].security.valor_number == "EXISTING"

    def test_no_kursliste_data_skips_enrichment(self, taxpayer, institution, rates):
        sec = Security("XX0000000000", "UNKNOWN", "USD")
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            holdings=[Holding(sec, Decimal("10"))],
        )
        enrich(stmt, rates, {})
        h = stmt.holdings[0]
        assert h.security.valor_number is None
        assert h.security.tax_value_per_unit is None
        assert h.tax_value_chf is None
        assert h.exchange_rate == Decimal("0.90")


class TestEnrichCashBalances:
    def test_chf_cash_passthrough(self, taxpayer, institution, rates):
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            cash_balances=[CashBalance("CHF", Decimal("5000"), interest_earned=Decimal("10"))],
        )
        enrich(stmt, rates)
        cb = stmt.cash_balances[0]
        assert cb.tax_value_chf == Decimal("5000")
        assert cb.interest_earned_chf == Decimal("10")
        assert cb.exchange_rate is None

    def test_usd_cash_converted(self, taxpayer, institution, rates):
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            cash_balances=[CashBalance("USD", Decimal("3000"), interest_earned=Decimal("5"))],
        )
        enrich(stmt, rates)
        cb = stmt.cash_balances[0]
        assert cb.tax_value_chf == Decimal("3000") * Decimal("0.90")
        assert cb.interest_earned_chf == Decimal("5") * Decimal("0.90")
        assert cb.exchange_rate == Decimal("0.90")

    def test_overrides_existing_tax_value_with_estv_rate(self, taxpayer, institution, rates):
        """ESTV rate always takes precedence over portfolio PDF value for non-CHF cash."""
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            cash_balances=[CashBalance("USD", Decimal("3000"), tax_value_chf=Decimal("9999"))],
        )
        enrich(stmt, rates)
        # 3000 * 0.90 = 2700.00 (ESTV rate overrides the portfolio PDF value)
        assert stmt.cash_balances[0].tax_value_chf == Decimal("2700.00")


class TestEnrichSecurityPayments:
    def test_chf_payment_converted(self, taxpayer, institution, rates):
        sec = Security("CH0038863350", "NESTLE SA", "CHF")
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            security_payments=[
                SecurityPayment(
                    sec, date(2025, 3, 15), Decimal("300"), "CHF",
                    Decimal("105"), Decimal("195"),
                ),
            ],
        )
        enrich(stmt, rates)
        p = stmt.security_payments[0]
        assert p.gross_amount_chf == Decimal("300")  # 300 * 1
        assert p.withholding_tax_chf == Decimal("105")  # 105 * 1
        assert p.exchange_rate is None  # CHF

    def test_usd_payment_converted(self, taxpayer, institution, rates):
        sec = Security("US0378331005", "APPLE INC", "USD")
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            security_payments=[
                SecurityPayment(
                    sec, date(2025, 5, 10), Decimal("19.50"), "USD",
                    Decimal("5.85"), Decimal("13.65"),
                ),
            ],
        )
        enrich(stmt, rates)
        p = stmt.security_payments[0]
        assert p.gross_amount_chf == Decimal("19.50") * Decimal("0.90")
        assert p.withholding_tax_chf == Decimal("5.85") * Decimal("0.90")
        assert p.exchange_rate == Decimal("0.90")

    def test_preserves_existing_chf_values(self, taxpayer, institution, rates):
        sec = Security("US0378331005", "APPLE INC", "USD")
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            security_payments=[
                SecurityPayment(
                    sec, date(2025, 5, 10), Decimal("19.50"), "USD",
                    Decimal("5.85"), Decimal("13.65"),
                    gross_amount_chf=Decimal("99"),
                    withholding_tax_chf=Decimal("33"),
                ),
            ],
        )
        enrich(stmt, rates)
        p = stmt.security_payments[0]
        assert p.gross_amount_chf == Decimal("99")
        assert p.withholding_tax_chf == Decimal("33")


class TestEnrichMissingCurrency:
    def test_missing_currency_raises_with_explicit_rates(self, taxpayer, institution):
        """When explicit rates dict is passed, missing currency still raises."""
        sec = Security("JP0000000001", "TOYOTA", "JPY")
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            holdings=[Holding(sec, Decimal("100"))],
        )
        # Rates dict without JPY
        with pytest.raises(MissingRateError, match="JPY"):
            enrich(stmt, rates={"USD": Decimal("0.90")})


class TestEnrichMissingYearTolerance:
    """When bundled rates don't exist for a year, enricher skips FX conversion."""

    def test_usd_holding_skipped_when_no_bundled_rates(self, taxpayer, institution):
        """USD holding gets no CHF conversion when year has no rates."""
        sec = Security("US0378331005", "APPLE INC", "USD",
                       tax_value_per_unit=Decimal("200"))
        stmt = TaxStatement(
            tax_year=2010, period_from=date(2010, 1, 1),
            period_to=date(2010, 12, 31), taxpayer=taxpayer,
            institution=institution,
            holdings=[Holding(sec, Decimal("50"))],
        )
        with patch("src.enrichment.enricher.get_rate", side_effect=MissingRateError("No rates for 2010")):
            enrich(stmt)
        h = stmt.holdings[0]
        assert h.tax_value_chf is None
        assert h.exchange_rate is None

    def test_chf_holding_still_enriched(self, taxpayer, institution):
        """CHF holdings work even when foreign rates are missing."""
        sec_chf = Security("CH0038863350", "NESTLE SA", "CHF",
                           tax_value_per_unit=Decimal("74.52"))
        sec_usd = Security("US0378331005", "APPLE INC", "USD",
                           tax_value_per_unit=Decimal("200"))
        stmt = TaxStatement(
            tax_year=2010, period_from=date(2010, 1, 1),
            period_to=date(2010, 12, 31), taxpayer=taxpayer,
            institution=institution,
            holdings=[
                Holding(sec_chf, Decimal("100")),
                Holding(sec_usd, Decimal("50")),
            ],
        )

        def _mock_get_rate(currency, year):
            if currency == "CHF":
                return Decimal("1")
            raise MissingRateError(f"No rates for {year}")

        with patch("src.enrichment.enricher.get_rate", side_effect=_mock_get_rate):
            enrich(stmt)

        # CHF holding enriched
        assert stmt.holdings[0].tax_value_chf == Decimal("100") * Decimal("74.52")
        # USD holding skipped
        assert stmt.holdings[1].tax_value_chf is None
        assert stmt.holdings[1].exchange_rate is None

    def test_cash_balance_skipped_when_no_rates(self, taxpayer, institution):
        """Non-CHF cash balance left unconverted when rates missing."""
        stmt = TaxStatement(
            tax_year=2010, period_from=date(2010, 1, 1),
            period_to=date(2010, 12, 31), taxpayer=taxpayer,
            institution=institution,
            cash_balances=[
                CashBalance("CHF", Decimal("5000"), interest_earned=Decimal("10")),
                CashBalance("USD", Decimal("3000"), interest_earned=Decimal("5")),
            ],
        )

        def _mock_get_rate(currency, year):
            if currency == "CHF":
                return Decimal("1")
            raise MissingRateError(f"No rates for {year}")

        with patch("src.enrichment.enricher.get_rate", side_effect=_mock_get_rate):
            enrich(stmt)

        # CHF cash enriched
        assert stmt.cash_balances[0].tax_value_chf == Decimal("5000")
        # USD cash skipped
        assert stmt.cash_balances[1].tax_value_chf is None
        assert stmt.cash_balances[1].exchange_rate is None

    def test_payment_skipped_when_no_rates(self, taxpayer, institution):
        """Non-CHF payment left unconverted when rates missing."""
        sec = Security("US0378331005", "APPLE INC", "USD")
        stmt = TaxStatement(
            tax_year=2010, period_from=date(2010, 1, 1),
            period_to=date(2010, 12, 31), taxpayer=taxpayer,
            institution=institution,
            security_payments=[
                SecurityPayment(
                    sec, date(2010, 5, 10), Decimal("19.50"), "USD",
                    Decimal("5.85"), Decimal("13.65"),
                ),
            ],
        )

        def _mock_get_rate(currency, year):
            if currency == "CHF":
                return Decimal("1")
            raise MissingRateError(f"No rates for {year}")

        with patch("src.enrichment.enricher.get_rate", side_effect=_mock_get_rate):
            enrich(stmt)

        p = stmt.security_payments[0]
        assert p.gross_amount_chf is None
        assert p.withholding_tax_chf is None
        assert p.exchange_rate is None


class TestEnrichWithoutKursliste:
    def test_works_without_kursliste(self, taxpayer, institution, rates):
        sec = Security("CH0038863350", "NESTLE SA", "CHF",
                       tax_value_per_unit=Decimal("80"))
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            holdings=[Holding(sec, Decimal("100"))],
        )
        # Explicitly pass empty kursliste to prevent auto-loading bundled data
        enrich(stmt, rates, kursliste={})
        assert stmt.holdings[0].tax_value_chf == Decimal("8000")

    def test_returns_same_statement(self, taxpayer, institution, rates):
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
        )
        result = enrich(stmt, rates, kursliste={})
        assert result is stmt


class TestBundledKursliste:
    def test_load_bundled_returns_none_for_missing_year(self):
        """No bundled file → returns None."""
        assert load_bundled_kursliste(1999) is None

    def test_load_bundled_from_json(self, tmp_path):
        """Bundled JSON is loaded and deserialized correctly."""
        data = {
            "CH0038863350": {
                "valor": "3886335",
                "price": "74.52",
                "quotation_type": "PIECE",
                "currency": "CHF",
                "country": "CH",
                "tax_value_chf": "74.52",
            },
            "US0378331005": {
                "valor": "908440",
                "price": None,
                "quotation_type": "PIECE",
                "currency": "USD",
                "country": "US",
                "tax_value_chf": "228.97",
            },
        }
        json_path = tmp_path / "kursliste_2099.json"
        json_path.write_text(json.dumps(data))

        with patch("src.enrichment.kursliste_parser._DATA_DIR", tmp_path):
            result = load_bundled_kursliste(2099)

        assert result is not None
        assert "CH0038863350" in result
        assert result["CH0038863350"].valor_number == "3886335"
        assert result["CH0038863350"].year_end_price == Decimal("74.52")
        assert result["CH0038863350"].tax_value_chf == Decimal("74.52")
        assert result["US0378331005"].year_end_price is None
        assert result["US0378331005"].tax_value_chf == Decimal("228.97")

    def test_enricher_auto_loads_bundled(self, taxpayer, institution, rates, tmp_path):
        """Enricher auto-loads bundled kursliste when none provided."""
        data = {
            "CH0038863350": {
                "valor": "3886335",
                "price": "74.52",
                "quotation_type": "PIECE",
                "currency": "CHF",
                "country": "CH",
            },
        }
        json_path = tmp_path / "kursliste_2025.json"
        json_path.write_text(json.dumps(data))

        sec = Security("CH0038863350", "NESTLE SA", "CHF")
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            holdings=[Holding(sec, Decimal("100"))],
        )

        with patch("src.enrichment.kursliste_parser._DATA_DIR", tmp_path):
            enrich(stmt, rates)

        h = stmt.holdings[0]
        assert h.security.valor_number == "3886335"
        assert h.security.tax_value_per_unit == Decimal("74.52")
        assert h.tax_value_chf == Decimal("100") * Decimal("74.52")


class TestEnrichDa1Rates:
    """Tests for DA-1 rate enrichment on Rubrik B security payments."""

    @pytest.fixture
    def da1_rates(self):
        return {
            ("US", "SHARE"): Da1Rate(
                country="US", security_group="SHARE", security_type="",
                da1_rate_type="OTHER", withholding_rate=Decimal("30"),
                release=Decimal("15"), non_recoverable=Decimal("15"),
                non_recoverable_max=Decimal("15"),
            ),
            ("DE", "SHARE"): Da1Rate(
                country="DE", security_group="SHARE", security_type="",
                da1_rate_type="OTHER", withholding_rate=Decimal("26.375"),
                release=Decimal("0"), non_recoverable=Decimal("26.375"),
                non_recoverable_max=Decimal("26.375"),
            ),
        }

    def test_rubrik_b_payment_gets_da1_fields(self, taxpayer, institution, rates, da1_rates):
        """US dividend (Rubrik B) gets non_recoverable and lump_sum from da1Rate."""
        sec = Security("US0378331005", "APPLE INC", "USD")
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            security_payments=[
                SecurityPayment(
                    sec, date(2025, 5, 10), Decimal("19.50"), "USD",
                    Decimal("5.85"), Decimal("13.65"),
                ),
            ],
        )
        enrich(stmt, rates, kursliste={}, da1_rates=da1_rates)
        p = stmt.security_payments[0]
        assert p.non_recoverable_tax_pct == Decimal("15")
        assert p.lump_sum_tax_credit_pct == Decimal("15")
        # Computed from gross_amount_chf (19.50 * 0.90 = 17.55)
        expected_gross_chf = Decimal("19.50") * Decimal("0.90")
        assert p.non_recoverable_tax_chf == expected_gross_chf * Decimal("15") / Decimal("100")
        assert p.lump_sum_tax_credit_chf == expected_gross_chf * Decimal("15") / Decimal("100")

    def test_rubrik_a_payment_not_enriched(self, taxpayer, institution, rates, da1_rates):
        """Swiss dividend (Rubrik A) is not enriched with DA-1 data."""
        sec = Security("CH0038863350", "NESTLE SA", "CHF")
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            security_payments=[
                SecurityPayment(
                    sec, date(2025, 3, 15), Decimal("300"), "CHF",
                    Decimal("105"), Decimal("195"),
                    income_category=IncomeCategory.RUBRIK_A,
                ),
            ],
        )
        enrich(stmt, rates, kursliste={}, da1_rates=da1_rates)
        p = stmt.security_payments[0]
        assert p.non_recoverable_tax_pct is None
        assert p.lump_sum_tax_credit_pct is None

    def test_de_share_zero_release(self, taxpayer, institution, rates, da1_rates):
        """DE dividend: release=0 means lump_sum_tax_credit=0."""
        sec = Security("DE0007236101", "SIEMENS AG", "EUR")
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            security_payments=[
                SecurityPayment(
                    sec, date(2025, 6, 1), Decimal("100"), "EUR",
                    Decimal("26.38"), Decimal("73.62"),
                ),
            ],
        )
        enrich(stmt, rates, kursliste={}, da1_rates=da1_rates)
        p = stmt.security_payments[0]
        assert p.non_recoverable_tax_pct == Decimal("26.375")
        assert p.lump_sum_tax_credit_pct == Decimal("0")
        assert p.lump_sum_tax_credit_chf == Decimal("0")

    def test_missing_country_rate_skipped(self, taxpayer, institution, rates, da1_rates):
        """Payment from country not in da1_rates is skipped."""
        sec = Security("JP0000000001", "TOYOTA", "JPY")
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            security_payments=[
                SecurityPayment(
                    sec, date(2025, 6, 1), Decimal("100"), "JPY",
                    Decimal("10"), Decimal("90"),
                ),
            ],
        )
        # JPY not in explicit rates → will raise; pass explicit rate
        rates_with_jpy = {**rates, "JPY": Decimal("0.006")}
        enrich(stmt, rates_with_jpy, kursliste={}, da1_rates=da1_rates)
        p = stmt.security_payments[0]
        assert p.non_recoverable_tax_pct is None
        assert p.lump_sum_tax_credit_pct is None

    def test_preserves_existing_da1_fields(self, taxpayer, institution, rates, da1_rates):
        """Pre-existing DA-1 fields are not overwritten."""
        sec = Security("US0378331005", "APPLE INC", "USD")
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            security_payments=[
                SecurityPayment(
                    sec, date(2025, 5, 10), Decimal("19.50"), "USD",
                    Decimal("5.85"), Decimal("13.65"),
                    non_recoverable_tax_pct=Decimal("99"),
                    non_recoverable_tax_chf=Decimal("999"),
                    lump_sum_tax_credit_pct=Decimal("88"),
                    lump_sum_tax_credit_chf=Decimal("888"),
                ),
            ],
        )
        enrich(stmt, rates, kursliste={}, da1_rates=da1_rates)
        p = stmt.security_payments[0]
        assert p.non_recoverable_tax_pct == Decimal("99")
        assert p.lump_sum_tax_credit_pct == Decimal("88")

    def test_uses_kursliste_security_group(self, taxpayer, institution, rates, da1_rates, kursliste):
        """Security group is looked up from Kursliste, not just defaulting to SHARE."""
        sec = Security("US0378331005", "APPLE INC", "USD")
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            security_payments=[
                SecurityPayment(
                    sec, date(2025, 5, 10), Decimal("19.50"), "USD",
                    Decimal("5.85"), Decimal("13.65"),
                ),
            ],
        )
        # kursliste has US0378331005 as SHARE → matches da1_rates ("US", "SHARE")
        enrich(stmt, rates, kursliste=kursliste, da1_rates=da1_rates)
        p = stmt.security_payments[0]
        assert p.non_recoverable_tax_pct == Decimal("15")

    def test_fund_falls_back_to_share_rate(self, taxpayer, institution, rates, da1_rates):
        """FUND security without a FUND rate falls back to SHARE rate.

        The ESTV DA-1 rate tables only publish SHARE and BOND rates.
        Fund distributions are treated as dividend income under tax treaties,
        so they use the same DA-1 rates as shares.
        """
        sec = Security("US4642872422", "ISHARES BIOTECH ETF", "USD")
        # Kursliste classifies this as FUND
        kursliste_with_fund = {
            "US4642872422": KurslisteEntry(
                isin="US4642872422", valor_number="1234567",
                security_group="FUND", security_type="", currency="USD",
                country="US", year_end_price=Decimal("130"),
                quotation_type="", tax_value_chf=Decimal("117"),
            ),
        }
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            security_payments=[
                SecurityPayment(
                    sec, date(2025, 6, 15), Decimal("50.00"), "USD",
                    Decimal("15.00"), Decimal("35.00"),
                ),
            ],
        )
        # da1_rates has ("US", "SHARE") but NOT ("US", "FUND")
        enrich(stmt, rates, kursliste=kursliste_with_fund, da1_rates=da1_rates)
        p = stmt.security_payments[0]
        # Should fall back to ("US", "SHARE") rate: 15% non-recoverable, 15% release
        assert p.non_recoverable_tax_pct == Decimal("15")
        assert p.lump_sum_tax_credit_pct == Decimal("15")

    def test_fund_without_any_fallback_skipped(self, taxpayer, institution, rates):
        """FUND security from a country with no SHARE rate either is skipped."""
        sec = Security("JP1234567890", "JAPAN FUND", "JPY")
        kursliste_jp = {
            "JP1234567890": KurslisteEntry(
                isin="JP1234567890", valor_number="9999999",
                security_group="FUND", security_type="", currency="JPY",
                country="JP", year_end_price=Decimal("1000"),
                quotation_type="", tax_value_chf=Decimal("6"),
            ),
        }
        da1_no_jp = {
            ("US", "SHARE"): Da1Rate(
                country="US", security_group="SHARE", security_type="",
                da1_rate_type="OTHER", withholding_rate=Decimal("30"),
                release=Decimal("15"), non_recoverable=Decimal("15"),
                non_recoverable_max=Decimal("15"),
            ),
        }
        rates_with_jpy = {**rates, "JPY": Decimal("0.006")}
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            security_payments=[
                SecurityPayment(
                    sec, date(2025, 6, 1), Decimal("100"), "JPY",
                    Decimal("10"), Decimal("90"),
                ),
            ],
        )
        enrich(stmt, rates_with_jpy, kursliste=kursliste_jp, da1_rates=da1_no_jp)
        p = stmt.security_payments[0]
        # No ("JP", "FUND") and no ("JP", "SHARE") → DA-1 skipped
        assert p.non_recoverable_tax_pct is None
        assert p.lump_sum_tax_credit_pct is None

    def test_empty_da1_rates_skips_enrichment(self, taxpayer, institution, rates):
        """Empty da1_rates dict means no DA-1 enrichment."""
        sec = Security("US0378331005", "APPLE INC", "USD")
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            security_payments=[
                SecurityPayment(
                    sec, date(2025, 5, 10), Decimal("19.50"), "USD",
                    Decimal("5.85"), Decimal("13.65"),
                ),
            ],
        )
        enrich(stmt, rates, kursliste={}, da1_rates={})
        p = stmt.security_payments[0]
        assert p.non_recoverable_tax_pct is None
