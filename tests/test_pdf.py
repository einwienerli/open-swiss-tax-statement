"""Tests for PDF generation (Phase 5)."""

from __future__ import annotations

import io
import math
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from pypdf import PdfReader

from src.generators.pdf import generate_pdf, generate_pdf_from_xml
from src.generators.pdf.assembler import _pack_1bit
from src.generators.pdf.renderer import PdfRenderer
from src.models import (
    CashBalance,
    Holding,
    IncomeCategory,
    Institution,
    Security,
    SecurityPayment,
    TaxPayer,
    TaxStatement,
    Transaction,
    TransactionType,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def taxpayer():
    return TaxPayer(
        first_name="Max",
        last_name="Muster",
        street="Bahnhofstrasse 1",
        zip_code="8001",
        city="Zurich",
        canton="ZH",
        ahv_number="756.1234.5678.90",
    )


@pytest.fixture
def institution():
    return Institution(
        name="Swissquote Bank SA",
        uid="CHE-106.787.734",
        clearing_number="08750",
    )


@pytest.fixture
def empty_statement(taxpayer, institution):
    """Minimal statement with no holdings/payments/expenses."""
    return TaxStatement(
        tax_year=2025,
        period_from=date(2025, 1, 1),
        period_to=date(2025, 12, 31),
        taxpayer=taxpayer,
        institution=institution,
        account_number="123456",
    )


@pytest.fixture
def rich_statement(taxpayer, institution):
    """Statement with multiple holdings, payments, cash, and expenses."""
    nestle = Security(isin="CH0038863350", name="NESTLE SA", currency="CHF", valor_number="3886335")
    apple = Security(isin="US0378331005", name="APPLE INC", currency="USD")
    vanguard = Security(
        isin="IE00B3XXRP09",
        name="VANGUARD FTSE ALL-WORLD UCITS ETF",
        currency="EUR",
    )

    holdings = [
        Holding(security=nestle, quantity=Decimal("70"), tax_value_chf=Decimal("7000")),
        Holding(security=apple, quantity=Decimal("50"), tax_value_chf=Decimal("8000")),
        Holding(security=vanguard, quantity=Decimal("200"), tax_value_chf=Decimal("17060")),
    ]
    cash_balances = [
        CashBalance(
            currency="CHF",
            balance=Decimal("5000"),
            tax_value_chf=Decimal("5000"),
            interest_earned=Decimal("12.50"),
        ),
        CashBalance(
            currency="USD",
            balance=Decimal("3000"),
            tax_value_chf=Decimal("2700"),
            interest_earned=Decimal("0.85"),
        ),
    ]
    payments = [
        SecurityPayment(
            security=nestle,
            payment_date=date(2025, 3, 15),
            gross_amount=Decimal("300"),
            currency="CHF",
            withholding_tax=Decimal("105"),
            net_amount=Decimal("195"),
            quantity=Decimal("70"),
            income_category=IncomeCategory.RUBRIK_A,
        ),
        SecurityPayment(
            security=apple,
            payment_date=date(2025, 5, 10),
            gross_amount=Decimal("19.50"),
            currency="USD",
            withholding_tax=Decimal("5.85"),
            net_amount=Decimal("13.65"),
            quantity=Decimal("50"),
        ),
    ]
    expenses = [
        Transaction(
            date=date(2025, 9, 15),
            type=TransactionType.CUSTODY_FEE,
            net_amount=Decimal("-45"),
            currency="CHF",
        ),
    ]

    return TaxStatement(
        tax_year=2025,
        period_from=date(2025, 1, 1),
        period_to=date(2025, 12, 31),
        taxpayer=taxpayer,
        institution=institution,
        holdings=holdings,
        cash_balances=cash_balances,
        security_payments=payments,
        expenses=expenses,
        account_number="123456",
    )


def _extract_text(pdf_bytes: bytes) -> str:
    """Extract all text from PDF bytes using pypdf."""
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _get_page_sizes(pdf_bytes: bytes) -> list[tuple[float, float]]:
    """Return (width, height) for each page's mediabox."""
    reader = PdfReader(BytesIO(pdf_bytes))
    return [
        (float(page.mediabox.width), float(page.mediabox.height))
        for page in reader.pages
    ]


# ===========================================================================
# PDF generation tests
# ===========================================================================

class TestGeneratePdf:
    def test_generates_valid_pdf(self, rich_statement):
        """Output starts with %PDF."""
        pdf = generate_pdf(rich_statement)
        assert pdf[:5] == b"%PDF-"

    def test_has_correct_page_count(self, rich_statement):
        """Cover(1) + securities(1) + cash(1) + expenses(1) + barcode_sheets(>=1)."""
        pdf = generate_pdf(rich_statement)
        reader = PdfReader(BytesIO(pdf))
        # At minimum: 1 cover + 1 securities + 1 cash + 1 expenses + 1 barcode = 5
        assert len(reader.pages) >= 5

    def test_contains_taxpayer_name(self, rich_statement):
        """Taxpayer name appears in PDF text."""
        pdf = generate_pdf(rich_statement)
        text = _extract_text(pdf)
        assert "Max" in text
        assert "Muster" in text

    def test_contains_institution_name(self, rich_statement):
        """Institution name appears in PDF text."""
        pdf = generate_pdf(rich_statement)
        text = _extract_text(pdf)
        assert "Swissquote Bank SA" in text

    def test_contains_tax_year(self, rich_statement):
        """Tax year appears in PDF text."""
        pdf = generate_pdf(rich_statement)
        text = _extract_text(pdf)
        assert "2025" in text

    def test_contains_security_names(self, rich_statement):
        """Security names appear in PDF."""
        pdf = generate_pdf(rich_statement)
        text = _extract_text(pdf)
        assert "NESTLE" in text
        assert "APPLE" in text

    def test_all_pages_portrait_mediabox(self, rich_statement):
        """All pages should have portrait mediabox (width < height)."""
        pdf = generate_pdf(rich_statement)
        sizes = _get_page_sizes(pdf)
        for i, (w, h) in enumerate(sizes):
            assert w < h, f"Page {i+1} mediabox should be portrait (w={w}, h={h})"

    def test_all_pages_rotate_90(self, rich_statement):
        """All pages should have /Rotate=90 for landscape display."""
        pdf = generate_pdf(rich_statement)
        reader = PdfReader(BytesIO(pdf))
        for i, page in enumerate(reader.pages):
            rotate = page.get("/Rotate", 0)
            assert rotate == 90, f"Page {i+1} should have /Rotate=90, got {rotate}"

    def test_small_statement_portrait_plus_rotate(self, empty_statement):
        """Minimal data produces portrait pages with /Rotate=90."""
        pdf = generate_pdf(empty_statement)
        reader = PdfReader(BytesIO(pdf))
        for i, page in enumerate(reader.pages):
            w = float(page.mediabox.width)
            h = float(page.mediabox.height)
            assert w < h, f"Page {i+1} mediabox should be portrait"
            assert page.get("/Rotate", 0) == 90

    def test_empty_statement_no_securities_page(self, empty_statement):
        """No holdings means fewer pages (no securities table)."""
        pdf = generate_pdf(empty_statement)
        reader = PdfReader(BytesIO(pdf))
        # Cover(1) + barcode_sheet(>=1), no securities/cash/expenses pages
        assert len(reader.pages) >= 2

    def test_contains_ahv_number(self, rich_statement):
        """AHV number appears on cover page."""
        pdf = generate_pdf(rich_statement)
        text = _extract_text(pdf)
        assert "756.1234.5678.90" in text


class TestFormatChf:
    """Test Swiss CHF number formatting."""

    def test_simple_value(self):
        assert PdfRenderer._format_chf(Decimal("1234.50")) == "1'234.50"

    def test_zero(self):
        assert PdfRenderer._format_chf(Decimal("0")) == "0.00"

    def test_none(self):
        assert PdfRenderer._format_chf(None) == "0.00"

    def test_large_value(self):
        assert PdfRenderer._format_chf(Decimal("1234567.89")) == "1'234'567.89"

    def test_small_value(self):
        assert PdfRenderer._format_chf(Decimal("45.00")) == "45.00"

    def test_negative_value(self):
        assert PdfRenderer._format_chf(Decimal("-120.50")) == "-120.50"

    def test_rounding(self):
        # Should round to 2 decimal places
        assert PdfRenderer._format_chf(Decimal("1.999")) == "2.00"


# ===========================================================================
# Barcode colorspace tests
# ===========================================================================

def _get_barcode_xobjects(pdf_bytes: bytes) -> list[dict]:
    """Extract barcode XObject properties from PDF bytes."""
    reader = PdfReader(BytesIO(pdf_bytes))
    results = []
    for page_idx, page in enumerate(reader.pages):
        resources = page.get("/Resources")
        if not resources:
            continue
        xobjects = resources.get("/XObject")
        if not xobjects:
            continue
        xobj_dict = xobjects.get_object()
        for name in xobj_dict:
            xobj = xobj_dict[name].get_object()
            if xobj.get("/Subtype") != "/Image":
                continue
            w = int(xobj["/Width"])
            h = int(xobj["/Height"])
            results.append({
                "page": page_idx + 1,
                "name": name,
                "width": w,
                "height": h,
                "colorspace": str(xobj.get("/ColorSpace", "")),
                "bpc": int(xobj.get("/BitsPerComponent", 0)),
                "filter": str(xobj.get("/Filter", "")),
                "data_len": len(xobj.get_data()),
            })
    return results


class TestBarcodeColorspace:
    """Verify barcode XObjects are DeviceGray/1-bit after post-processing."""

    def test_code128c_is_device_gray_bpc1(self, rich_statement):
        """CODE128C XObjects (123x46) should be DeviceGray BPC=1."""
        pdf = generate_pdf(rich_statement)
        xobjects = _get_barcode_xobjects(pdf)
        code128c = [x for x in xobjects if (x["width"], x["height"]) == (123, 46)]
        assert len(code128c) > 0, "No CODE128C XObjects found"
        for xobj in code128c:
            assert xobj["colorspace"] == "/DeviceGray", f"Page {xobj['page']}: expected /DeviceGray, got {xobj['colorspace']}"
            assert xobj["bpc"] == 1, f"Page {xobj['page']}: expected BPC=1, got {xobj['bpc']}"

    def test_pdf417_is_device_gray_bpc1(self, rich_statement):
        """PDF417 XObjects (290x35) should be DeviceGray BPC=1."""
        pdf = generate_pdf(rich_statement)
        xobjects = _get_barcode_xobjects(pdf)
        pdf417 = [x for x in xobjects if (x["width"], x["height"]) == (290, 35)]
        assert len(pdf417) > 0, "No PDF417 XObjects found"
        for xobj in pdf417:
            assert xobj["colorspace"] == "/DeviceGray", f"Page {xobj['page']}: expected /DeviceGray, got {xobj['colorspace']}"
            assert xobj["bpc"] == 1, f"Page {xobj['page']}: expected BPC=1, got {xobj['bpc']}"

    def test_code128c_pixel_data_size(self, rich_statement):
        """1-bit packed CODE128C: ceil(123/8) * 46 = 736 bytes."""
        pdf = generate_pdf(rich_statement)
        xobjects = _get_barcode_xobjects(pdf)
        code128c = [x for x in xobjects if (x["width"], x["height"]) == (123, 46)]
        expected = math.ceil(123 / 8) * 46  # 16 * 46 = 736
        for xobj in code128c:
            assert xobj["data_len"] == expected, f"Expected {expected} bytes, got {xobj['data_len']}"

    def test_pdf417_pixel_data_size(self, rich_statement):
        """1-bit packed PDF417: ceil(290/8) * 35 = 1295 bytes."""
        pdf = generate_pdf(rich_statement)
        xobjects = _get_barcode_xobjects(pdf)
        pdf417 = [x for x in xobjects if (x["width"], x["height"]) == (290, 35)]
        expected = math.ceil(290 / 8) * 35  # 37 * 35 = 1295
        for xobj in pdf417:
            assert xobj["data_len"] == expected, f"Expected {expected} bytes, got {xobj['data_len']}"

    def test_xobject_names_sequential(self, rich_statement):
        """XObject names must be /Im1, /Im2, ... (portal rejects /FormXob.*)."""
        pdf = generate_pdf(rich_statement)
        reader = PdfReader(BytesIO(pdf))
        for page_idx, page in enumerate(reader.pages):
            resources = page.get("/Resources")
            if not resources:
                continue
            xobjects = resources.get("/XObject")
            if not xobjects:
                continue
            for name in xobjects.get_object():
                assert name.startswith("/Im") and name[3:].isdigit(), (
                    f"Page {page_idx+1}: XObject {name!r} should be /Im<N>"
                )


class TestPackOneBit:
    """Unit tests for the _pack_1bit helper."""

    def test_all_white(self):
        """All pixels >= 128 → all bits set."""
        data = bytes([255] * 16)
        result = _pack_1bit(data, 16, 1)
        assert result == b"\xff\xff"

    def test_all_black(self):
        """All pixels < 128 → all bits clear."""
        data = bytes([0] * 16)
        result = _pack_1bit(data, 16, 1)
        assert result == b"\x00\x00"

    def test_threshold_boundary(self):
        """Pixel=127 → black(0), pixel=128 → white(1)."""
        data = bytes([128, 127])
        result = _pack_1bit(data, 2, 1)
        # bit0=1, bit1=0 → 0x80 (MSB-first), padded to 1 byte
        assert result == b"\x80"

    def test_row_padding(self):
        """Width=3 pixels → 1 byte per row (padded to byte boundary)."""
        data = bytes([255, 0, 255])  # 1,0,1
        result = _pack_1bit(data, 3, 1)
        # bits: 1,0,1,0,0,0,0,0 → 0xA0
        assert result == b"\xa0"

    def test_output_size(self):
        """Verify packed size matches ceil(w/8) * h."""
        w, h = 123, 46
        data = bytes([200] * (w * h))
        result = _pack_1bit(data, w, h)
        assert len(result) == math.ceil(w / 8) * h


class TestGeneratePdfFromXml:
    """Test barcode-only PDF generation from raw XML."""

    @pytest.fixture
    def sample_xml(self, rich_statement):
        """Build XML from a rich statement for testing."""
        from src.generators.ech0196.xml_builder import build_xml, serialize
        root = build_xml(rich_statement)
        return serialize(root)

    def test_produces_valid_pdf(self, sample_xml):
        """Output starts with %PDF-."""
        pdf = generate_pdf_from_xml(sample_xml, "08750")
        assert pdf[:5] == b"%PDF-"

    def test_has_rotate_90(self, sample_xml):
        """All pages have /Rotate=90."""
        pdf = generate_pdf_from_xml(sample_xml, "08750")
        reader = PdfReader(BytesIO(pdf))
        assert len(reader.pages) >= 1
        for i, page in enumerate(reader.pages):
            rotate = page.get("/Rotate", 0)
            assert rotate == 90, f"Page {i+1} should have /Rotate=90, got {rotate}"

    def test_barcodes_are_device_gray_bpc1(self, sample_xml):
        """All barcode XObjects are DeviceGray/1-bit."""
        pdf = generate_pdf_from_xml(sample_xml, "08750")
        xobjects = _get_barcode_xobjects(pdf)
        assert len(xobjects) > 0, "No barcode XObjects found"
        for xobj in xobjects:
            assert xobj["colorspace"] == "/DeviceGray", f"{xobj['name']}: expected /DeviceGray"
            assert xobj["bpc"] == 1, f"{xobj['name']}: expected BPC=1"

    def test_has_barcode_pages_only(self, sample_xml):
        """PDF should have barcode sheets only (no data pages)."""
        pdf = generate_pdf_from_xml(sample_xml, "08750")
        reader = PdfReader(BytesIO(pdf))
        # Each page should have XObject images (barcodes)
        for i, page in enumerate(reader.pages):
            resources = page.get("/Resources")
            assert resources is not None, f"Page {i+1} missing /Resources"
            xobjects = resources.get("/XObject")
            assert xobjects is not None, f"Page {i+1} missing /XObject (no barcodes)"


class TestPdfFooter:
    def test_footer_contains_app_name(self, taxpayer, institution):
        """PDF pages contain the app name in the footer."""
        from src import APP_DISPLAY_NAME
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            cash_balances=[CashBalance("CHF", Decimal("5000"), Decimal("5000"))],
            account_number="123456",
        )
        pdf_bytes = generate_pdf(stmt, input_filenames=["test.csv", "portfolio.pdf"])
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = reader.pages[0].extract_text() or ""
        assert APP_DISPLAY_NAME in text or "Open E-Tax" in text

    def test_footer_contains_input_filenames(self, taxpayer, institution):
        """PDF footer shows the input filenames."""
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            cash_balances=[CashBalance("CHF", Decimal("5000"), Decimal("5000"))],
            account_number="123456",
        )
        pdf_bytes = generate_pdf(stmt, input_filenames=["mydata.csv", "portfolio.pdf"])
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = reader.pages[0].extract_text() or ""
        assert "mydata.csv" in text
        assert "portfolio.pdf" in text

    def test_footer_without_filenames(self, taxpayer, institution):
        """PDF footer works when no filenames provided."""
        stmt = TaxStatement(
            tax_year=2025, period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31), taxpayer=taxpayer,
            institution=institution,
            cash_balances=[CashBalance("CHF", Decimal("5000"), Decimal("5000"))],
            account_number="123456",
        )
        pdf_bytes = generate_pdf(stmt)
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = reader.pages[0].extract_text() or ""
        assert "Open Swiss Tax" in text
        assert "Input:" not in text  # No input line when no filenames


class TestPdfTotalsMatchXml:
    """Printed PDF totals must use the same Rubrik A/B logic as the XML."""

    def test_swiss_dividend_without_explicit_category_is_rubrik_a(self, taxpayer, institution):
        # Parsers never set income_category; the XML builder applies the
        # CH-ISIN + WHT heuristic. The printed summary must do the same,
        # otherwise the Verrechnungssteueranspruch shows 0.
        nestle = Security(isin="CH0038863350", name="NESTLE SA", currency="CHF")
        apple = Security(isin="US0378331005", name="APPLE INC", currency="USD")
        stmt = TaxStatement(
            tax_year=2025,
            period_from=date(2025, 1, 1),
            period_to=date(2025, 12, 31),
            taxpayer=taxpayer,
            institution=institution,
            security_payments=[
                SecurityPayment(
                    security=nestle, payment_date=date(2025, 4, 17),
                    gross_amount=Decimal("300"), currency="CHF",
                    withholding_tax=Decimal("105"), net_amount=Decimal("195"),
                ),
                SecurityPayment(
                    security=apple, payment_date=date(2025, 5, 10),
                    gross_amount=Decimal("20"), currency="USD",
                    withholding_tax=Decimal("3"), net_amount=Decimal("17"),
                    gross_amount_chf=Decimal("18"), withholding_tax_chf=Decimal("2.70"),
                ),
            ],
            account_number="123456",
        )
        totals = PdfRenderer(stmt, "", None)._compute_totals()
        assert totals["revenue_a"] == Decimal("300")
        assert totals["wht_claim"] == Decimal("105")
        assert totals["revenue_b"] == Decimal("18")

        from src.generators.ech0196 import build_xml
        root = build_xml(stmt)
        assert Decimal(root.get("totalGrossRevenueA")) == totals["revenue_a"]
        assert Decimal(root.get("totalWithHoldingTaxClaim")) == totals["wht_claim"]

    def test_total_tax_value_not_double_counting_cash(self, empty_statement):
        empty_statement.cash_balances = [
            CashBalance(currency="CHF", balance=Decimal("100"), tax_value_chf=Decimal("100")),
        ]
        empty_statement.total_tax_value = Decimal("100")
        totals = PdfRenderer(empty_statement, "", None)._compute_totals()
        assert totals["total_value"] == Decimal("100")
