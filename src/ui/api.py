"""pywebview JS API bridge — replaces Flask routes."""

from __future__ import annotations

import io
import logging
import os
import shutil
import warnings
import zipfile
from datetime import date

from src.models import TaxPayer
from src.parsers.swissquote.portfolio_parser import PortfolioParser
from src.ui.folders import (
    build_folder_name,
    create_run_folders,
    list_statements,
)
from src.ui.pipeline import parse_statement, run_pipeline
from src.ui.profile import load_profile, save_profile

logger = logging.getLogger(__name__)

# All 26 Swiss cantons
CANTONS = [
    "AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GL", "GR",
    "JU", "LU", "NE", "NW", "OW", "SG", "SH", "SO", "SZ", "TG",
    "TI", "UR", "VD", "VS", "ZG", "ZH",
]


class Api:
    """Exposed to JS via pywebview.api.<method>()."""

    def __init__(self, user_data_dir: str):
        self.user_data_dir = user_data_dir

    # ── Page init ────────────────────────────────────────────────────────────

    def get_init_data(self) -> dict:
        """Return profile, cantons, statements, default_year, releases_url."""
        from dataclasses import asdict

        from src import APP_AUTHOR, APP_LICENSE, __version__

        profile = load_profile()
        statements = list_statements(self.user_data_dir)
        default_year = date.today().year - 1
        return {
            "profile": asdict(profile),
            "cantons": CANTONS,
            "statements": statements,
            "default_year": default_year,
            "version": __version__,
            "author": APP_AUTHOR,
            "license": APP_LICENSE,
            "has_saved_profile": self.has_saved_profile(),
        }

    def has_saved_profile(self) -> bool:
        """Check if a saved profile exists on disk."""
        from src.ui.profile import PROFILE_PATH

        return os.path.isfile(PROFILE_PATH)

    def get_profile(self) -> dict:
        """Load and return saved profile as dict."""
        from dataclasses import asdict

        profile = load_profile()
        return asdict(profile)

    # ── File selection (native dialogs) ──────────────────────────────────────

    def select_transaction_file(self) -> dict | None:
        """Open OS file picker for transaction file (CSV or Kontoauszug PDF).

        Returns {path, filename, info: {...}} or None.
        For Kontoauszug PDFs, also extracts client info for auto-population.
        """
        import webview
        window = webview.windows[0]
        result = window.create_file_dialog(
            webview.FileDialog.OPEN,
            file_types=(
                "Transaction Files (*.csv;*.pdf)",
                "CSV Files (*.csv)",
                "PDF Files (*.pdf)",
            ),
        )
        if not result or len(result) == 0:
            return None

        path = result[0]
        response = {"path": path, "filename": os.path.basename(path)}

        # For Kontoauszug PDFs, extract client info
        if path.lower().endswith(".pdf"):
            info = self._extract_kontoauszug_info(path)
            if info:
                response["info"] = info

        return response

    def _extract_kontoauszug_info(self, pdf_path: str) -> dict:
        """Extract client info from a Kontoauszug PDF."""
        from pypdf import PdfReader

        from src.parsers.swissquote.kontoauszug_parser import (
            SwissquoteKontoauszugParser,
        )

        pypdf_logger = logging.getLogger("pypdf")
        prev_level = pypdf_logger.level
        pypdf_logger.setLevel(logging.ERROR)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                reader = PdfReader(pdf_path)
                pages = []
                for page in reader.pages:
                    text = page.extract_text(
                        extraction_mode="layout",
                    ) or ""
                    pages.append(text)
                full_text = "\n".join(pages)

            info = SwissquoteKontoauszugParser.extract_client_info(full_text)
            year = SwissquoteKontoauszugParser.extract_year(full_text)
            if year:
                info["year"] = year
            return info
        except Exception:
            return {}
        finally:
            pypdf_logger.setLevel(prev_level)

    def select_portfolio_file(self) -> dict | None:
        """Open OS file picker for PDF. Auto-extracts client info.

        Returns {path, filename, info: {...}} or None.
        """
        import webview
        window = webview.windows[0]
        result = window.create_file_dialog(
            webview.FileDialog.OPEN,
            file_types=("PDF Files (*.pdf)",),
        )
        if not result or len(result) == 0:
            return None

        path = result[0]
        info = self._extract_client_info(path)
        return {
            "path": path,
            "filename": os.path.basename(path),
            "info": info,
        }

    def _extract_client_info(self, pdf_path: str) -> dict:
        """Extract client info from a portfolio PDF."""
        from pypdf import PdfReader
        pypdf_logger = logging.getLogger("pypdf")
        prev_level = pypdf_logger.level
        pypdf_logger.setLevel(logging.ERROR)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                reader = PdfReader(pdf_path)
                text = ""
                if reader.pages:
                    text = reader.pages[0].extract_text(
                        extraction_mode="layout",
                    ) or ""

            info = PortfolioParser.extract_client_info(text)
            year = PortfolioParser.extract_year(text)
            if year:
                info["year"] = year
            return info
        except Exception:
            return {}
        finally:
            pypdf_logger.setLevel(prev_level)

    # ── Core pipeline ────────────────────────────────────────────────────────

    def preview(self, data: dict) -> dict:
        """Validate → parse → enrich → return summary JSON."""
        errors = self._validate(data)
        if errors:
            return {"success": False, "errors": errors}

        taxpayer = self._build_taxpayer(data)
        tax_year = int(data["tax_year"])
        provider = data.get("provider", "swissquote")
        account_number = data.get("account_number", "").strip()
        input_path = data.get("input_path") or data.get("csv_path", "")
        portfolio_path = data.get("portfolio_path") or None

        logger.info("Preview requested: broker=%s, tax_year=%d", provider, tax_year)

        try:
            summary = parse_statement(
                provider=provider,
                input_path=input_path,
                portfolio_pdf_path=portfolio_path,
                tax_year=tax_year,
                taxpayer=taxpayer,
                account_number=account_number,
            )
        except FileNotFoundError as e:
            return {"success": False, "errors": [str(e)]}
        except ValueError as e:
            return {"success": False, "errors": [str(e)]}
        except RuntimeError as e:
            logger.error("Preview failed", exc_info=True)
            return {"success": False, "errors": [str(e)]}

        # Save profile if requested
        if data.get("save_profile"):
            save_profile(taxpayer, data.get("account_number", "").strip())

        return {
            "success": True,
            "summary": summary,
            "warnings": summary.pop("warnings", []),
        }

    def generate(self, data: dict) -> dict:
        """Validate → copy files → run pipeline → return result."""
        errors = self._validate(data)
        if errors:
            return {"success": False, "errors": errors}

        taxpayer = self._build_taxpayer(data)
        tax_year = int(data["tax_year"])
        provider = data.get("provider", "swissquote")
        account_number = data.get("account_number", "").strip()
        input_path = data.get("input_path") or data.get("csv_path", "")
        portfolio_path = data.get("portfolio_path") or None

        logger.info("Generate requested: broker=%s, tax_year=%d", provider, tax_year)
        logger.info(
            "Form fields: personal=%s, address=%s, account=%s, canton=%s",
            "Y" if data.get("first_name") and data.get("last_name") else "N",
            "Y" if data.get("street") and data.get("zip_code") and data.get("city") else "N",
            "Y" if data.get("account_number") else "N",
            "Y" if data.get("canton") else "N",
        )

        folder_name = build_folder_name(
            provider, tax_year, account_number,
            taxpayer.first_name, taxpayer.last_name,
        )
        input_dir, output_dir = create_run_folders(
            self.user_data_dir, folder_name,
        )

        # Copy input files to user_data/{folder}/input/
        dst_input = os.path.join(input_dir, os.path.basename(input_path))
        shutil.copy2(input_path, dst_input)

        dst_portfolio = None
        if portfolio_path:
            dst_portfolio = os.path.join(
                input_dir, os.path.basename(portfolio_path),
            )
            shutil.copy2(portfolio_path, dst_portfolio)

        # Output filename
        output_filename = f"e-steuerauszug_{folder_name}.pdf"
        output_path = os.path.join(output_dir, output_filename)

        input_filenames = [os.path.basename(dst_input)]
        if dst_portfolio:
            input_filenames.append(os.path.basename(dst_portfolio))

        try:
            pipeline_warnings = run_pipeline(
                provider=provider,
                input_path=dst_input,
                portfolio_pdf_path=dst_portfolio,
                tax_year=tax_year,
                taxpayer=taxpayer,
                output_path=output_path,
                account_number=account_number,
                input_filenames=input_filenames,
            )
        except FileNotFoundError as e:
            return {"success": False, "errors": [str(e)]}
        except ValueError as e:
            return {"success": False, "errors": [str(e)]}
        except RuntimeError as e:
            logger.error("Generate failed", exc_info=True)
            return {"success": False, "errors": [str(e)]}

        # Save profile if requested
        if data.get("save_profile"):
            save_profile(taxpayer, account_number)

        return {
            "success": True,
            "folder": folder_name,
            "filename": output_filename,
            "output_path": output_path,
            "warnings": pipeline_warnings,
        }

    def save_pdf(self, source_path: str) -> bool:
        """Open native Save dialog, copy PDF to user-chosen location."""
        import webview
        window = webview.windows[0]
        result = window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=os.path.basename(source_path),
            file_types=("PDF Files (*.pdf)",),
        )
        if result:
            dest = result if isinstance(result, str) else result[0]
            shutil.copy2(source_path, dest)
            return True
        return False

    # ── History management ───────────────────────────────────────────────────

    def list_statements(self) -> list[dict]:
        """Return list of statement metadata dicts."""
        return list_statements(self.user_data_dir)

    def delete_statement(self, folder: str) -> dict:
        """Delete a statement folder with traversal protection."""
        resolved = self._validate_path(folder)
        if resolved is None:
            return {"success": False, "error": "Invalid path"}

        if not os.path.isdir(resolved):
            return {"success": False, "error": "Folder not found"}

        shutil.rmtree(resolved)
        return {"success": True}

    def download_statement(self, folder: str, filename: str) -> bool:
        """Open native Save dialog for an existing statement PDF."""
        resolved = self._validate_path(folder)
        if resolved is None:
            return False

        source = os.path.join(resolved, "output", filename)
        if not os.path.isfile(source):
            return False

        import webview
        window = webview.windows[0]
        result = window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=filename,
            file_types=("PDF Files (*.pdf)",),
        )
        if result:
            dest = result if isinstance(result, str) else result[0]
            shutil.copy2(source, dest)
            return True
        return False

    def download_folder_zip(self, folder: str) -> bool:
        """Build in-memory ZIP, save via native dialog."""
        resolved = self._validate_path(folder)
        if resolved is None:
            return False

        if not os.path.isdir(resolved):
            return False

        # Build ZIP in memory
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for dirpath, _dirnames, filenames in os.walk(resolved):
                for fname in filenames:
                    full_path = os.path.join(dirpath, fname)
                    arcname = os.path.relpath(full_path, resolved)
                    zf.write(full_path, arcname)
        zip_bytes = buf.getvalue()

        import webview
        window = webview.windows[0]
        result = window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=f"{folder}.zip",
            file_types=("ZIP Files (*.zip)",),
        )
        if result:
            dest = result if isinstance(result, str) else result[0]
            with open(dest, "wb") as f:
                f.write(zip_bytes)
            return True
        return False

    def save_user_profile(self, data: dict) -> bool:
        """Save profile from form data."""
        try:
            taxpayer = self._build_taxpayer(data)
            save_profile(taxpayer, data.get("account_number", "").strip())
            return True
        except Exception:
            return False

    # ── i18n ────────────────────────────────────────────────────────────────

    def get_translations(self, lang: str) -> dict:
        """Load i18n translations for the given language."""
        import json

        supported = ("en", "de", "fr", "it")
        if lang not in supported:
            lang = "en"
        i18n_dir = os.path.join(os.path.dirname(__file__), "static", "i18n")
        path = os.path.join(i18n_dir, f"{lang}.json")
        if not os.path.isfile(path):
            return {}
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    # ── Kursliste management ────────────────────────────────────────────────

    def check_kursliste(self, year: int) -> dict:
        """Check if kursliste data is available for the given year.

        Returns {available: bool, source: "user"|"bundled"|"missing"}.
        """
        from src.enrichment.kursliste_parser import check_kursliste_availability
        return check_kursliste_availability(year)

    def select_kursliste_file(self) -> str | None:
        """Open native file dialog for XML file selection.

        Returns the selected file path, or None if cancelled.
        """
        import webview
        window = webview.windows[0]
        result = window.create_file_dialog(
            webview.FileDialog.OPEN,
            file_types=("XML Files (*.xml)",),
        )
        if not result or len(result) == 0:
            return None
        return result[0]

    def process_kursliste(self, year: int, xml_path: str) -> dict:
        """Parse a Kursliste XML and save JSON to user data directory.

        This may take 2-3 minutes for large files. pywebview runs API
        methods in a thread pool, so this won't freeze the UI.

        Returns {success: bool, count: int} or {success: False, error: str}.
        """
        from pathlib import Path

        from src.admin.prepare_year import extract_kursliste
        from src.ui.paths import get_data_dir

        try:
            count = extract_kursliste(year, xml_path, Path(get_data_dir()))
            return {"success": True, "count": count}
        except FileNotFoundError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Private helpers ──────────────────────────────────────────────────────

    def _validate(self, data: dict) -> list[str]:
        """Validate form data. Returns list of error messages."""
        errors = []

        # File paths required (accept both input_path and legacy csv_path)
        if not (data.get("input_path") or data.get("csv_path")):
            errors.append("Transaction file is required.")

        provider = data.get("provider", "swissquote")
        if provider == "swissquote":
            if not data.get("portfolio_path"):
                errors.append("Portfolio PDF is required for Swissquote.")

        # Required text fields
        for field, label in [
            ("first_name", "First name"),
            ("last_name", "Last name"),
            ("street", "Street"),
            ("zip_code", "ZIP code"),
            ("city", "City"),
            ("canton", "Canton"),
        ]:
            if not data.get(field, "").strip():
                errors.append(f"{label} is required.")

        # Tax year
        year_str = data.get("tax_year", "").strip()
        if not year_str:
            errors.append("Tax year is required.")
        else:
            try:
                year = int(year_str)
                if not 2000 <= year <= 2099:
                    errors.append("Tax year must be between 2000 and 2099.")
            except ValueError:
                errors.append("Tax year must be a number.")

        # Canton format
        canton = data.get("canton", "").strip()
        if canton and canton not in CANTONS:
            errors.append(
                f"Invalid canton: {canton}. "
                f"Must be one of: {', '.join(CANTONS)}."
            )

        # Account number required
        if not data.get("account_number", "").strip():
            errors.append("Account number is required.")

        return errors

    def _build_taxpayer(self, data: dict) -> TaxPayer:
        """Build TaxPayer from form dict."""
        ahv = data.get("ahv_number", "").strip() or None
        return TaxPayer(
            first_name=data["first_name"].strip(),
            last_name=data["last_name"].strip(),
            street=data["street"].strip(),
            zip_code=data["zip_code"].strip(),
            city=data["city"].strip(),
            canton=data["canton"].strip(),
            ahv_number=ahv,
        )

    def _validate_path(self, folder: str) -> str | None:
        """Traversal protection. Returns resolved path or None."""
        if ".." in folder or "/" in folder:
            return None
        folder_path = os.path.join(self.user_data_dir, folder)
        resolved = os.path.realpath(folder_path)
        if not resolved.startswith(os.path.realpath(self.user_data_dir)):
            return None
        return resolved
