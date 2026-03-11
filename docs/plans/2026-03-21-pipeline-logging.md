# Pipeline Logging Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add structured, PII-free logging across the full pipeline with session-based file rotation, enabling debugging from log files alone — including locale/asset class issues in unsupported languages.

**Architecture:** Single `src/logging_config.py` module configures Python's standard logging with a file handler writing to `~/Documents/OpenSwissTaxStatement/app.log`. Session rotation trims to last 3 sessions on startup. Log calls added at each pipeline step emit only metadata (counts, types, flags) — never PII.

**Tech Stack:** Python stdlib `logging` only. No new dependencies.

**Design doc:** `docs/plans/2026-03-21-pipeline-logging-design.md`

---

### Task 1: Create logging_config.py — Session Rotation

**Files:**
- Create: `src/logging_config.py`
- Test: `tests/test_logging.py`

**Step 1: Write the failing tests**

```python
"""Tests for logging configuration and session rotation."""

from __future__ import annotations

import logging
import os
from unittest.mock import patch

import pytest


class TestSessionRotation:
    """Test that log file retains only last 3 sessions."""

    def test_first_session_creates_file(self, tmp_path):
        """First session creates log file with session header."""
        from src.logging_config import setup_logging

        log_path = str(tmp_path / "app.log")
        with patch("src.logging_config._get_log_path", return_value=log_path):
            setup_logging(debug=False)

        assert os.path.exists(log_path)
        content = open(log_path).read()
        assert "SESSION START" in content

        # Clean up handlers
        logging.getLogger().handlers.clear()

    def test_retains_last_three_sessions(self, tmp_path):
        """After 4 sessions, only sessions 2-4 remain."""
        from src.logging_config import SESSION_MARKER, setup_logging

        log_path = str(tmp_path / "app.log")

        # Write 3 existing sessions manually
        with open(log_path, "w") as f:
            for i in range(1, 4):
                f.write(f"{SESSION_MARKER}\n")
                f.write(f"SESSION {i} log line\n")

        # Start a 4th session
        with patch("src.logging_config._get_log_path", return_value=log_path):
            setup_logging(debug=False)

        content = open(log_path).read()
        assert "SESSION 1" not in content
        assert "SESSION 2" in content
        assert "SESSION 3" in content
        assert "SESSION START" in content  # 4th session header

        logging.getLogger().handlers.clear()

    def test_fewer_than_three_sessions_kept_intact(self, tmp_path):
        """With only 2 existing sessions, nothing is trimmed."""
        from src.logging_config import SESSION_MARKER, setup_logging

        log_path = str(tmp_path / "app.log")

        with open(log_path, "w") as f:
            for i in range(1, 3):
                f.write(f"{SESSION_MARKER}\n")
                f.write(f"SESSION {i} log line\n")

        with patch("src.logging_config._get_log_path", return_value=log_path):
            setup_logging(debug=False)

        content = open(log_path).read()
        assert "SESSION 1" in content
        assert "SESSION 2" in content

        logging.getLogger().handlers.clear()


class TestLogLevels:
    """Test INFO vs DEBUG log level configuration."""

    def test_default_level_is_info(self, tmp_path):
        """Without debug flag, root logger is INFO."""
        from src.logging_config import setup_logging

        log_path = str(tmp_path / "app.log")
        with patch("src.logging_config._get_log_path", return_value=log_path):
            setup_logging(debug=False)

        logger = logging.getLogger("src.test")
        logger.debug("debug message")
        logger.info("info message")

        # Flush
        for h in logging.getLogger().handlers:
            h.flush()

        content = open(log_path).read()
        assert "debug message" not in content
        assert "info message" in content

        logging.getLogger().handlers.clear()

    def test_debug_flag_enables_debug(self, tmp_path):
        """With debug=True, DEBUG messages are written."""
        from src.logging_config import setup_logging

        log_path = str(tmp_path / "app.log")
        with patch("src.logging_config._get_log_path", return_value=log_path):
            setup_logging(debug=True)

        logger = logging.getLogger("src.test")
        logger.debug("debug message")

        for h in logging.getLogger().handlers:
            h.flush()

        content = open(log_path).read()
        assert "debug message" in content

        logging.getLogger().handlers.clear()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_logging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.logging_config'`

**Step 3: Write the implementation**

```python
"""Logging configuration for Open Swiss Tax Statement.

Configures structured logging to ~/Documents/OpenSwissTaxStatement/app.log
with session-based rotation (retains last 3 sessions).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from src import __version__
from src.ui.paths import get_app_support_dir

SESSION_MARKER = "=" * 80

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_MAX_SESSIONS = 3


def _get_log_path() -> str:
    """Return the log file path."""
    return os.path.join(get_app_support_dir(), "app.log")


def _trim_sessions(log_path: str) -> None:
    """Trim log file to retain only the last (_MAX_SESSIONS - 1) sessions.

    Called before writing the new session header, so after trimming +
    new header there will be _MAX_SESSIONS total.
    """
    if not os.path.exists(log_path):
        return

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # Split on session markers
    parts = content.split(SESSION_MARKER)
    # First part is content before any marker (usually empty)
    # Each subsequent part is a session (marker was consumed by split)

    # Count sessions: parts[1:] each represent a session
    sessions = parts[1:]  # skip pre-marker content
    keep = _MAX_SESSIONS - 1  # keep N-1 old sessions + 1 new one about to start

    if len(sessions) <= keep:
        return  # Nothing to trim

    # Keep only the last `keep` sessions
    kept = sessions[-keep:]
    trimmed = SESSION_MARKER + SESSION_MARKER.join(kept)

    with open(log_path, "w", encoding="utf-8") as f:
        f.write(trimmed)


def setup_logging(debug: bool = False) -> None:
    """Configure logging with file handler and optional stderr.

    Args:
        debug: If True, set log level to DEBUG and add stderr handler.
               If False, set log level to INFO, file only.
    """
    log_path = _get_log_path()
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    # Trim old sessions before starting new one
    _trim_sessions(log_path)

    level = logging.DEBUG if debug else logging.INFO
    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # File handler (append mode)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    # Configure root logger
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)

    # Console handler only in debug mode
    if debug:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    # Write session header directly to file
    now = datetime.now().strftime(_DATE_FORMAT)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n{SESSION_MARKER}\n")
        f.write(f"SESSION START — {now} — v{__version__}\n")
        f.write(f"{SESSION_MARKER}\n")
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_logging.py -v`
Expected: PASS (all 5 tests)

**Step 5: Commit**

```bash
git add src/logging_config.py tests/test_logging.py
git commit -m "feat: add logging config with session-based rotation"
```

---

### Task 2: Wire up logging in main.py + paths.py

**Files:**
- Modify: `src/main.py`
- Modify: `src/ui/paths.py` (add `get_log_path`)

**Step 1: Update main.py to call setup_logging**

In `src/main.py`, add after `ensure_dirs()`:

```python
from src.logging_config import setup_logging

# In main(), after ensure_dirs():
setup_logging(debug=args.debug)
```

The full `main()` function becomes:

```python
def main():
    import webview

    from src.logging_config import setup_logging
    from src.ui.api import Api
    from src.ui.paths import ensure_dirs, get_statements_dir

    parser = argparse.ArgumentParser(
        description="Open Swiss Tax Statement - E-Steuerauszug Generator",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable pywebview dev tools",
    )
    args = parser.parse_args()

    # Create directories and configure logging
    ensure_dirs()
    setup_logging(debug=args.debug)

    statements_dir = get_statements_dir()
    project_root = _get_project_root()
    api = Api(statements_dir)
    html_path = "file://" + os.path.join(project_root, "src", "ui", "index.html")

    webview.create_window(
        "Open Swiss Tax Statement",
        url=html_path,
        js_api=api,
        width=1024,
        height=768,
    )
    webview.start(debug=args.debug)
```

**Step 2: Run existing tests to verify nothing breaks**

Run: `pytest tests/test_paths.py tests/test_ui.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add src/main.py
git commit -m "feat: wire up logging in main.py"
```

---

### Task 3: Add logging to CSV parser

**Files:**
- Modify: `src/parsers/swissquote/csv_parser.py`
- Test: `tests/test_logging.py` (append)

**Step 1: Write the failing test**

Append to `tests/test_logging.py`:

```python
class TestCsvParserLogging:
    """Verify CSV parser emits expected log messages."""

    @pytest.fixture
    def fixture_csv(self):
        path = os.path.join(
            os.path.dirname(__file__), "fixtures", "swissquote_transactions.csv"
        )
        if not os.path.exists(path):
            pytest.skip("Test fixture not available")
        with open(path, encoding="latin-1") as f:
            return f.read()

    @pytest.fixture
    def taxpayer(self):
        from src.models import TaxPayer
        return TaxPayer(
            first_name="Max", last_name="Muster",
            street="Str. 1", zip_code="8000", city="Zurich", canton="ZH",
        )

    def test_parse_logs_summary(self, fixture_csv, taxpayer, caplog):
        """CSV parser logs locale, row count, and transaction summary."""
        from src.parsers.swissquote import SwissquoteCsvParser

        with caplog.at_level(logging.INFO, logger="src.parsers.swissquote.csv_parser"):
            parser = SwissquoteCsvParser()
            parser.parse(fixture_csv, 2025, taxpayer, portfolio_content=None, account_number="123456")

        log_text = caplog.text
        # Should log detected locale
        assert "locale" in log_text.lower() or "Locale" in log_text
        # Should log transaction counts
        assert "transaction" in log_text.lower()

    def test_parse_logs_no_pii(self, fixture_csv, taxpayer, caplog):
        """CSV parser logs must not contain taxpayer PII."""
        from src.parsers.swissquote import SwissquoteCsvParser

        with caplog.at_level(logging.DEBUG, logger="src.parsers.swissquote.csv_parser"):
            parser = SwissquoteCsvParser()
            parser.parse(fixture_csv, 2025, taxpayer, portfolio_content=None, account_number="123456")

        log_text = caplog.text
        assert "Max" not in log_text
        assert "Muster" not in log_text
        assert "123456" not in log_text
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_logging.py::TestCsvParserLogging -v`
Expected: FAIL — assertions about locale/transaction not found in logs

**Step 3: Add logging to csv_parser.py**

Add these log calls at the specified points in `src/parsers/swissquote/csv_parser.py`:

After locale detection (after `type_map = _detect_locale(lines[0])`, ~line 238):
```python
logger.info("CSV locale detected: %s, columns: %d",
            "DE" if type_map is GERMAN_TRANSACTION_TYPES else "EN",
            len(col_map))
```

After filtering to tax year (~line 245):
```python
logger.info("Parsed %d total rows, %d in tax year %d, %d skipped/storno",
            len(transactions), len(year_transactions), tax_year,
            len(transactions) - len(year_transactions))
```

Log transaction type breakdown (after year filtering):
```python
from collections import Counter
type_counts = Counter(t.type.value for t in year_transactions)
logger.info("Transaction types: %s", dict(type_counts))
```

Before returning the TaxStatement (end of parse(), ~line 287):
```python
logger.info("Parse result: %d holdings, %d cash balances, %d payments, %d expenses",
            len(holdings), len(cash_balances), len(payments), len(expenses))
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_logging.py::TestCsvParserLogging -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/parsers/swissquote/csv_parser.py tests/test_logging.py
git commit -m "feat: add logging to CSV parser"
```

---

### Task 4: Add logging to portfolio parser (with validation status)

**Files:**
- Modify: `src/parsers/swissquote/portfolio_parser.py`
- Modify: `src/parsers/swissquote/constants.py` (add validation metadata)
- Test: `tests/test_logging.py` (append)

**Step 1: Add validation status metadata to constants.py**

The constants already have `# confirmed` / `# unverified` comments, but these aren't machine-readable. Add a companion dict in `constants.py` after `PORTFOLIO_LABELS`:

```python
# Validation status of portfolio section type mappings.
# "confirmed" = verified against a real Swissquote export.
# "unverified" = added from documentation, not yet seen in a real file.
PORTFOLIO_SECTION_VALIDATION: dict[str, dict[str, str]] = {
    "de": {
        "Aktien": "confirmed",
        "Anlagefonds": "confirmed",
        "Obligationen": "confirmed",
        "Strukturierte Produkte": "unverified",
        "ETF": "confirmed",
    },
    "en": {
        "Shares": "confirmed",
        "Investment funds": "unverified",
        "Bonds": "unverified",
        "Structured products": "unverified",
        "ETF": "confirmed",
    },
}
```

**Step 2: Write the failing test**

Append to `tests/test_logging.py`:

```python
class TestPortfolioParserLogging:
    """Verify portfolio parser logs sections, client info flags, and validation status."""

    @pytest.fixture
    def portfolio_text(self):
        path = os.path.join(
            os.path.dirname(__file__), "fixtures", "swissquote_portfolio.txt"
        )
        if not os.path.exists(path):
            pytest.skip("Portfolio fixture not available")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_parse_logs_sections_and_year(self, portfolio_text, caplog):
        """Portfolio parser logs detected sections and year."""
        from src.parsers.swissquote.portfolio_parser import PortfolioParser

        with caplog.at_level(logging.INFO, logger="src.parsers.swissquote.portfolio_parser"):
            parser = PortfolioParser()
            parser.parse(portfolio_text)

        log_text = caplog.text
        assert "holdings" in log_text.lower() or "Holdings" in log_text

    def test_client_info_logs_flags(self, portfolio_text, caplog):
        """Client info extraction logs success flags without PII."""
        from src.parsers.swissquote.portfolio_parser import PortfolioParser

        with caplog.at_level(logging.INFO, logger="src.parsers.swissquote.portfolio_parser"):
            PortfolioParser.extract_client_info(portfolio_text)

        log_text = caplog.text
        # Should log extraction flags
        assert "name" in log_text.lower()
        # Must NOT contain actual extracted values
        # (fixture has "Max Muster" — ensure it's not in log)
```

**Step 3: Add logging to portfolio_parser.py**

After locale detection in `parse()` (~line 243):
```python
logger.info("Portfolio locale: %s", locale)
```

When a section header matches (inside the section_match block, ~line 270):
```python
from src.parsers.swissquote.constants import PORTFOLIO_SECTION_VALIDATION
section_name = section_match.group(1)
validation = PORTFOLIO_SECTION_VALIDATION.get(locale, {}).get(section_name, "unknown")
logger.info("Portfolio section: '%s in %s' (validation: %s)",
            section_name, section_match.group(2), validation)
```

When a line doesn't match any known section but looks like a section header (unrecognized):
```python
# After the section_match check, add an else branch for lines matching "X in CUR" pattern:
elif re.match(r"^.+\s+in\s+[A-Z]{3}$", line):
    logger.warning("Unrecognized portfolio section header: '%s'", line)
```

Before returning from `parse()` (~line 299):
```python
logger.info("Portfolio result: %d holdings, %d cash balances, year=%s",
            len(holdings), len(cash_balances), year)
```

In `extract_client_info()`, before the return (~line 225):
```python
logger.info("Client info extraction: name=%s, address=%s, account=%s",
            "Y" if info.get("first_name") else "N",
            "Y" if info.get("street") else "N",
            "Y" if info.get("account_number") else "N")
```

**Step 4: Run tests**

Run: `pytest tests/test_logging.py::TestPortfolioParserLogging -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/parsers/swissquote/portfolio_parser.py src/parsers/swissquote/constants.py tests/test_logging.py
git commit -m "feat: add logging to portfolio parser with validation status"
```

---

### Task 5: Add logging to kontoauszug parser

**Files:**
- Modify: `src/parsers/swissquote/kontoauszug_parser.py`

**Step 1: Add log calls**

After locale detection (~line 310):
```python
logger.info("Kontoauszug locale: %s", locale)
```

After currency section split (~line 343):
```python
logger.info("Kontoauszug currency sections: %d", len(sections))
```

When an unknown transaction type is encountered (existing warning ~line 755, enhance):
```python
logger.warning("Unrecognized Kontoauszug transaction type: '%s'", type_keyword)
```

Before returning from `parse()` (~line 434):
```python
logger.info("Kontoauszug result: %d holdings, %d cash balances, %d payments, %d expenses",
            len(holdings), len(cash_balances), len(payments), len(expenses))
```

**Step 2: Run existing kontoauszug tests**

Run: `pytest tests/test_kontoauszug_parser.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add src/parsers/swissquote/kontoauszug_parser.py
git commit -m "feat: add logging to kontoauszug parser"
```

---

### Task 6: Add logging to enrichment pipeline

**Files:**
- Modify: `src/enrichment/enricher.py`

**Step 1: Add log calls**

At the start of `enrich()`:
```python
log.info("Enrichment start: %d holdings, %d payments, %d cash balances, year=%d",
         len(statement.holdings), len(statement.security_payments),
         len(statement.cash_balances), statement.tax_year)
```

After kursliste loading:
```python
if kursliste:
    log.info("Kursliste loaded: %d entries (source: %s)", len(kursliste), source)
else:
    log.warning("Kursliste not available for year %d", statement.tax_year)
```

After enriching holdings (log match rate):
```python
matched = sum(1 for h in statement.holdings if h.valor_number)
log.info("Kursliste match: %d/%d securities", matched, len(statement.holdings))
unmatched = [h.security.isin for h in statement.holdings if not h.valor_number]
if unmatched:
    log.info("Unmatched ISINs: %s", unmatched)
```

After DA-1 enrichment:
```python
da1_count = sum(1 for p in statement.security_payments if p.da1_rate is not None)
log.info("DA-1 rates applied: %d/%d payments", da1_count, len(statement.security_payments))
```

**Step 2: Run enricher tests**

Run: `pytest tests/test_enricher.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add src/enrichment/enricher.py
git commit -m "feat: add logging to enrichment pipeline"
```

---

### Task 7: Add logging to XML builder + barcode + PDF

**Files:**
- Modify: `src/generators/ech0196/xml_builder.py`
- Modify: `src/generators/barcode/orchestrator.py`
- Modify: `src/generators/pdf/assembler.py`

**Step 1: Add logger and log calls to xml_builder.py**

At top of file:
```python
import logging
logger = logging.getLogger(__name__)
```

In `build_xml()` before return (~line 60):
```python
rubrik_a = sum(1 for p in statement.security_payments
               if _determine_income_category(p) == IncomeCategory.RUBRIK_A)
rubrik_b = len(statement.security_payments) - rubrik_a
logger.info("XML built: %d holdings, %d payments (A:%d B:%d), %d expenses, %d cash",
            len(statement.holdings), len(statement.security_payments),
            rubrik_a, rubrik_b, len(statement.expenses), len(statement.cash_balances))
```

**Step 2: Add log calls to orchestrator.py**

At top of file (logger likely exists, verify):
```python
import logging
logger = logging.getLogger(__name__)
```

In `generate_data_barcodes()`:

After compression (~line 45):
```python
logger.info("XML compressed: %d -> %d bytes (%.0f%%)",
            len(xml_string), len(compressed),
            len(compressed) / len(xml_string) * 100)
```

Before return (~line 81):
```python
logger.info("Barcodes: %d segments, %d sheets", len(images), num_sheets)
```

**Step 3: Add log calls to assembler.py**

At top of file:
```python
import logging
logger = logging.getLogger(__name__)
```

In `generate_pdf()` before final return:
```python
page_count = len(reader.pages)
pdf_size = len(buf.getvalue())
logger.info("PDF generated: %d pages, %.1f KB", page_count, pdf_size / 1024)
```

**Step 4: Run generator tests**

Run: `pytest tests/test_xml_builder.py tests/test_barcode.py tests/test_pdf.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/generators/ech0196/xml_builder.py src/generators/barcode/orchestrator.py src/generators/pdf/assembler.py
git commit -m "feat: add logging to XML builder, barcode, and PDF generators"
```

---

### Task 8: Add logging to UI layer (api.py + pipeline.py)

**Files:**
- Modify: `src/ui/api.py`
- Modify: `src/ui/pipeline.py`

**Step 1: Add logger and log calls to api.py**

At top of file:
```python
import logging
logger = logging.getLogger(__name__)
```

In `select_transaction_file()` after file selection (~line 79):
```python
if result and result[0]:
    file_path = result[0]
    file_size = os.path.getsize(file_path)
    file_ext = os.path.splitext(file_path)[1].lower()
    logger.info("Transaction file selected: %s, %.1f KB",
                file_ext, file_size / 1024)
```

In `select_portfolio_file()` after file selection (~line 143):
```python
if result and result[0]:
    file_path = result[0]
    file_size = os.path.getsize(file_path)
    logger.info("Portfolio file selected: %.1f KB", file_size / 1024)
```

In `preview()` — log broker and form vs file year:
```python
provider = data.get("provider", "swissquote")
tax_year = int(data["tax_year"])
logger.info("Preview requested: broker=%s, tax_year=%d", provider, tax_year)
```

In `preview()` and `generate()` error handlers (~lines 212-217, 280-285):
```python
except FileNotFoundError as e:
    logger.error("File not found: %s", e)
    return {"success": False, "errors": [str(e)]}
except ValueError as e:
    logger.error("Validation error: %s", e)
    return {"success": False, "errors": [str(e)]}
except RuntimeError as e:
    logger.error("Pipeline error: %s", e, exc_info=True)
    return {"success": False, "errors": [str(e)]}
```

In `generate()` — log form validation flags:
```python
logger.info("Generate requested: broker=%s, tax_year=%d", provider, tax_year)
logger.info("Form fields: personal=%s, address=%s, account=%s, canton=%s",
            "Y" if data.get("first_name") and data.get("last_name") else "N",
            "Y" if data.get("street") and data.get("zip_code") and data.get("city") else "N",
            "Y" if data.get("account_number") else "N",
            "Y" if data.get("canton") else "N")
```

**Step 2: Add log calls to pipeline.py**

Add logger at top:
```python
logger = logging.getLogger(__name__)
```

In `_parse_and_enrich()` after parser selection (~line 90):
```python
logger.info("Parser: %s (%s)", provider, input_type)
```

In `_parse_and_enrich()` — log year comparison (~line 103):
```python
if statement.portfolio_year is not None:
    logger.info("Year check: portfolio=%d, form=%d", statement.portfolio_year, tax_year)
```

In `run_pipeline()` — log timing:
```python
import time
start = time.monotonic()
# ... (existing pipeline code) ...
elapsed = time.monotonic() - start
logger.info("Pipeline completed in %.2fs", elapsed)
```

**Step 3: Run UI tests**

Run: `pytest tests/test_ui.py -v`
Expected: PASS

**Step 4: Commit**

```bash
git add src/ui/api.py src/ui/pipeline.py
git commit -m "feat: add logging to UI layer (api + pipeline)"
```

---

### Task 9: PII absence test

**Files:**
- Test: `tests/test_logging.py` (append)

**Step 1: Write PII test**

This runs a full pipeline and asserts no PII appears in log output:

```python
class TestPiiAbsence:
    """Verify that full pipeline logging contains no PII."""

    @pytest.fixture
    def fixture_csv(self):
        path = os.path.join(
            os.path.dirname(__file__), "fixtures", "swissquote_transactions.csv"
        )
        if not os.path.exists(path):
            pytest.skip("Test fixture not available")
        with open(path, encoding="latin-1") as f:
            return f.read()

    @pytest.fixture
    def portfolio_text(self):
        path = os.path.join(
            os.path.dirname(__file__), "fixtures", "swissquote_portfolio.txt"
        )
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

        # These PII values must NOT appear anywhere in logs
        pii_values = [
            "Maximilian", "Mustermann",
            "Geheimestrasse", "756.9876.5432.10",
            "987654",
        ]
        for pii in pii_values:
            assert pii not in log_text, f"PII leaked in logs: '{pii}'"
```

**Step 2: Run test**

Run: `pytest tests/test_logging.py::TestPiiAbsence -v`
Expected: PASS (if all logging was done correctly without PII)

**Step 3: Commit**

```bash
git add tests/test_logging.py
git commit -m "test: add PII absence verification for pipeline logging"
```

---

### Task 10: Update CLAUDE.md and final verification

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Add logging section to CLAUDE.md**

Add after the "Data Persistence" section:

```markdown
### Logging

Structured pipeline logging to `~/Documents/OpenSwissTaxStatement/app.log`. Configured in `src/logging_config.py`.

- **Session rotation:** retains last 3 sessions, trimmed on startup
- **INFO level** (default): pipeline progress, counts, flags, errors
- **DEBUG level** (`--debug`): per-row parsing details, XML construction
- **PII-free by convention:** only metadata (counts, types, currencies, ISINs) — never names, addresses, account numbers, AHV. Tested in `tests/test_logging.py::TestPiiAbsence`
- **Self-service debugging:** unrecognized locale labels and asset class section headers are logged verbatim, enabling contributors to add FR/IT support from a log file alone without accessing user documents
- **Validation tracking:** portfolio section matches log their validation status (confirmed/unverified) from `constants.py` annotations
```

**Step 2: Run full test suite**

Run: `pytest -v`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add logging documentation to CLAUDE.md"
```
