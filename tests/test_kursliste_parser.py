"""Tests for ICTAX Kursliste V2.2 XML parser."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from src.enrichment.kursliste_parser import (
    Da1Rate,
    KurslisteEntry,
    parse_da1_rates,
    parse_da1_rates_all,
    parse_kursliste,
    parse_kursliste_indexed,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_XML = str(FIXTURES_DIR / "kursliste_sample.xml")


class TestParseKursliste:
    def test_finds_share_by_isin(self):
        result = parse_kursliste(SAMPLE_XML, {"CH0038863350"})
        assert "CH0038863350" in result
        entry = result["CH0038863350"]
        assert entry.valor_number == "3886335"
        assert entry.security_group == "SHARE"
        assert entry.currency == "CHF"
        assert entry.country == "CH"
        assert entry.year_end_price == Decimal("74.52")
        assert entry.tax_value_chf == Decimal("74.52")
        assert entry.quotation_type == "PIECE"

    def test_finds_fund_by_isin(self):
        result = parse_kursliste(SAMPLE_XML, {"IE00B3XXRP09"})
        assert "IE00B3XXRP09" in result
        entry = result["IE00B3XXRP09"]
        assert entry.valor_number == "18575458"
        assert entry.security_group == "FUND"
        assert entry.security_type == "FUND.DISTRIBUTION"
        assert entry.currency == "USD"
        assert entry.year_end_price is None  # Foreign: only taxValueCHF
        assert entry.tax_value_chf == Decimal("103.56")

    def test_finds_bond_by_isin(self):
        result = parse_kursliste(SAMPLE_XML, {"CH0123456789"})
        assert "CH0123456789" in result
        entry = result["CH0123456789"]
        assert entry.security_group == "BOND"
        assert entry.year_end_price == Decimal("101.50")
        assert entry.quotation_type == "PERCENT"

    def test_multiple_isins(self):
        isins = {"CH0038863350", "US0378331005", "IE00B3XXRP09"}
        result = parse_kursliste(SAMPLE_XML, isins)
        assert len(result) == 3
        assert result["US0378331005"].valor_number == "908440"
        assert result["US0378331005"].currency == "USD"
        assert result["US0378331005"].year_end_price == Decimal("254.49")

    def test_filters_unrequested_isins(self):
        result = parse_kursliste(SAMPLE_XML, {"CH0038863350"})
        assert len(result) == 1
        assert "US0378331005" not in result

    def test_missing_isin_not_in_result(self):
        result = parse_kursliste(SAMPLE_XML, {"XX0000000000"})
        assert len(result) == 0

    def test_empty_isins_returns_empty(self):
        result = parse_kursliste(SAMPLE_XML, set())
        assert result == {}

    def test_entry_is_dataclass(self):
        result = parse_kursliste(SAMPLE_XML, {"CH0038863350"})
        entry = result["CH0038863350"]
        assert isinstance(entry, KurslisteEntry)

    def test_us_share_country(self):
        result = parse_kursliste(SAMPLE_XML, {"US0378331005"})
        assert result["US0378331005"].country == "US"

    def test_german_share(self):
        result = parse_kursliste(SAMPLE_XML, {"DE0007236101"})
        entry = result["DE0007236101"]
        assert entry.currency == "EUR"
        assert entry.country == "DE"
        assert entry.year_end_price == Decimal("195.30")

    def test_usd_share_tax_value_chf(self):
        """V2.2 provides pre-computed taxValueCHF for foreign securities."""
        result = parse_kursliste(SAMPLE_XML, {"US0378331005"})
        entry = result["US0378331005"]
        assert entry.tax_value_chf == Decimal("228.97")
        assert entry.exchange_rate == Decimal("0.899800")

    def test_fund_no_original_currency_price(self):
        """Foreign funds may have only taxValueCHF, no original-currency taxValue."""
        result = parse_kursliste(SAMPLE_XML, {"IE00B3XXRP09"})
        entry = result["IE00B3XXRP09"]
        assert entry.year_end_price is None
        assert entry.tax_value_chf == Decimal("103.56")
        assert entry.exchange_rate is None  # Not on this fund's yearend

    def test_chf_share_exchange_rate_is_none(self):
        """CHF securities have no exchange rate on yearend element."""
        result = parse_kursliste(SAMPLE_XML, {"CH0038863350"})
        assert result["CH0038863350"].exchange_rate is None


class TestParseKurslisteIndexed:
    def test_indexed_lookup_matches_stream(self, tmp_path):
        """Index-based lookup returns same data as stream parse."""
        # Create .ix index file pointing to offsets in our sample XML
        xml_path = SAMPLE_XML
        # Read the XML to find byte offsets of each security element
        xml_bytes = Path(xml_path).read_bytes()

        ix_lines = []
        for valor, isin in [
            ("3886335", "CH0038863350"),
            ("908440", "US0378331005"),
            ("18575458", "IE00B3XXRP09"),
        ]:
            # Find the start tag byte offset
            search_isin = f'isin="{isin}"'.encode()
            pos = xml_bytes.find(search_isin)
            if pos == -1:
                continue
            # Back up to find the element start (<share, <fund, etc.)
            start = xml_bytes.rfind(b"<", 0, pos)
            # Find the closing tag
            tag = xml_bytes[start + 1 : xml_bytes.find(b" ", start)].decode()
            end_tag = f"</{tag}>".encode()
            end = xml_bytes.find(end_tag, start) + len(end_tag)
            ix_lines.append(f"{valor};{isin};{start};{end - start}")

        ix_file = tmp_path / "kursliste.ix"
        ix_file.write_text("\n".join(ix_lines) + "\n")

        isins = {"CH0038863350", "US0378331005", "IE00B3XXRP09"}
        stream_result = parse_kursliste(xml_path, isins)
        indexed_result = parse_kursliste_indexed(xml_path, str(ix_file), isins)

        assert set(indexed_result.keys()) == set(stream_result.keys())
        for isin in isins:
            assert indexed_result[isin].valor_number == stream_result[isin].valor_number
            assert indexed_result[isin].tax_value_chf == stream_result[isin].tax_value_chf

    def test_falls_back_when_ix_missing(self):
        """When .ix file doesn't exist, falls back to stream parse."""
        result = parse_kursliste_indexed(
            SAMPLE_XML, "/nonexistent/file.ix", {"CH0038863350"}
        )
        assert "CH0038863350" in result
        assert result["CH0038863350"].valor_number == "3886335"

    def test_empty_isins_returns_empty(self, tmp_path):
        ix_file = tmp_path / "kursliste.ix"
        ix_file.write_text("")
        result = parse_kursliste_indexed(SAMPLE_XML, str(ix_file), set())
        assert result == {}


class TestParseDa1Rates:
    """Tests for DA-1 rate parsing from Kursliste XML."""

    def test_parses_other_rate_type(self):
        """parse_da1_rates returns only OTHER rate type entries."""
        result = parse_da1_rates(SAMPLE_XML)
        # US:SHARE has both OTHER and LP entries; only OTHER should be returned
        assert ("US", "SHARE") in result
        rate = result[("US", "SHARE")]
        assert rate.da1_rate_type == "OTHER"

    def test_us_share_rates(self):
        result = parse_da1_rates(SAMPLE_XML)
        rate = result[("US", "SHARE")]
        assert rate.withholding_rate == Decimal("30")
        assert rate.release == Decimal("15")
        assert rate.non_recoverable == Decimal("15")
        assert rate.non_recoverable_max == Decimal("15")

    def test_de_share_rates(self):
        result = parse_da1_rates(SAMPLE_XML)
        rate = result[("DE", "SHARE")]
        assert rate.withholding_rate == Decimal("26.375")
        assert rate.release == Decimal("0")
        assert rate.non_recoverable == Decimal("26.375")

    def test_ie_fund_rates(self):
        result = parse_da1_rates(SAMPLE_XML)
        rate = result[("IE", "FUND")]
        assert rate.withholding_rate == Decimal("25")
        assert rate.release == Decimal("10")
        assert rate.non_recoverable == Decimal("15")

    def test_lp_rate_excluded(self):
        """LP rate type entries should not appear in parse_da1_rates result."""
        result = parse_da1_rates(SAMPLE_XML)
        # There should be only one US:SHARE entry (OTHER, not LP)
        rate = result[("US", "SHARE")]
        assert rate.da1_rate_type == "OTHER"
        assert rate.withholding_rate == Decimal("30")  # OTHER rate, not LP's 15

    def test_parse_all_includes_lp(self):
        """parse_da1_rates_all includes all rate types including LP."""
        result = parse_da1_rates_all(SAMPLE_XML)
        # Should include at least 5 entries (4 OTHER + 1 LP)
        assert len(result) == 5
        lp_rates = [r for r in result if r.da1_rate_type == "LP"]
        assert len(lp_rates) == 1
        assert lp_rates[0].country == "US"

    def test_da1_rate_is_dataclass(self):
        result = parse_da1_rates(SAMPLE_XML)
        rate = result[("US", "SHARE")]
        assert isinstance(rate, Da1Rate)

    def test_key_is_country_security_group(self):
        """Keys are (country, securityGroup) tuples."""
        result = parse_da1_rates(SAMPLE_XML)
        for key in result:
            assert isinstance(key, tuple)
            assert len(key) == 2
            country, group = key
            assert len(country) == 2
            assert group in ("SHARE", "FUND", "BOND")
