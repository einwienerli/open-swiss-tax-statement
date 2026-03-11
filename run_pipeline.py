"""Run the full E-Steuerauszug pipeline on real data.

Usage: python run_pipeline.py
"""

import os
import sys
import warnings

from src.models import TaxPayer
from src.generators.pdf import generate_pdf
from src.parsers.swissquote import SwissquoteCsvParser

# ── Configuration ────────────────────────────────────────────────────────────

# Folder name: {provider}_{year}_{account}_{firstname}_{lastname}
RUN_FOLDER = "sq_2025_123456_max_muster"

BASE_DIR = os.path.join("user_data", RUN_FOLDER)
INPUT_DIR = os.path.join(BASE_DIR, "input")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

CSV_PATH = os.path.join(INPUT_DIR, "transactions-from-01012025-to-31122025.csv")
PORTFOLIO_PDF_PATH = os.path.join(INPUT_DIR, "Portfolio-Wertentwicklung_123456_20260108.pdf")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, f"e-steuerauszug_{RUN_FOLDER}.pdf")
TAX_YEAR = 2025

# TODO: Fill in your real details
TAXPAYER = TaxPayer(
    first_name="Max",
    last_name="Muster",
    street="Musterstrasse 1",
    zip_code="8000",
    city="Zürich",
    canton="ZH",
    ahv_number="756.1234.5678.90",
)

# ── Pipeline ─────────────────────────────────────────────────────────────────

def main():
    # 0. Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 1. Read CSV (Swissquote exports use latin-1 encoding)
    print(f"Reading CSV: {CSV_PATH}")
    csv_text = open(CSV_PATH, encoding="latin-1").read()

    # 2. Extract portfolio text from PDF via pypdf
    print(f"Extracting portfolio text: {PORTFOLIO_PDF_PATH}")
    from pypdf import PdfReader

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        reader = PdfReader(PORTFOLIO_PDF_PATH)
        pages = []
        for page in reader.pages:
            text = page.extract_text(extraction_mode="layout")
            if text:
                pages.append(text)
    portfolio_text = "\n".join(pages)

    # 3. Parse
    print("Parsing transactions and portfolio...")
    parser = SwissquoteCsvParser()
    statement = parser.parse(
        csv_text, TAX_YEAR, TAXPAYER, portfolio_content=portfolio_text,
    )
    print(f"  Holdings: {len(statement.holdings)}")
    print(f"  Cash balances: {len(statement.cash_balances)}")
    print(f"  Security payments: {len(statement.security_payments)}")
    print(f"  Expenses: {len(statement.expenses)}")

    # 4. Generate PDF
    print("Generating PDF...")
    pdf_bytes = generate_pdf(statement)

    # 5. Save
    with open(OUTPUT_PATH, "wb") as f:
        f.write(pdf_bytes)
    print(f"Done! PDF saved to: {OUTPUT_PATH} ({len(pdf_bytes):,} bytes)")


if __name__ == "__main__":
    main()
