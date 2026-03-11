"""Tests for Swissquote parser constants — translation dict integrity."""

from __future__ import annotations

from src.parsers.swissquote.constants import (
    KONTOAUSZUG_LABELS,
    PORTFOLIO_LABELS,
)


class TestPortfolioLabels:
    def test_has_de_and_en(self):
        assert "de" in PORTFOLIO_LABELS
        assert "en" in PORTFOLIO_LABELS

    def test_all_locales_have_same_keys(self):
        de_keys = set(PORTFOLIO_LABELS["de"].keys())
        en_keys = set(PORTFOLIO_LABELS["en"].keys())
        assert de_keys == en_keys, f"Key mismatch: DE-only={de_keys - en_keys}, EN-only={en_keys - de_keys}"

    def test_section_types_map_to_same_canonical_values(self):
        de_values = set(PORTFOLIO_LABELS["de"]["section_types"].values())
        en_values = set(PORTFOLIO_LABELS["en"]["section_types"].values())
        assert de_values == en_values

    def test_required_keys_present(self):
        required = {"title", "title_date_prefix", "account_number_label",
                     "account_owner_label", "salutations", "cash_header",
                     "section_types"}
        for locale in PORTFOLIO_LABELS:
            assert required <= set(PORTFOLIO_LABELS[locale].keys()), \
                f"Locale {locale} missing keys: {required - set(PORTFOLIO_LABELS[locale].keys())}"


class TestKontoauszugLabels:
    def test_has_de_and_en(self):
        assert "de" in KONTOAUSZUG_LABELS
        assert "en" in KONTOAUSZUG_LABELS

    def test_all_locales_have_same_keys(self):
        de_keys = set(KONTOAUSZUG_LABELS["de"].keys())
        en_keys = set(KONTOAUSZUG_LABELS["en"].keys())
        assert de_keys == en_keys, f"Key mismatch: DE-only={de_keys - en_keys}, EN-only={en_keys - de_keys}"

    def test_transaction_types_map_to_same_canonical_values(self):
        de_values = set(KONTOAUSZUG_LABELS["de"]["transaction_types"].values())
        en_values = set(KONTOAUSZUG_LABELS["en"]["transaction_types"].values())
        assert de_values == en_values

    def test_detail_keys_map_to_same_canonical_values(self):
        de_values = set(KONTOAUSZUG_LABELS["de"]["detail_keys"].values())
        en_values = set(KONTOAUSZUG_LABELS["en"]["detail_keys"].values())
        assert de_values == en_values

    def test_required_keys_present(self):
        required = {"period_pattern", "account_number_label", "salutations",
                     "currency_section_prefix", "page_footer_patterns",
                     "page_header_patterns", "section_summary_prefixes",
                     "column_header_pattern", "saldo_label",
                     "address_exclude_prefixes", "transaction_types",
                     "detail_keys", "fee_keyword"}
        for locale in KONTOAUSZUG_LABELS:
            assert required <= set(KONTOAUSZUG_LABELS[locale].keys()), \
                f"Locale {locale} missing keys: {required - set(KONTOAUSZUG_LABELS[locale].keys())}"
