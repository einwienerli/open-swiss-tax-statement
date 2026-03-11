"""Tests for the admin CLI: prepare_year."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.admin.prepare_year import main, parse_rates, update_rates_file


class TestParseRates:
    def test_single_rate(self):
        result = parse_rates("USD=0.906250")
        assert result == {"USD": "0.906250"}

    def test_multiple_rates(self):
        result = parse_rates("USD=0.906250,EUR=0.938450,GBP=1.135038")
        assert result == {
            "USD": "0.906250",
            "EUR": "0.938450",
            "GBP": "1.135038",
        }

    def test_whitespace_handling(self):
        result = parse_rates("  USD = 0.906  , EUR = 0.938  ")
        assert result == {"USD": "0.906", "EUR": "0.938"}

    def test_uppercases_keys(self):
        result = parse_rates("usd=0.90")
        assert result == {"USD": "0.90"}

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="Invalid rate pair"):
            parse_rates("USD0.90")

    def test_invalid_decimal_raises(self):
        with pytest.raises(ValueError, match="Invalid decimal"):
            parse_rates("USD=abc")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="No rates parsed"):
            parse_rates("")


class TestUpdateRatesFile:
    def test_creates_new_file(self, tmp_path):
        path = tmp_path / "estv_rates.json"
        with patch("src.admin.prepare_year._RATES_FILE", path):
            update_rates_file(2024, {"USD": "0.906250", "EUR": "0.938450"})

        data = json.loads(path.read_text())
        assert "2024" in data
        assert data["2024"]["USD"] == "0.906250"

    def test_merges_with_existing(self, tmp_path):
        path = tmp_path / "estv_rates.json"
        path.write_text(json.dumps({"2025": {"USD": "0.792250"}}))

        with patch("src.admin.prepare_year._RATES_FILE", path):
            update_rates_file(2024, {"USD": "0.906250"})

        data = json.loads(path.read_text())
        assert "2024" in data
        assert "2025" in data  # preserved


class TestMainCli:
    def test_rates_only(self, tmp_path):
        rates_file = tmp_path / "estv_rates.json"
        with patch("src.admin.prepare_year._RATES_FILE", rates_file):
            main(["--year", "2024", "--rates", "USD=0.906,EUR=0.938"])

        data = json.loads(rates_file.read_text())
        assert data["2024"]["USD"] == "0.906"

    def test_no_args_raises(self):
        with pytest.raises(SystemExit):
            main(["--year", "2024"])

    def test_kursliste_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="not found"):
            main(["--year", "2024", "--kursliste", str(tmp_path / "nonexistent.xml")])
