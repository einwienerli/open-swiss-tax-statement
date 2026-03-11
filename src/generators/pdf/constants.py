"""Constants for E-Steuerauszug PDF generation."""

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm, mm

# Landscape drawing dimensions (used by all text/table code via CTM)
PAGE_WIDTH, PAGE_HEIGHT = landscape(A4)  # 841.89 x 595.27 pt (297 x 210 mm)

# Portrait page dimensions (actual page setup and barcode placement)
PORTRAIT_WIDTH, PORTRAIT_HEIGHT = A4  # 595.27 x 841.89 pt

# Margins (in landscape drawing coordinates)
MARGIN_LEFT = 25 * mm  # 5mm quiet zone past CODE128C right edge (18mm + 5mm + 2mm buffer)
MARGIN_RIGHT = 20 * mm
MARGIN_TOP = 15 * mm
MARGIN_BOTTOM = 25 * mm  # room for CODE128C at bottom

# Usable content area
CONTENT_WIDTH = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT

# Fonts
FONT_FAMILY = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
FONT_SIZE_TITLE = 16
FONT_SIZE_HEADING = 12
FONT_SIZE_BODY = 9
FONT_SIZE_SMALL = 7

# Line spacing
LINE_HEIGHT_BODY = FONT_SIZE_BODY * 1.4
LINE_HEIGHT_HEADING = FONT_SIZE_HEADING * 1.6
TABLE_ROW_HEIGHT = FONT_SIZE_BODY * 1.8

# CODE128C placement in portrait coordinates
# In landscape view: appears upper-left, vertical (top→bottom reading)
CODE128C_PORTRAIT_X = 29    # pt from left of portrait (~10mm from top in landscape)
CODE128C_PORTRAIT_Y = 14    # pt from bottom of portrait (~5mm from left in landscape)
CODE128C_PORTRAIT_W = 123   # pt display width
CODE128C_PORTRAIT_H = 37    # pt display height

# PDF417 placement in portrait coordinates (horizontal images stacked vertically)
# Spec: 290×35 pixel images, 12.18cm wide × 2.8cm tall with 97% print compensation
PDF417_PORTRAIT_X = 185     # pt from left of portrait (vertical position in landscape)
PDF417_PORTRAIT_W = 12.18 * cm / 0.97   # spec width with 97% print compensation
PDF417_PORTRAIT_H = 2.8 * cm / 0.97     # spec height with 97% print compensation

# Barcode sheet layout: 6 per sheet in 2 groups of 3 (matching TrueWealth reference)
# All spacing is multiples of unit G = (page_height - 6*barcode_height) / 12
# Top/bottom margin = 3G, within-group gap = G, between-group gap = 2G
BARCODES_PER_SHEET = 6
_PDF417_UNIT = (PORTRAIT_HEIGHT - BARCODES_PER_SHEET * PDF417_PORTRAIT_H) / 12
_PDF417_TOP_MARGIN = 3 * _PDF417_UNIT

# 6 fixed y-positions (portrait coords, bottom edge), filled top-to-bottom.
# Partial pages use the same positions, leaving the rest empty.
PDF417_SLOT_Y: tuple[float, ...] = tuple(
    PORTRAIT_HEIGHT - _PDF417_TOP_MARGIN - PDF417_PORTRAIT_H
    - i * (PDF417_PORTRAIT_H + _PDF417_UNIT)
    - (1 * _PDF417_UNIT if i >= 3 else 0)  # extra unit gap after slot 3
    for i in range(BARCODES_PER_SHEET)
)

# Swiss number formatting
THOUSANDS_SEP = "'"
DECIMAL_SEP = "."

# Footer (in landscape drawing coordinates)
FOOTER_FONT_SIZE = 6
FOOTER_Y = 8 * mm  # below MARGIN_BOTTOM (25mm), above page edge
FOOTER_COLOR = 0.5  # gray (0=black, 1=white)
