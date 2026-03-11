"""Tests for logging configuration with session rotation."""
from __future__ import annotations

import logging
import os
from unittest.mock import patch

import pytest


class TestSessionRotation:
    """Tests for session-based log rotation."""

    def teardown_method(self) -> None:
        logging.getLogger().handlers.clear()

    def test_first_session_creates_file(self, tmp_path: object) -> None:
        log_file = str(tmp_path / "app.log")
        with patch("src.logging_config._get_log_path", return_value=log_file):
            from src.logging_config import setup_logging

            setup_logging()

        content = open(log_file).read()
        assert "SESSION START" in content
        assert "=" * 80 in content

    def test_retains_last_three_sessions(self, tmp_path: object) -> None:
        from src.logging_config import SESSION_MARKER

        log_file = str(tmp_path / "app.log")

        # Write 3 existing sessions
        parts = []
        for i in range(1, 4):
            parts.append(
                f"\n{SESSION_MARKER}\n"
                f"SESSION START — session {i}\n"
                f"{SESSION_MARKER}\n"
                f"Log entry from session {i}\n"
            )
        with open(log_file, "w") as f:
            f.write("".join(parts))

        with patch("src.logging_config._get_log_path", return_value=log_file):
            from src.logging_config import setup_logging

            setup_logging()

        content = open(log_file).read()
        # Session 1 should be trimmed
        assert "session 1" not in content
        # Sessions 2 and 3 should remain
        assert "session 2" in content
        assert "session 3" in content
        # New session header should be present
        assert content.count("SESSION START") == 3

    def test_fewer_than_three_sessions_kept_intact(self, tmp_path: object) -> None:
        from src.logging_config import SESSION_MARKER

        log_file = str(tmp_path / "app.log")

        # Write 2 existing sessions
        parts = []
        for i in range(1, 3):
            parts.append(
                f"\n{SESSION_MARKER}\n"
                f"SESSION START — session {i}\n"
                f"{SESSION_MARKER}\n"
                f"Log entry from session {i}\n"
            )
        with open(log_file, "w") as f:
            f.write("".join(parts))

        with patch("src.logging_config._get_log_path", return_value=log_file):
            from src.logging_config import setup_logging

            setup_logging()

        content = open(log_file).read()
        # Both original sessions should remain
        assert "session 1" in content
        assert "session 2" in content
        # Plus the new session header
        assert content.count("SESSION START") == 3


class TestCsvParserLogging:
    """Tests for structured logging in the Swissquote CSV parser."""

    @pytest.fixture
    def fixture_csv(self):
        path = os.path.join(os.path.dirname(__file__), "fixtures", "swissquote_transactions.csv")
        if not os.path.exists(path):
            pytest.skip("Test fixture not available")
        with open(path, encoding="latin-1") as f:
            return f.read()

    @pytest.fixture
    def taxpayer(self):
        from src.models import TaxPayer
        return TaxPayer(first_name="Max", last_name="Muster", street="Str. 1", zip_code="8000", city="Zurich", canton="ZH")

    def test_parse_logs_summary(self, fixture_csv, taxpayer, caplog):
        from src.parsers.swissquote import SwissquoteCsvParser
        with caplog.at_level(logging.INFO, logger="src.parsers.swissquote.csv_parser"):
            SwissquoteCsvParser().parse(fixture_csv, 2025, taxpayer, portfolio_content=None, account_number="123456")
        log_text = caplog.text
        assert "locale" in log_text.lower() or "Locale" in log_text
        assert "transaction" in log_text.lower()

    def test_parse_logs_no_pii(self, fixture_csv, taxpayer, caplog):
        from src.parsers.swissquote import SwissquoteCsvParser
        with caplog.at_level(logging.DEBUG, logger="src.parsers.swissquote.csv_parser"):
            SwissquoteCsvParser().parse(fixture_csv, 2025, taxpayer, portfolio_content=None, account_number="123456")
        log_text = caplog.text
        assert "Max" not in log_text
        assert "Muster" not in log_text
        assert "123456" not in log_text


class TestLogLevels:
    """Tests for log level configuration."""

    def teardown_method(self) -> None:
        logging.getLogger().handlers.clear()

    def test_default_level_is_info(self, tmp_path: object) -> None:
        log_file = str(tmp_path / "app.log")
        with patch("src.logging_config._get_log_path", return_value=log_file):
            from src.logging_config import setup_logging

            setup_logging(debug=False)

        logger = logging.getLogger("test.info_level")
        logger.debug("debug_message_hidden")
        logger.info("info_message_visible")

        # Flush handlers
        for handler in logging.getLogger().handlers:
            handler.flush()

        content = open(log_file).read()
        assert "debug_message_hidden" not in content
        assert "info_message_visible" in content

    def test_debug_flag_enables_debug(self, tmp_path: object) -> None:
        log_file = str(tmp_path / "app.log")
        with patch("src.logging_config._get_log_path", return_value=log_file):
            from src.logging_config import setup_logging

            setup_logging(debug=True)

        logger = logging.getLogger("test.debug_level")
        logger.debug("debug_message_visible")

        for handler in logging.getLogger().handlers:
            handler.flush()

        content = open(log_file).read()
        assert "debug_message_visible" in content


class TestPortfolioParserLogging:
    @pytest.fixture
    def portfolio_text(self):
        path = os.path.join(os.path.dirname(__file__), "fixtures", "swissquote_portfolio.txt")
        if not os.path.exists(path):
            pytest.skip("Portfolio fixture not available")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_parse_logs_sections_and_year(self, portfolio_text, caplog):
        from src.parsers.swissquote.portfolio_parser import PortfolioParser
        with caplog.at_level(logging.INFO, logger="src.parsers.swissquote.portfolio_parser"):
            PortfolioParser().parse(portfolio_text)
        log_text = caplog.text
        assert "Portfolio locale" in log_text
        assert "Portfolio result" in log_text

    def test_client_info_logs_flags(self, portfolio_text, caplog):
        from src.parsers.swissquote.portfolio_parser import PortfolioParser
        with caplog.at_level(logging.INFO, logger="src.parsers.swissquote.portfolio_parser"):
            PortfolioParser.extract_client_info(portfolio_text)
        log_text = caplog.text
        assert "Client info extraction" in log_text
        assert "name=" in log_text

    def test_section_validation_status_logged(self, portfolio_text, caplog):
        from src.parsers.swissquote.portfolio_parser import PortfolioParser
        with caplog.at_level(logging.INFO, logger="src.parsers.swissquote.portfolio_parser"):
            PortfolioParser().parse(portfolio_text)
        log_text = caplog.text
        assert "validation:" in log_text


class TestPiiAbsence:
    """Verify that full pipeline logging contains no PII."""

    @pytest.fixture
    def fixture_csv(self):
        path = os.path.join(os.path.dirname(__file__), "fixtures", "swissquote_transactions.csv")
        if not os.path.exists(path):
            pytest.skip("Test fixture not available")
        with open(path, encoding="latin-1") as f:
            return f.read()

    @pytest.fixture
    def portfolio_text(self):
        path = os.path.join(os.path.dirname(__file__), "fixtures", "swissquote_portfolio.txt")
        if not os.path.exists(path):
            pytest.skip("Portfolio fixture not available")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_full_pipeline_logs_no_pii(self, fixture_csv, portfolio_text, caplog, tmp_path):
        """Run full pipeline and verify no PII in any log message."""
        from unittest.mock import patch as mock_patch

        from src.models import TaxPayer
        from src.ui.pipeline import run_pipeline

        taxpayer = TaxPayer(
            first_name="Maximilian", last_name="Mustermann",
            street="Geheimestrasse 42", zip_code="8000",
            city="Zürich", canton="ZH",
            ahv_number="756.9876.5432.10",
        )

        csv_path = str(tmp_path / "transactions.csv")
        with open(csv_path, "w", encoding="latin-1") as f:
            f.write(fixture_csv)

        output_path = str(tmp_path / "output.pdf")

        with caplog.at_level(logging.DEBUG), \
                mock_patch("src.ui.pipeline._extract_portfolio_text", return_value=portfolio_text):
            run_pipeline(
                "swissquote", csv_path, "/fake/portfolio.pdf",
                2025, taxpayer, output_path,
                account_number="987654",
            )

        log_text = caplog.text

        pii_values = [
            "Maximilian", "Mustermann",
            "Geheimestrasse", "756.9876.5432.10",
            "987654",
        ]
        for pii in pii_values:
            assert pii not in log_text, f"PII leaked in logs: '{pii}'"
