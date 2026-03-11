"""CODE128C page barcode generation for eCH-0196.

Pure implementation — no external barcode library dependency.
Generates a 123×46 pixel 1-bit B&W image matching the spec and
TrueWealth reference layout.

Image structure (46 rows):
  Rows  0-2:  quiet zone (3px white)
  Rows  3-28: barcode bars (26px, all rows identical)
  Rows 29-37: gap (9px white)
  Rows 38-44: human-readable number (7px, 5×7 bitmap font)
  Row  45:    bottom padding (1px white)
"""

from __future__ import annotations

from PIL import Image

from .constants import FORM_NUMBER, VERSION

# ---------------------------------------------------------------------------
# Code 128 bar/space width patterns (ISO/IEC 15417)
# Each string: alternating bar-space widths, 6 elements = 11 modules.
# Stop (index 106): 7 elements = 13 modules.
# ---------------------------------------------------------------------------
_PATTERNS = (
    "212222", "222122", "222221", "121223", "121322",  # 0-4
    "131222", "122213", "122312", "132212", "221213",  # 5-9
    "221312", "231212", "112232", "122132", "122231",  # 10-14
    "113222", "123122", "123221", "223211", "221132",  # 15-19
    "221231", "213212", "223112", "312131", "311222",  # 20-24
    "321122", "321221", "312212", "322112", "322211",  # 25-29
    "212123", "212321", "232121", "111323", "131123",  # 30-34
    "131321", "112313", "132113", "132311", "211313",  # 35-39
    "231113", "231311", "112133", "112331", "132131",  # 40-44
    "113123", "113321", "133121", "313121", "211331",  # 45-49
    "231131", "213113", "213311", "213131", "311123",  # 50-54
    "311321", "331121", "312113", "312311", "332111",  # 55-59
    "314111", "221411", "431111", "111224", "111422",  # 60-64
    "121124", "121421", "141122", "141221", "112214",  # 65-69
    "112412", "122114", "122411", "142112", "142211",  # 70-74
    "241211", "221114", "413111", "241112", "134111",  # 75-79
    "111242", "121142", "121241", "114212", "124112",  # 80-84
    "124211", "411212", "421112", "421211", "212141",  # 85-89
    "214121", "412121", "111143", "111341", "131141",  # 90-94
    "114113", "114311", "411113", "411311", "113141",  # 95-99
    "114131", "311141", "411131",                       # 100-102
    "211412", "211214", "211232",                       # 103-105 (Start A/B/C)
    "2331112",                                          # 106 (Stop)
)

_START_C = 105
_STOP = 106

# ---------------------------------------------------------------------------
# Image layout constants (pixels, matching TrueWealth reference)
# ---------------------------------------------------------------------------
_WIDTH = 123
_HEIGHT = 46
_QUIET_TOP = 3
_BAR_ROWS = 26      # rows 3-28
_GAP_ROWS = 9       # rows 29-37 (white)
_TEXT_Y = 38         # rows 38-44 (7px font)

# ---------------------------------------------------------------------------
# 5×7 bitmap font for digits 0-9
# Each digit = 7 rows, each row is a 5-bit value (MSB = leftmost pixel)
# ---------------------------------------------------------------------------
_DIGIT_FONT: dict[str, tuple[int, ...]] = {
    "0": (0b01110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110),
    "1": (0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110),
    "2": (0b01110, 0b10001, 0b00001, 0b00110, 0b01000, 0b10000, 0b11111),
    "3": (0b01110, 0b10001, 0b00001, 0b00110, 0b00001, 0b10001, 0b01110),
    "4": (0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010),
    "5": (0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110),
    "6": (0b00110, 0b01000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110),
    "7": (0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000),
    "8": (0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110),
    "9": (0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00010, 0b01100),
}
_CHAR_W = 5
_CHAR_SPACING = 1


def build_identifier(
    clearing_number: str,
    page_number: int,
    has_pdf417: bool,
) -> str:
    """Build 16-digit CODE128C identifier string.

    Format: 196 + 22 + CCCCC + PPP + D + O + R
      - 196: form number
      - 22: version
      - CCCCC: clearing number, zero-padded to 5
      - PPP: page number, zero-padded to 3
      - D: 1 if page has PDF417 barcodes, else 0
      - O: 0 (always Querformat/landscape per spec)
      - R: 2 (reading direction per working reference)
    """
    clearing = clearing_number.zfill(5)
    page = str(page_number).zfill(3)
    pdf417_flag = "1" if has_pdf417 else "0"
    return f"{FORM_NUMBER}{VERSION}{clearing}{page}{pdf417_flag}02"


def _widths_to_modules(widths: str) -> list[int]:
    """Convert bar/space width string to module bit pattern (1=bar, 0=space)."""
    modules: list[int] = []
    is_bar = True
    for ch in widths:
        modules.extend([1 if is_bar else 0] * int(ch))
        is_bar = not is_bar
    return modules


def _encode(identifier: str) -> list[int]:
    """Encode 16-digit identifier to Code 128C module pattern.

    Returns list of 123 ints (1=bar/black, 0=space/white).
    """
    pairs = [int(identifier[i:i + 2]) for i in range(0, 16, 2)]

    # Checksum: start_value + sum(position * value), positions starting at 1
    checksum = _START_C
    for i, val in enumerate(pairs, 1):
        checksum += i * val
    check_digit = checksum % 103

    modules: list[int] = []
    modules.extend(_widths_to_modules(_PATTERNS[_START_C]))
    for val in pairs:
        modules.extend(_widths_to_modules(_PATTERNS[val]))
    modules.extend(_widths_to_modules(_PATTERNS[check_digit]))
    modules.extend(_widths_to_modules(_PATTERNS[_STOP]))

    return modules


def generate(
    clearing_number: str,
    page_number: int,
    has_pdf417: bool,
) -> Image.Image:
    """Generate CODE128C barcode as 123×46 1-bit PIL Image.

    The image uses mode '1' (1-bit pixels): 0=black, 1=white.
    Matches TrueWealth reference: BPC=1, DeviceGray.
    """
    identifier = build_identifier(clearing_number, page_number, has_pdf417)
    modules = _encode(identifier)

    # Create 1-bit image, white background
    img = Image.new("1", (_WIDTH, _HEIGHT), 1)

    # Draw bars (rows 3-28): each module column is black or white
    bar_end = _QUIET_TOP + _BAR_ROWS
    for x, mod in enumerate(modules):
        if mod:
            for y in range(_QUIET_TOP, bar_end):
                img.putpixel((x, y), 0)

    # Draw human-readable number (rows 38-44) using 5×7 bitmap font
    text_width = len(identifier) * (_CHAR_W + _CHAR_SPACING) - _CHAR_SPACING
    x_start = (_WIDTH - text_width) // 2
    for ci, ch in enumerate(identifier):
        bitmap = _DIGIT_FONT[ch]
        cx = x_start + ci * (_CHAR_W + _CHAR_SPACING)
        for row_idx, row_bits in enumerate(bitmap):
            y = _TEXT_Y + row_idx
            for bit in range(4, -1, -1):
                x = cx + (4 - bit)
                if 0 <= x < _WIDTH and (row_bits >> bit) & 1:
                    img.putpixel((x, y), 0)

    return img
