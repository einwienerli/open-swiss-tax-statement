"""Tests for DIN 1333 rounding utility."""

from __future__ import annotations

from decimal import Decimal

from src.enrichment.rounding import round_to_005


class TestRoundTo005:
    def test_exact_value_unchanged(self):
        assert round_to_005(Decimal("10.00")) == Decimal("10.00")

    def test_rounds_down(self):
        assert round_to_005(Decimal("10.02")) == Decimal("10.00")

    def test_rounds_up(self):
        assert round_to_005(Decimal("10.03")) == Decimal("10.05")

    def test_midpoint_rounds_up(self):
        """DIN 1333: 0.025 rounds up to 0.05."""
        assert round_to_005(Decimal("10.025")) == Decimal("10.05")

    def test_rounds_to_nearest_five(self):
        assert round_to_005(Decimal("10.07")) == Decimal("10.05")
        assert round_to_005(Decimal("10.08")) == Decimal("10.10")

    def test_zero(self):
        assert round_to_005(Decimal("0")) == Decimal("0")

    def test_negative_value(self):
        assert round_to_005(Decimal("-10.03")) == Decimal("-10.05")

    def test_large_value(self):
        assert round_to_005(Decimal("123456.78")) == Decimal("123456.80")

    def test_already_on_five(self):
        assert round_to_005(Decimal("99.95")) == Decimal("99.95")

    def test_small_fractional(self):
        assert round_to_005(Decimal("0.01")) == Decimal("0.00")
        assert round_to_005(Decimal("0.03")) == Decimal("0.05")
