"""Tests for ESTV exchange rate module."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.enrichment.exchange_rates import (
    MissingRateError,
    get_rate,
    has_rates,
    load_rates,
)


class TestLoadRates:
    def test_loads_2025_rates(self):
        rates = load_rates(2025)
        assert isinstance(rates, dict)
        assert "USD" in rates
        assert "EUR" in rates

    def test_rates_are_decimals(self):
        rates = load_rates(2025)
        for cur, rate in rates.items():
            assert isinstance(rate, Decimal), f"{cur} rate is not Decimal"

    def test_missing_year_raises(self):
        with pytest.raises(MissingRateError, match="year 1999"):
            load_rates(1999)

    def test_known_rates_correct(self):
        rates = load_rates(2025)
        assert rates["USD"] == Decimal("0.792250")
        assert rates["EUR"] == Decimal("0.930500")
        assert rates["GBP"] == Decimal("1.065681")

    def test_jpy_normalized(self):
        """JPY denomination=100 should be normalized to per-1-unit."""
        rates = load_rates(2025)
        assert rates["JPY"] == Decimal("0.005054")


class TestHasRates:
    def test_bundled_year_returns_true(self):
        assert has_rates(2025) is True

    def test_missing_year_returns_false(self):
        assert has_rates(1999) is False


class TestGetRate:
    def test_chf_returns_one(self):
        assert get_rate("CHF", 2025) == Decimal("1")

    def test_chf_no_data_needed(self):
        """CHF should return 1 even for a year with no data file."""
        assert get_rate("CHF", 1900) == Decimal("1")

    def test_usd_rate(self):
        assert get_rate("USD", 2025) == Decimal("0.792250")

    def test_missing_currency_raises(self):
        with pytest.raises(MissingRateError, match="XYZ"):
            get_rate("XYZ", 2025)

    def test_missing_year_raises(self):
        with pytest.raises(MissingRateError, match="year 1999"):
            get_rate("USD", 1999)
