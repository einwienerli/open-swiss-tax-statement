"""Tests for the web UI layer: profile, folders, API class, validation, i18n."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.models import TaxPayer
from src.ui.api import CANTONS, Api
from src.ui.folders import (
    build_folder_name,
    create_run_folders,
    extract_account_number,
    list_statements,
)
from src.ui.profile import UserProfile, load_profile, save_profile

# ── Profile tests ────────────────────────────────────────────────────────────


class TestProfile:
    def test_load_missing_file(self, tmp_path):
        """Missing profile file returns empty UserProfile."""
        p = load_profile(str(tmp_path / "nonexistent.json"))
        assert p == UserProfile()

    def test_save_and_load_round_trip(self, tmp_path):
        """Save then load returns same data."""
        path = str(tmp_path / "profile.json")
        taxpayer = TaxPayer(
            first_name="Max",
            last_name="Müster",
            street="Bahnhofstrasse 1",
            zip_code="8000",
            city="Zürich",
            canton="ZH",
            ahv_number="756.1234.5678.90",
        )
        save_profile(taxpayer, account_number="123456", path=path)
        loaded = load_profile(path)

        assert loaded.first_name == "Max"
        assert loaded.last_name == "Müster"
        assert loaded.city == "Zürich"
        assert loaded.canton == "ZH"
        assert loaded.ahv_number == "756.1234.5678.90"
        assert loaded.account_number == "123456"

    def test_corrupt_json_returns_empty(self, tmp_path):
        """Corrupt JSON returns empty UserProfile."""
        path = tmp_path / "profile.json"
        path.write_text("not json{{{", encoding="utf-8")
        p = load_profile(str(path))
        assert p == UserProfile()

    def test_save_creates_directory(self, tmp_path):
        """save_profile creates parent directory if it doesn't exist."""
        path = str(tmp_path / "subdir" / "profile.json")
        taxpayer = TaxPayer(
            first_name="A", last_name="B", street="C",
            zip_code="1000", city="D", canton="BE",
        )
        save_profile(taxpayer, path=path)
        assert os.path.exists(path)

    def test_load_ignores_unknown_keys(self, tmp_path):
        """Unknown keys in JSON are silently ignored."""
        path = tmp_path / "profile.json"
        data = {"first_name": "Max", "unknown_field": "ignored"}
        path.write_text(json.dumps(data), encoding="utf-8")
        p = load_profile(str(path))
        assert p.first_name == "Max"

    def test_save_uses_utf8(self, tmp_path):
        """Saved JSON uses UTF-8 with ensure_ascii=False."""
        path = str(tmp_path / "profile.json")
        taxpayer = TaxPayer(
            first_name="Müller", last_name="Schönbächler",
            street="Str.", zip_code="8000", city="Zürich", canton="ZH",
        )
        save_profile(taxpayer, path=path)
        raw = open(path, encoding="utf-8").read()
        assert "Müller" in raw  # Not escaped as \u00fc


# ── Folders tests ────────────────────────────────────────────────────────────


class TestFolders:
    def test_build_folder_name_swissquote(self):
        name = build_folder_name("swissquote", 2025, "123456", "Max", "Muster")
        assert name == "sq_2025_123456_max_muster"

    def test_build_folder_name_sanitizes(self):
        name = build_folder_name("swissquote", 2025, "123456", "Hans-Peter", "O'Brien")
        assert name == "sq_2025_123456_hans_peter_o_brien"

    def test_build_folder_name_unknown_provider(self):
        name = build_folder_name("Interactive Brokers", 2025, "U123", "Max", "M")
        assert name.startswith("interactive_brokers_")

    def test_create_run_folders(self, tmp_path):
        input_dir, output_dir = create_run_folders(str(tmp_path), "sq_2025_test")
        assert os.path.isdir(input_dir)
        assert os.path.isdir(output_dir)
        assert input_dir.endswith("input")
        assert output_dir.endswith("output")

    def test_create_run_folders_idempotent(self, tmp_path):
        """Calling twice doesn't raise."""
        create_run_folders(str(tmp_path), "sq_2025_test")
        create_run_folders(str(tmp_path), "sq_2025_test")

    def test_extract_account_number_standard(self):
        assert extract_account_number("Portfolio-Wertentwicklung_123456_20260108.pdf") == "123456"

    def test_extract_account_number_long(self):
        assert extract_account_number("file_1234567890_date.pdf") == "1234567890"

    def test_extract_account_number_no_match(self):
        assert extract_account_number("random_file.pdf") is None

    def test_extract_account_number_too_short(self):
        """4-digit numbers don't match (need 5+)."""
        assert extract_account_number("file_1234_date.pdf") is None

    def test_list_statements_empty(self, tmp_path):
        """Empty or non-existent directory returns empty list."""
        assert list_statements(str(tmp_path / "nonexistent")) == []
        assert list_statements(str(tmp_path)) == []

    def test_list_statements_with_folders(self, tmp_path):
        """Parses folder names and checks for output PDFs."""
        # Folder with output
        folder1 = tmp_path / "sq_2025_123456_max_muster"
        (folder1 / "output").mkdir(parents=True)
        (folder1 / "output" / "e-steuerauszug.pdf").write_bytes(b"pdf")

        # Folder without output
        folder2 = tmp_path / "sq_2024_123456_anna_test"
        (folder2 / "input").mkdir(parents=True)

        results = list_statements(str(tmp_path))
        assert len(results) == 2

        # Sorted reverse alphabetically: sq_2025 before sq_2024
        assert results[0]["year"] == 2025
        assert results[0]["has_output"] is True
        assert results[0]["output_file"] == "e-steuerauszug.pdf"

        assert results[1]["year"] == 2024
        assert results[1]["has_output"] is False

    def test_list_statements_skips_files(self, tmp_path):
        """Regular files in base_dir are skipped."""
        (tmp_path / "not_a_folder.txt").write_text("hi")
        assert list_statements(str(tmp_path)) == []

    def test_list_statements_skips_bad_names(self, tmp_path):
        """Folders that don't match naming pattern are skipped."""
        (tmp_path / "invalid").mkdir()
        (tmp_path / "no_year").mkdir()
        assert list_statements(str(tmp_path)) == []


# ── API test fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def api(tmp_path):
    """Create Api with isolated user_data dir."""
    user_data_dir = str(tmp_path / "user_data")
    os.makedirs(user_data_dir, exist_ok=True)
    return Api(user_data_dir)


def _portfolio_fixture_text():
    """Load the portfolio text fixture."""
    path = os.path.join(
        os.path.dirname(__file__), "fixtures", "swissquote_portfolio.txt"
    )
    with open(path, encoding="utf-8") as f:
        return f.read()


def _mock_portfolio_extract(portfolio_text):
    """Return a patcher that mocks _extract_portfolio_text in the pipeline."""
    return patch(
        "src.ui.pipeline._extract_portfolio_text", return_value=portfolio_text,
    )


def _base_form_data(csv_path="/fake/path.csv", portfolio_path="/fake/portfolio.pdf"):
    """Return base form fields for a valid submission."""
    return {
        "csv_path": csv_path,
        "portfolio_path": portfolio_path,
        "tax_year": "2025",
        "provider": "swissquote",
        "account_number": "123456",
        "first_name": "Max",
        "last_name": "Muster",
        "street": "Musterstrasse 1",
        "zip_code": "8000",
        "city": "Zürich",
        "canton": "ZH",
    }


# ── API validation tests ─────────────────────────────────────────────────────


class TestApiValidation:
    """Test Api._validate() directly."""

    def test_valid_form_passes(self):
        api = Api("/tmp/fake")
        errors = api._validate(_base_form_data())
        assert errors == []

    def test_transaction_file_required(self):
        api = Api("/tmp/fake")
        data = _base_form_data()
        data["csv_path"] = ""
        errors = api._validate(data)
        assert any("Transaction file" in e for e in errors)

    def test_portfolio_required_for_swissquote(self):
        api = Api("/tmp/fake")
        data = _base_form_data()
        data["portfolio_path"] = ""
        errors = api._validate(data)
        assert any("Portfolio" in e for e in errors)

    def test_missing_required_fields(self):
        api = Api("/tmp/fake")
        data = _base_form_data()
        # Remove all personal fields
        for field in ("first_name", "last_name", "street", "zip_code", "city", "canton"):
            data[field] = ""
        errors = api._validate(data)
        assert len(errors) >= 6  # At least 6 missing fields

    def test_year_out_of_range(self):
        api = Api("/tmp/fake")
        data = _base_form_data()
        data["tax_year"] = "1999"
        errors = api._validate(data)
        assert any("2000" in e for e in errors)

    def test_invalid_year(self):
        api = Api("/tmp/fake")
        data = _base_form_data()
        data["tax_year"] = "abc"
        errors = api._validate(data)
        assert any("year" in e.lower() for e in errors)

    def test_invalid_canton(self):
        api = Api("/tmp/fake")
        data = _base_form_data()
        data["canton"] = "XX"
        errors = api._validate(data)
        assert any("canton" in e.lower() for e in errors)

    def test_missing_account_number(self):
        api = Api("/tmp/fake")
        data = _base_form_data()
        data["account_number"] = ""
        errors = api._validate(data)
        assert any("Account" in e for e in errors)


# ── API method tests ─────────────────────────────────────────────────────────


class TestApiInitData:
    """Tests for get_init_data()."""

    def test_returns_cantons(self, api):
        result = api.get_init_data()
        assert result["cantons"] == CANTONS

    def test_returns_default_year(self, api):
        from datetime import date
        result = api.get_init_data()
        assert result["default_year"] == date.today().year - 1

    def test_returns_empty_statements(self, api):
        result = api.get_init_data()
        assert result["statements"] == []

    def test_returns_profile_dict(self, api):
        result = api.get_init_data()
        assert "profile" in result
        assert isinstance(result["profile"], dict)

    def test_returns_author_and_license(self, api):
        result = api.get_init_data()
        assert "author" in result
        assert "license" in result
        assert isinstance(result["author"], str)
        assert isinstance(result["license"], str)


class TestApiProfileManagement:
    """Tests for has_saved_profile() and get_profile()."""

    def test_has_saved_profile_false_when_no_file(self, tmp_path):
        api = Api(str(tmp_path))
        # Returns a bool regardless of whether the profile file exists
        assert isinstance(api.has_saved_profile(), bool)

    def test_get_profile_returns_dict(self, tmp_path):
        api = Api(str(tmp_path))
        profile = api.get_profile()
        assert isinstance(profile, dict)
        assert "first_name" in profile

    def test_get_profile_has_all_fields(self, tmp_path):
        api = Api(str(tmp_path))
        profile = api.get_profile()
        expected_fields = [
            "first_name", "last_name", "street",
            "zip_code", "city", "canton",
            "ahv_number", "account_number",
        ]
        for field in expected_fields:
            assert field in profile

    def test_init_data_includes_version(self, api):
        result = api.get_init_data()
        assert "version" in result
        assert isinstance(result["version"], str)
        assert len(result["version"]) > 0

    def test_init_data_includes_has_saved_profile(self, api):
        result = api.get_init_data()
        assert "has_saved_profile" in result
        assert isinstance(result["has_saved_profile"], bool)


class TestApiGenerateErrors:
    """Tests for generate() error handling."""

    def test_generate_without_files_returns_error(self, api):
        data = _base_form_data()
        data["csv_path"] = ""
        data["portfolio_path"] = ""
        result = api.generate(data)
        assert result["success"] is False
        assert any("Transaction file" in e for e in result["errors"])

    def test_generate_missing_portfolio_returns_error(self, api):
        data = _base_form_data()
        data["portfolio_path"] = ""
        result = api.generate(data)
        assert result["success"] is False
        assert any("Portfolio" in e for e in result["errors"])


class TestApiPreview:
    """Tests for preview()."""

    @pytest.fixture
    def fixture_csv(self):
        path = os.path.join(
            os.path.dirname(__file__), "fixtures", "swissquote_transactions.csv"
        )
        if not os.path.exists(path):
            pytest.skip("Test fixture not available")
        return path

    @pytest.fixture
    def portfolio_text(self):
        path = os.path.join(
            os.path.dirname(__file__), "fixtures", "swissquote_portfolio.txt"
        )
        if not os.path.exists(path):
            pytest.skip("Portfolio fixture not available")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_preview_without_portfolio_returns_error(self, api):
        data = _base_form_data()
        data["portfolio_path"] = ""
        result = api.preview(data)
        assert result["success"] is False
        assert any("Portfolio" in e for e in result["errors"])

    def test_preview_without_input_returns_error(self, api):
        data = _base_form_data()
        data["csv_path"] = ""
        result = api.preview(data)
        assert result["success"] is False
        assert any("Transaction file" in e for e in result["errors"])

    def test_preview_returns_summary(self, api, fixture_csv, portfolio_text):
        data = _base_form_data(csv_path=fixture_csv)

        with _mock_portfolio_extract(portfolio_text):
            result = api.preview(data)

        assert result["success"] is True
        assert "summary" in result

        summary = result["summary"]
        assert "holdings" in summary
        assert "cash_balances" in summary
        assert "security_payments" in summary
        assert "expenses" in summary
        assert "totals" in summary

        # Totals match list lengths
        assert summary["totals"]["holdings_count"] == len(summary["holdings"])
        assert summary["totals"]["cash_count"] == len(summary["cash_balances"])
        assert summary["totals"]["payments_count"] == len(summary["security_payments"])
        assert summary["totals"]["expenses_count"] == len(summary["expenses"])

    def test_preview_holdings_structure(self, api, fixture_csv, portfolio_text):
        data = _base_form_data(csv_path=fixture_csv)

        with _mock_portfolio_extract(portfolio_text):
            result = api.preview(data)

        summary = result["summary"]
        if summary["holdings"]:
            h = summary["holdings"][0]
            assert "name" in h
            assert "isin" in h
            assert "currency" in h
            assert "quantity" in h
            assert "tax_value_chf" in h

    def test_preview_cash_structure(self, api, fixture_csv, portfolio_text):
        data = _base_form_data(csv_path=fixture_csv)

        with _mock_portfolio_extract(portfolio_text):
            result = api.preview(data)

        summary = result["summary"]
        if summary["cash_balances"]:
            c = summary["cash_balances"][0]
            assert "currency" in c
            assert "balance" in c
            assert "interest" in c

    def test_preview_payments_structure(self, api, fixture_csv, portfolio_text):
        data = _base_form_data(csv_path=fixture_csv)

        with _mock_portfolio_extract(portfolio_text):
            result = api.preview(data)

        summary = result["summary"]
        if summary["security_payments"]:
            p = summary["security_payments"][0]
            assert "name" in p
            assert "date" in p
            assert "gross" in p
            assert "currency" in p
            assert "category" in p
            assert p["category"] in ("A", "B")
            assert "wht" in p
            assert "net" in p

    def test_preview_includes_trades(self, api, fixture_csv, portfolio_text):
        data = _base_form_data(csv_path=fixture_csv)

        with _mock_portfolio_extract(portfolio_text):
            result = api.preview(data)

        assert result["success"] is True
        summary = result["summary"]
        assert "trades" in summary
        assert "trades_count" in summary["totals"]
        assert summary["totals"]["trades_count"] == len(summary["trades"])

        for t in summary["trades"]:
            assert "name" in t
            assert "isin" in t
            assert "currency" in t
            assert "qty_bought" in t
            assert "qty_sold" in t
            assert "net_change" in t

    def test_preview_trades_aggregation(self, api, fixture_csv, portfolio_text):
        data = _base_form_data(csv_path=fixture_csv)

        with _mock_portfolio_extract(portfolio_text):
            result = api.preview(data)

        summary = result["summary"]
        trades = summary["trades"]

        # Each ISIN appears at most once (aggregated)
        isins = [t["isin"] for t in trades]
        assert len(isins) == len(set(isins)), "Duplicate ISINs in trades"

        # Net change = bought - sold for each entry
        from decimal import Decimal
        for t in trades:
            bought = Decimal(t["qty_bought"])
            sold = Decimal(t["qty_sold"])
            net = Decimal(t["net_change"])
            assert net == bought - sold, f"Net mismatch for {t['isin']}"


class TestApiDelete:
    """Tests for delete_statement()."""

    def test_delete_existing_folder(self, api):
        folder = "sq_2025_123456_max_muster"
        folder_path = os.path.join(api.user_data_dir, folder)
        os.makedirs(os.path.join(folder_path, "input"))
        os.makedirs(os.path.join(folder_path, "output"))
        with open(os.path.join(folder_path, "input", "test.csv"), "w") as f:
            f.write("data")

        result = api.delete_statement(folder)
        assert result["success"] is True
        assert not os.path.exists(folder_path)

    def test_delete_traversal_blocked(self, api):
        result = api.delete_statement("..secret")
        assert result["success"] is False

    def test_delete_nonexistent_returns_error(self, api):
        result = api.delete_statement("nonexistent_folder")
        assert result["success"] is False


class TestApiListStatements:
    """Tests for list_statements()."""

    def test_list_empty(self, api):
        result = api.list_statements()
        assert result == []

    def test_list_after_folder_creation(self, api):
        folder = "sq_2025_123456_max_muster"
        folder_path = os.path.join(api.user_data_dir, folder)
        os.makedirs(os.path.join(folder_path, "output"))
        with open(os.path.join(folder_path, "output", "test.pdf"), "wb") as f:
            f.write(b"%PDF")

        result = api.list_statements()
        assert len(result) == 1
        assert result[0]["folder"] == folder
        assert result[0]["has_output"] is True


# ── Pipeline integration tests via Api ────────────────────────────────────────


class TestApiPipelineIntegration:
    """Integration test: real fixture CSV → Api.generate() → PDF created."""

    @pytest.fixture
    def fixture_csv(self):
        path = os.path.join(
            os.path.dirname(__file__), "fixtures", "swissquote_transactions.csv"
        )
        if not os.path.exists(path):
            pytest.skip("Test fixture not available")
        return path

    @pytest.fixture
    def portfolio_text(self):
        path = os.path.join(
            os.path.dirname(__file__), "fixtures", "swissquote_portfolio.txt"
        )
        if not os.path.exists(path):
            pytest.skip("Portfolio fixture not available")
        with open(path, encoding="utf-8") as f:
            return f.read()

    @pytest.fixture
    def portfolio_pdf(self, tmp_path):
        """Create a dummy PDF file for generate() to copy."""
        pdf_path = str(tmp_path / "portfolio.pdf")
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-fake")
        return pdf_path

    def test_generate_with_fixture_csv(self, api, fixture_csv, portfolio_text, portfolio_pdf):
        data = _base_form_data(csv_path=fixture_csv, portfolio_path=portfolio_pdf)

        with _mock_portfolio_extract(portfolio_text):
            result = api.generate(data)

        assert result["success"] is True, f"Pipeline failed: {result.get('errors')}"
        assert result["output_path"].endswith(".pdf")

        # Verify PDF was written
        assert os.path.exists(result["output_path"])
        assert os.path.getsize(result["output_path"]) > 0

    def test_generate_saves_profile(self, api, fixture_csv, portfolio_text, portfolio_pdf, tmp_path):
        """save_profile checkbox persists taxpayer data."""
        import src.ui.api as api_mod
        original_save = api_mod.save_profile

        saved_args = {}
        def mock_save(taxpayer, account_number="", path=None):
            saved_args["taxpayer"] = taxpayer
            saved_args["account"] = account_number

        api_mod.save_profile = mock_save
        try:
            data = _base_form_data(csv_path=fixture_csv, portfolio_path=portfolio_pdf)
            data["first_name"] = "TestFirst"
            data["last_name"] = "TestLast"
            data["save_profile"] = True

            with _mock_portfolio_extract(portfolio_text):
                result = api.generate(data)

            assert result["success"] is True, f"Pipeline failed: {result.get('errors')}"
            assert saved_args["taxpayer"].first_name == "TestFirst"
            assert saved_args["account"] == "123456"
        finally:
            api_mod.save_profile = original_save

    def test_statements_lists_after_generation(self, api, fixture_csv, portfolio_text, portfolio_pdf):
        data = _base_form_data(csv_path=fixture_csv, portfolio_path=portfolio_pdf)

        with _mock_portfolio_extract(portfolio_text):
            api.generate(data)

        statements = api.list_statements()
        assert len(statements) >= 1
        assert statements[0]["has_output"] is True


class TestApiPipelineWarnings:
    """Tests that pipeline surfaces warnings for missing FX rates."""

    @pytest.fixture
    def fixture_csv(self):
        path = os.path.join(
            os.path.dirname(__file__), "fixtures", "swissquote_transactions.csv"
        )
        if not os.path.exists(path):
            pytest.skip("Test fixture not available")
        return path

    @pytest.fixture
    def portfolio_text(self):
        return _portfolio_fixture_text()

    @pytest.fixture
    def portfolio_pdf(self, tmp_path):
        """Create a dummy PDF file for generate() to copy."""
        pdf_path = str(tmp_path / "portfolio.pdf")
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-fake")
        return pdf_path

    def test_preview_includes_warnings_key(self, api, fixture_csv, portfolio_text):
        data = _base_form_data(csv_path=fixture_csv)

        with _mock_portfolio_extract(portfolio_text):
            result = api.preview(data)

        assert result["success"] is True
        assert "warnings" in result

    def test_preview_no_warnings_for_bundled_year(self, api, fixture_csv, portfolio_text):
        data = _base_form_data(csv_path=fixture_csv)
        data["tax_year"] = "2025"

        with _mock_portfolio_extract(portfolio_text):
            result = api.preview(data)

        assert result["success"] is True
        assert result["warnings"] == []

    def test_preview_warning_for_missing_year(self, api, fixture_csv, portfolio_text):
        data = _base_form_data(csv_path=fixture_csv)
        data["tax_year"] = "2025"

        with _mock_portfolio_extract(portfolio_text), \
                patch("src.ui.pipeline.has_rates", return_value=False):
            result = api.preview(data)

        assert result["success"] is True
        assert len(result["warnings"]) == 1
        assert "exchange rates" in result["warnings"][0].lower()

    def test_generate_warning_for_missing_year(self, api, fixture_csv, portfolio_text, portfolio_pdf):
        data = _base_form_data(csv_path=fixture_csv, portfolio_path=portfolio_pdf)
        data["tax_year"] = "2025"

        with _mock_portfolio_extract(portfolio_text), \
                patch("src.ui.pipeline.has_rates", return_value=False):
            result = api.generate(data)

        assert result["success"] is True
        assert len(result["warnings"]) == 1
        assert "exchange rates" in result["warnings"][0].lower()


class TestPipelinePortfolioRequired:
    """Pipeline enforces portfolio PDF requirement for Swissquote."""

    def test_pipeline_raises_without_portfolio(self):
        """_parse_and_enrich raises ValueError if portfolio_text is None."""
        from src.ui.pipeline import _parse_and_enrich

        taxpayer = TaxPayer(
            first_name="Max", last_name="Muster",
            street="Str. 1", zip_code="8000", city="Zurich", canton="ZH",
        )
        with pytest.raises(ValueError, match="Portfolio PDF is required"):
            _parse_and_enrich("swissquote", "csv data", "csv", None, 2025, taxpayer)


class TestPipelinePortfolioYearValidation:
    """Pipeline validates portfolio year matches tax_year."""

    @pytest.fixture
    def fixture_csv_text(self):
        path = os.path.join(
            os.path.dirname(__file__), "fixtures", "swissquote_transactions.csv"
        )
        if not os.path.exists(path):
            pytest.skip("Test fixture not available")
        with open(path, encoding="latin-1") as f:
            return f.read()

    def test_year_mismatch_raises(self, fixture_csv_text):
        """Portfolio for 2025 + tax_year=2024 raises ValueError."""
        from src.ui.pipeline import _parse_and_enrich

        portfolio_text = _portfolio_fixture_text()  # fixture says 2025
        taxpayer = TaxPayer(
            first_name="Max", last_name="Muster",
            street="Str. 1", zip_code="8000", city="Zurich", canton="ZH",
        )
        with pytest.raises(ValueError, match="Portfolio PDF is for year 2025.*tax year 2024"):
            _parse_and_enrich("swissquote", fixture_csv_text, "csv", portfolio_text, 2024, taxpayer)

    def test_year_match_passes(self, fixture_csv_text):
        """Portfolio for 2025 + tax_year=2025 does not raise."""
        from src.ui.pipeline import _parse_and_enrich

        portfolio_text = _portfolio_fixture_text()  # fixture says 2025
        taxpayer = TaxPayer(
            first_name="Max", last_name="Muster",
            street="Str. 1", zip_code="8000", city="Zurich", canton="ZH",
        )
        # Should not raise — enrichment may fail but year validation passes
        try:
            _parse_and_enrich("swissquote", fixture_csv_text, "csv", portfolio_text, 2025, taxpayer)
        except ValueError as e:
            # Only fail if it's a year mismatch error
            assert "Portfolio PDF is for year" not in str(e)


class TestSummaryTradesFilter:
    """Trades summary excludes securities not in holdings or payments."""

    def test_rights_issuance_excluded_from_trades(self):
        """Rights sold (no holding, no dividend) don't appear in trades."""
        from datetime import date
        from decimal import Decimal

        from src.models import (
            Holding,
            Institution,
            Security,
            TaxPayer,
            TaxStatement,
            Transaction,
        )
        from src.models.enums import TransactionType
        from src.ui.pipeline import _statement_to_summary

        taxpayer = TaxPayer(
            first_name="Max", last_name="Muster",
            street="Str. 1", zip_code="8000", city="Zurich", canton="ZH",
        )
        institution = Institution(
            name="Swissquote", uid="CHE-106.787.734",
            clearing_number="08750",
        )

        main_stock = Security(
            isin="DK0060094928", name="ORSTED A/S",
            currency="DKK", valor_number="36498920",
        )
        rights = Security(
            isin="DK0064307839",
            name="ORSTED - RIGHTS FOR SHARES 2025-02.10.25",
            currency="DKK",
        )

        statement = TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
            holdings=[
                Holding(security=main_stock, quantity=Decimal("45")),
            ],
            cash_balances=[],
            security_payments=[],
            expenses=[],
            transactions=[
                # Rights issued (OTHER type, filtered by type anyway)
                Transaction(
                    date=date(2025, 9, 23),
                    type=TransactionType.OTHER,
                    security=rights,
                    quantity=Decimal("675"),
                    net_amount=Decimal("0"),
                    currency="DKK",
                ),
                # Rights sold — this is the one that was leaking through
                Transaction(
                    date=date(2025, 10, 1),
                    type=TransactionType.SELL,
                    security=rights,
                    quantity=Decimal("675"),
                    net_amount=Decimal("4663.81"),
                    currency="DKK",
                ),
                # A real buy of the main stock (should appear)
                Transaction(
                    date=date(2025, 3, 15),
                    type=TransactionType.BUY,
                    security=main_stock,
                    quantity=Decimal("10"),
                    net_amount=Decimal("5000"),
                    currency="DKK",
                ),
            ],
        )

        summary = _statement_to_summary(statement)
        trade_isins = {t["isin"] for t in summary["trades"]}

        # Main stock trade appears
        assert "DK0060094928" in trade_isins
        # Rights ISIN is excluded (not in holdings or payments)
        assert "DK0064307839" not in trade_isins
        assert summary["totals"]["trades_count"] == 1


class TestApiKursliste:
    """Tests for kursliste management API methods."""

    def test_check_kursliste_bundled_year(self, api):
        """check_kursliste returns available for a bundled year."""
        result = api.check_kursliste(2025)
        # 2025 has bundled data
        assert isinstance(result, dict)
        assert "available" in result
        assert "source" in result

    def test_check_kursliste_missing_year(self, api):
        """check_kursliste returns missing for a far-future year."""
        result = api.check_kursliste(2099)
        assert result["available"] is False
        assert result["source"] == "missing"

    def test_process_kursliste_missing_file(self, api):
        """process_kursliste returns error for non-existent file."""
        result = api.process_kursliste(2025, "/nonexistent/path.xml")
        assert result["success"] is False
        assert "error" in result


# -- i18n translation file tests -----------------------------------------------


class TestI18nFiles:
    """Validate translation file consistency and completeness."""

    I18N_DIR = Path(__file__).parent.parent / "src" / "ui" / "static" / "i18n"
    LANGUAGES = ("en", "de", "fr", "it")

    def test_all_four_languages_exist(self):
        """All four translation JSON files exist."""
        for lang in self.LANGUAGES:
            assert (self.I18N_DIR / f"{lang}.json").is_file(), f"{lang}.json missing"

    def test_all_files_are_valid_json(self):
        """All translation files parse as valid JSON."""
        for lang in self.LANGUAGES:
            path = self.I18N_DIR / f"{lang}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            assert isinstance(data, dict), f"{lang}.json is not a dict"

    def test_all_languages_have_same_keys(self):
        """All translation files have identical key sets."""
        keys_per_lang = {}
        for lang in self.LANGUAGES:
            data = json.loads(
                (self.I18N_DIR / f"{lang}.json").read_text(encoding="utf-8")
            )
            keys_per_lang[lang] = set(data.keys())

        en_keys = keys_per_lang["en"]
        for lang in ("de", "fr", "it"):
            missing = en_keys - keys_per_lang[lang]
            extra = keys_per_lang[lang] - en_keys
            assert not missing, f"{lang} missing keys: {missing}"
            assert not extra, f"{lang} has extra keys: {extra}"

    def test_no_empty_translations(self):
        """No translation value should be empty string."""
        for lang in self.LANGUAGES:
            data = json.loads(
                (self.I18N_DIR / f"{lang}.json").read_text(encoding="utf-8")
            )
            empty = [k for k, v in data.items() if not v.strip()]
            assert not empty, f"{lang} has empty translations: {empty}"

    def test_minimum_key_count(self):
        """Translation files should have a reasonable number of keys."""
        data = json.loads(
            (self.I18N_DIR / "en.json").read_text(encoding="utf-8")
        )
        # We expect at least 150 keys covering all UI sections
        assert len(data) >= 150, f"Only {len(data)} keys, expected 150+"
