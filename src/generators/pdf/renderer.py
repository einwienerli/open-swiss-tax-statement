"""PDF renderer for E-Steuerauszug (eCH-0196).

Builds the complete multi-page PDF using reportlab Canvas directly.
Pages are portrait A4 with a landscape CTM transform for drawing.
pypdf post-processing adds /Rotate=90 for correct landscape display.
"""

from __future__ import annotations

import io
from decimal import Decimal

from PIL import Image as PILImage
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from src.generators.barcode.orchestrator import BarcodeSet
from src.models import (
    Holding,
    IncomeCategory,
    SecurityPayment,
    TaxStatement,
)

from .constants import (
    BARCODES_PER_SHEET,
    CODE128C_PORTRAIT_H,
    CODE128C_PORTRAIT_W,
    CODE128C_PORTRAIT_X,
    CODE128C_PORTRAIT_Y,
    FONT_BOLD,
    FONT_FAMILY,
    FONT_SIZE_BODY,
    FONT_SIZE_HEADING,
    FONT_SIZE_SMALL,
    FONT_SIZE_TITLE,
    FOOTER_COLOR,
    FOOTER_FONT_SIZE,
    FOOTER_Y,
    LINE_HEIGHT_BODY,
    LINE_HEIGHT_HEADING,
    MARGIN_BOTTOM,
    MARGIN_LEFT,
    MARGIN_RIGHT,
    MARGIN_TOP,
    PAGE_HEIGHT,
    PAGE_WIDTH,
    PDF417_PORTRAIT_H,
    PDF417_PORTRAIT_W,
    PDF417_PORTRAIT_X,
    PDF417_SLOT_Y,
    PORTRAIT_WIDTH,
    TABLE_ROW_HEIGHT,
    THOUSANDS_SEP,
)

_EXPENSE_DISPLAY_NAMES = {
    "CUSTODY_FEE": "Depotgebühren",
}


class PdfRenderer:
    """Renders a complete E-Steuerauszug PDF."""

    def __init__(
        self,
        statement: TaxStatement,
        xml_string: str,
        barcode_set: BarcodeSet,
        input_filenames: list[str] | None = None,
    ) -> None:
        self._stmt = statement
        self._xml = xml_string
        self._barcodes = barcode_set
        self._page_num = 0
        self._input_filenames = input_filenames or []

    def render(self) -> bytes:
        """Build complete PDF, return as bytes."""
        buf = io.BytesIO()
        c = Canvas(buf, pagesize=A4)
        tp = self._stmt.taxpayer
        inst = self._stmt.institution.name
        title = f"Open Swiss Tax Statement {self._stmt.tax_year} {inst} {tp.first_name} {tp.last_name}"
        c.setTitle(title)

        # Data pages
        self._draw_cover_page(c)
        if self._stmt.holdings or self._stmt.security_payments:
            self._draw_securities_pages(c)
        if self._stmt.cash_balances:
            self._draw_cash_page(c)
        if self._stmt.expenses:
            self._draw_expenses_section(c)
        self._draw_totals(c)

        # Barcode sheets
        self._draw_barcode_sheets(c)

        c.save()
        return buf.getvalue()

    # ------------------------------------------------------------------
    # Page builders
    # ------------------------------------------------------------------

    def _draw_cover_page(self, c: Canvas) -> None:
        """Page 1: Institution header, taxpayer info, period, summary."""
        self._new_page(c)
        y = PAGE_HEIGHT - MARGIN_TOP
        inst = self._stmt.institution
        tp = self._stmt.taxpayer

        # Institution header
        c.setFont(FONT_BOLD, FONT_SIZE_TITLE)
        c.drawString(MARGIN_LEFT, y, inst.name)
        y -= LINE_HEIGHT_HEADING

        c.setFont(FONT_FAMILY, FONT_SIZE_BODY)
        c.drawString(MARGIN_LEFT, y, f"UID: {inst.uid}")
        y -= LINE_HEIGHT_BODY
        c.drawString(MARGIN_LEFT, y, f"Clearing: {inst.clearing_number}")
        y -= LINE_HEIGHT_BODY * 2

        # Title
        c.setFont(FONT_BOLD, FONT_SIZE_TITLE)
        c.drawString(MARGIN_LEFT, y, f"E-Steuerauszug {self._stmt.tax_year}")
        y -= LINE_HEIGHT_HEADING

        c.setFont(FONT_FAMILY, FONT_SIZE_BODY)
        period_from = self._stmt.period_from.strftime("%d.%m.%Y")
        period_to = self._stmt.period_to.strftime("%d.%m.%Y")
        c.drawString(MARGIN_LEFT, y, f"Steuerperiode: {period_from} - {period_to}")
        y -= LINE_HEIGHT_BODY * 2

        # Taxpayer info
        c.setFont(FONT_BOLD, FONT_SIZE_HEADING)
        c.drawString(MARGIN_LEFT, y, "Steuerpflichtige/r:")
        y -= LINE_HEIGHT_HEADING

        c.setFont(FONT_FAMILY, FONT_SIZE_BODY)
        c.drawString(MARGIN_LEFT, y, f"{tp.first_name} {tp.last_name}")
        y -= LINE_HEIGHT_BODY
        c.drawString(MARGIN_LEFT, y, f"{tp.street}, {tp.zip_code} {tp.city}")
        y -= LINE_HEIGHT_BODY
        if tp.ahv_number:
            c.drawString(MARGIN_LEFT, y, f"AHV: {tp.ahv_number}")
            y -= LINE_HEIGHT_BODY
        y -= LINE_HEIGHT_BODY

        # Summary totals
        self._draw_summary(c, y)

        # CODE128C barcode
        self._draw_page_barcode(c, has_pdf417=False)

    def _draw_summary(self, c: Canvas, y: float) -> None:
        """Draw summary totals box on cover page."""
        c.setFont(FONT_BOLD, FONT_SIZE_HEADING)
        c.drawString(MARGIN_LEFT, y, "Zusammenfassung")
        y -= LINE_HEIGHT_HEADING

        # Separator line
        c.setLineWidth(0.5)
        c.line(MARGIN_LEFT, y, PAGE_WIDTH - MARGIN_RIGHT, y)
        y -= LINE_HEIGHT_BODY

        totals = self._compute_totals()
        labels = [
            ("Steuerwert Wertschriften:", totals["securities_value"]),
            ("Steuerwert Bankkonten:", totals["cash_value"]),
            ("Total Steuerwert:", totals["total_value"]),
            ("Bruttoertrag Rubrik A:", totals["revenue_a"]),
            ("Bruttoertrag Rubrik B:", totals["revenue_b"]),
            ("Verrechnungssteueranspruch:", totals["wht_claim"]),
            ("Spesen:", totals["expenses"]),
        ]

        c.setFont(FONT_FAMILY, FONT_SIZE_BODY)
        label_x = MARGIN_LEFT + 5 * mm
        value_x = PAGE_WIDTH - MARGIN_RIGHT - 5 * mm
        for label, value in labels:
            c.drawString(label_x, y, label)
            c.drawRightString(value_x, y, f"CHF {self._format_chf(value)}")
            y -= LINE_HEIGHT_BODY

    def _draw_securities_pages(self, c: Canvas) -> None:
        """Securities table: one row per holding with payment sub-rows."""
        self._new_page(c)
        y = PAGE_HEIGHT - MARGIN_TOP

        c.setFont(FONT_BOLD, FONT_SIZE_HEADING)
        c.drawString(MARGIN_LEFT, y, "Wertschriftenverzeichnis")
        y -= LINE_HEIGHT_HEADING

        # Wider columns for landscape layout
        headers = ["ISIN", "Bezeichnung", "Whg", "Stk", "Steuerwert CHF", "Ertrag A", "Ertrag B"]
        col_widths = [80, 250, 35, 55, 100, 80, 80]
        y = self._draw_table_header(c, headers, col_widths, MARGIN_LEFT, y)

        # Index holdings and payments by ISIN
        holdings_by_isin: dict[str, Holding] = {}
        for h in self._stmt.holdings:
            holdings_by_isin[h.security.isin] = h
        payments_by_isin = self._group_payments_by_isin()

        # Union of ISINs from holdings and payments (sorted for deterministic output)
        all_isins = sorted(set(holdings_by_isin) | set(payments_by_isin))

        for isin in all_isins:
            holding = holdings_by_isin.get(isin)
            isin_payments = payments_by_isin.get(isin, [])
            security = holding.security if holding else isin_payments[0].security

            # Check page break
            needed = TABLE_ROW_HEIGHT
            needed += len(isin_payments) * TABLE_ROW_HEIGHT
            if y - needed < MARGIN_BOTTOM:
                self._draw_page_barcode(c, has_pdf417=False)
                self._new_page(c)
                y = PAGE_HEIGHT - MARGIN_TOP
                y = self._draw_table_header(c, headers, col_widths, MARGIN_LEFT, y)

            # Holding row
            c.setFont(FONT_FAMILY, FONT_SIZE_BODY)
            x = MARGIN_LEFT
            values = [
                security.isin,
                self._truncate_text(c, security.name, col_widths[1] - 4),
                security.currency,
                self._format_quantity(holding.quantity) if holding else "-",
                self._format_chf(holding.tax_value_chf) if holding else "-",
                "",
                "",
            ]
            # Compute revenue A/B for this security from payments
            rev_a = Decimal("0")
            rev_b = Decimal("0")
            for p in isin_payments:
                cat = p.income_category or security.income_category
                gross = p.gross_amount_chf if p.gross_amount_chf is not None else p.gross_amount
                if cat == IncomeCategory.RUBRIK_A:
                    rev_a += gross
                else:
                    rev_b += gross
            values[5] = self._format_chf(rev_a) if rev_a else ""
            values[6] = self._format_chf(rev_b) if rev_b else ""

            for i, val in enumerate(values):
                if i >= 3:  # right-align numeric columns
                    c.drawRightString(x + col_widths[i], y, str(val))
                else:
                    c.drawString(x, y, str(val))
                x += col_widths[i]
            y -= TABLE_ROW_HEIGHT

            # Payment sub-rows
            for p in isin_payments:
                c.setFont(FONT_FAMILY, FONT_SIZE_SMALL)
                x = MARGIN_LEFT + col_widths[0]  # indent under Bezeichnung
                pay_date = p.payment_date.strftime("%d.%m.%Y")
                cat = p.income_category or security.income_category
                cat_label = f"Rubrik {cat.value}" if cat else "Rubrik B"
                gross_val = p.gross_amount_chf if p.gross_amount_chf is not None else p.gross_amount
                gross = self._format_chf(gross_val)
                wht_val = p.withholding_tax_chf if p.withholding_tax_chf is not None else p.withholding_tax
                wht = self._format_chf(wht_val)
                c.drawString(x, y, f"  {pay_date}  {cat_label}  Brutto: {gross}  VSt: {wht}")
                y -= TABLE_ROW_HEIGHT

        self._draw_page_barcode(c, has_pdf417=False)

    def _draw_cash_page(self, c: Canvas) -> None:
        """Cash balances table."""
        self._new_page(c)
        y = PAGE_HEIGHT - MARGIN_TOP

        c.setFont(FONT_BOLD, FONT_SIZE_HEADING)
        c.drawString(MARGIN_LEFT, y, "Bankkonten")
        y -= LINE_HEIGHT_HEADING

        # Wider columns for landscape
        headers = ["Währung", "Saldo", "Steuerwert CHF", "Zinsen CHF"]
        col_widths = [80, 130, 130, 130]
        y = self._draw_table_header(c, headers, col_widths, MARGIN_LEFT, y)

        c.setFont(FONT_FAMILY, FONT_SIZE_BODY)
        for cb in self._stmt.cash_balances:
            x = MARGIN_LEFT
            vals = [
                cb.currency,
                self._format_chf(cb.balance),
                self._format_chf(cb.tax_value_chf),
                self._format_chf(cb.interest_earned_chf if cb.interest_earned_chf is not None else cb.interest_earned),
            ]
            for i, val in enumerate(vals):
                if i >= 1:
                    c.drawRightString(x + col_widths[i], y, val)
                else:
                    c.drawString(x, y, val)
                x += col_widths[i]
            y -= TABLE_ROW_HEIGHT

        self._draw_page_barcode(c, has_pdf417=False)

    def _draw_expenses_section(self, c: Canvas) -> None:
        """Expenses table."""
        self._new_page(c)
        y = PAGE_HEIGHT - MARGIN_TOP

        c.setFont(FONT_BOLD, FONT_SIZE_HEADING)
        c.drawString(MARGIN_LEFT, y, "Spesen und Unkosten")
        y -= LINE_HEIGHT_HEADING

        headers = ["Typ", "Betrag CHF"]
        col_widths = [250, 130]
        y = self._draw_table_header(c, headers, col_widths, MARGIN_LEFT, y)

        c.setFont(FONT_FAMILY, FONT_SIZE_BODY)
        for exp in self._stmt.expenses:
            x = MARGIN_LEFT
            c.drawString(x, y, _EXPENSE_DISPLAY_NAMES.get(exp.type.value, exp.type.value))
            x += col_widths[0]
            c.drawRightString(x + col_widths[1], y, self._format_chf(abs(exp.net_amount)))
            y -= TABLE_ROW_HEIGHT

        self._draw_page_barcode(c, has_pdf417=False)

    def _draw_totals(self, c: Canvas) -> None:
        """Summary totals at the end of the data pages."""
        # Totals are already on the cover page summary; this is intentional.
        # The cover page acts as the summary. No extra page needed.
        pass

    def _draw_barcode_sheets(self, c: Canvas) -> None:
        """Pages with PDF417 barcodes at fixed grid positions.

        Layout: 2 groups of 3, placed top-to-bottom using pre-computed
        PDF417_SLOT_Y positions. Partial pages use the same positions,
        leaving the rest empty (matching TrueWealth reference).

        Per spec §2.3: page has /Rotate=90 (added by pypdf post-processing),
        so images are stored unrotated in portrait coordinates and appear
        vertical when displayed in landscape.
        """
        barcodes = self._barcodes.data_barcodes
        num_sheets = self._barcodes.num_barcode_sheets

        for sheet_idx in range(num_sheets):
            self._new_page(c)
            start = sheet_idx * BARCODES_PER_SHEET
            end = min(start + BARCODES_PER_SHEET, len(barcodes))
            sheet_barcodes = barcodes[start:end]

            # Switch to portrait coordinates for barcode placement
            c.saveState()
            c.transform(0, -1, 1, 0, 0, PORTRAIT_WIDTH)  # inverse CTM

            for i, bc_img in enumerate(sheet_barcodes):
                img_reader = self._pil_to_reader(bc_img)
                c.drawImage(
                    img_reader, PDF417_PORTRAIT_X, PDF417_SLOT_Y[i],
                    width=PDF417_PORTRAIT_W, height=PDF417_PORTRAIT_H,
                )

            c.restoreState()

            # CODE128C barcode on barcode sheet
            self._draw_page_barcode(c, has_pdf417=True)

    # ------------------------------------------------------------------
    # CODE128C barcode on each page
    # ------------------------------------------------------------------

    def _draw_page_barcode(self, c: Canvas, has_pdf417: bool) -> None:
        """Draw CODE128C barcode on current page in portrait coordinates.

        The barcode image is stored unrotated. Page /Rotate=90 makes it
        appear vertical (top→bottom reading) in the landscape display.
        """
        from src.generators.barcode.orchestrator import generate_page_barcode

        img = generate_page_barcode(
            clearing_number=self._stmt.institution.clearing_number,
            page_number=self._page_num,
            has_pdf417=has_pdf417,
        )
        img_reader = self._pil_to_reader(img)

        # Switch to portrait coordinates
        c.saveState()
        c.transform(0, -1, 1, 0, 0, PORTRAIT_WIDTH)  # inverse CTM
        c.drawImage(
            img_reader, CODE128C_PORTRAIT_X, CODE128C_PORTRAIT_Y,
            width=CODE128C_PORTRAIT_W, height=CODE128C_PORTRAIT_H,
        )
        c.restoreState()
        self._draw_footer(c)

    def _draw_footer(self, c: Canvas) -> None:
        """Draw provenance footer in landscape coordinates."""
        from src import APP_AUTHOR, APP_DISPLAY_NAME, APP_LICENSE, APP_REPO, __version__

        c.saveState()
        c.setFont(FONT_FAMILY, FOOTER_FONT_SIZE)
        c.setFillGray(FOOTER_COLOR)

        y = FOOTER_Y
        # Line 1: App + version + license + author
        c.drawString(MARGIN_LEFT, y, f"{APP_DISPLAY_NAME} v{__version__} \u00b7 {APP_LICENSE} \u00b7 {APP_AUTHOR}")
        y -= FOOTER_FONT_SIZE * 1.3
        # Line 2: GitHub repo
        c.drawString(MARGIN_LEFT, y, APP_REPO)
        if self._input_filenames:
            y -= FOOTER_FONT_SIZE * 1.3
            # Line 3: Input files
            c.drawString(MARGIN_LEFT, y, f"Input: {', '.join(self._input_filenames)}")

        c.restoreState()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _new_page(self, c: Canvas) -> None:
        """Start a new portrait page with landscape drawing CTM.

        First call doesn't showPage; subsequent calls do.
        The CTM maps landscape coordinates (0→842 wide, 0→595 tall)
        to portrait page coordinates.
        """
        if self._page_num > 0:
            c.showPage()
        self._page_num += 1
        c.setPageSize(A4)
        # Landscape drawing coordinates via CTM: (x,y)_landscape → (W-y, x)_portrait
        c.transform(0, 1, -1, 0, PORTRAIT_WIDTH, 0)

    def _draw_table_header(
        self,
        c: Canvas,
        headers: list[str],
        col_widths: list[int | float],
        x_start: float,
        y: float,
    ) -> float:
        """Draw table header row. Returns new y position."""
        c.setFont(FONT_BOLD, FONT_SIZE_BODY)
        x = x_start
        for i, header in enumerate(headers):
            if i >= 1 and i < len(headers):
                # Right-align numeric headers (all except first)
                c.drawRightString(x + col_widths[i], y, header)
            else:
                c.drawString(x, y, header)
            x += col_widths[i]
        # Separator line
        y -= LINE_HEIGHT_BODY * 0.3
        c.setLineWidth(0.5)
        c.line(x_start, y, x_start + sum(col_widths), y)
        y -= LINE_HEIGHT_BODY
        return y

    def _compute_totals(self) -> dict[str, Decimal]:
        """Compute summary totals from statement data."""
        ZERO = Decimal("0")
        s = self._stmt

        sec_value = s.total_tax_value
        if sec_value is None:
            sec_value = sum(
                ((h.tax_value_chf or ZERO) for h in s.holdings), ZERO
            )
        cash_value = sum(
            ((cb.tax_value_chf or ZERO) for cb in s.cash_balances), ZERO
        )
        total_value = sec_value + cash_value

        def _pay_category(p: SecurityPayment) -> IncomeCategory | None:
            return p.income_category or (p.security.income_category if p.security else None)

        rev_a = s.total_gross_revenue_a
        if rev_a is None:
            rev_a = sum(
                ((p.gross_amount_chf if p.gross_amount_chf is not None else p.gross_amount)
                 for p in s.security_payments if _pay_category(p) == IncomeCategory.RUBRIK_A),
                ZERO,
            )
        rev_b = s.total_gross_revenue_b
        if rev_b is None:
            rev_b = sum(
                ((p.gross_amount_chf if p.gross_amount_chf is not None else p.gross_amount)
                 for p in s.security_payments if _pay_category(p) != IncomeCategory.RUBRIK_A),
                ZERO,
            )
        wht_claim = s.total_withholding_tax_claim
        if wht_claim is None:
            wht_claim = sum(
                ((p.withholding_tax_chf if p.withholding_tax_chf is not None else p.withholding_tax)
                 for p in s.security_payments if _pay_category(p) == IncomeCategory.RUBRIK_A),
                ZERO,
            )
        expenses = sum((abs(e.net_amount) for e in s.expenses), ZERO)

        # Add interest to revenue B
        for cb in s.cash_balances:
            interest = cb.interest_earned_chf if cb.interest_earned_chf is not None else cb.interest_earned
            rev_b += interest

        return {
            "securities_value": sec_value,
            "cash_value": cash_value,
            "total_value": total_value,
            "revenue_a": rev_a,
            "revenue_b": rev_b,
            "wht_claim": wht_claim,
            "expenses": expenses,
        }

    def _group_payments_by_isin(self) -> dict[str, list[SecurityPayment]]:
        """Group security payments by ISIN."""
        result: dict[str, list[SecurityPayment]] = {}
        for p in self._stmt.security_payments:
            isin = p.security.isin
            result.setdefault(isin, []).append(p)
        return result

    @staticmethod
    def _format_chf(value: Decimal | int | None) -> str:
        """Format decimal as Swiss CHF string (e.g., 1'234.50)."""
        if value is None:
            return "0.00"
        if not isinstance(value, Decimal):
            value = Decimal(str(value))
        # Round to 2 decimal places
        rounded = value.quantize(Decimal("0.01"))
        sign = "-" if rounded < 0 else ""
        abs_val = abs(rounded)
        int_part = int(abs_val)
        dec_part = abs_val - int_part

        # Format integer part with thousands separator
        int_str = f"{int_part:,}".replace(",", THOUSANDS_SEP)
        dec_str = f"{dec_part:.2f}"[1:]  # ".XX"
        return f"{sign}{int_str}{dec_str}"

    @staticmethod
    def _format_quantity(value: Decimal) -> str:
        """Format quantity (remove trailing zeros)."""
        normalized = value.normalize()
        if normalized == int(normalized):
            return str(int(normalized))
        return str(normalized)

    @staticmethod
    def _truncate_text(c: Canvas, text: str, max_width: float) -> str:
        """Truncate text to fit within max_width pixels."""
        if c.stringWidth(text, FONT_FAMILY, FONT_SIZE_BODY) <= max_width:
            return text
        while len(text) > 3 and c.stringWidth(text + "...", FONT_FAMILY, FONT_SIZE_BODY) > max_width:
            text = text[:-1]
        return text + "..."

    @staticmethod
    def _pil_to_reader(img: PILImage.Image) -> ImageReader:
        """Convert PIL Image to reportlab ImageReader."""
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return ImageReader(buf)
