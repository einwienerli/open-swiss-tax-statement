"""Shared test fixtures for Open Swiss Tax Statement."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.models.taxpayer import TaxPayer

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_csv() -> str:
    return (FIXTURES_DIR / "swissquote_transactions.csv").read_text(encoding="utf-8")


@pytest.fixture
def sample_csv_13col() -> str:
    return (FIXTURES_DIR / "swissquote_transactions_13col.csv").read_text(encoding="utf-8")


@pytest.fixture
def sample_portfolio_text() -> str:
    return (FIXTURES_DIR / "swissquote_portfolio.txt").read_text(encoding="utf-8")


@pytest.fixture
def taxpayer() -> TaxPayer:
    return TaxPayer(
        first_name="Max",
        last_name="Muster",
        street="Bahnhofstrasse 1",
        zip_code="8001",
        city="Zurich",
        canton="ZH",
        ahv_number="756.1234.5678.90",
    )
