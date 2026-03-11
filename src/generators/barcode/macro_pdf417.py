"""Macro PDF417 barcode generation with structured append.

Bypasses pdf417gen.encode() to inject Macro control codewords (ISO 15438),
while reusing pdf417gen's low-level compaction, error correction, and rendering.
"""

from pdf417gen.compaction.byte import compact_bytes
from pdf417gen.compaction.text import compact_text
from pdf417gen.encoding import encode_rows
from pdf417gen.error_correction import compute_error_correction_code_words
from pdf417gen.rendering import render_image
from pdf417gen.util import chunks
from PIL import Image

from .constants import (
    BYTE_LATCH,
    MACRO_BEGIN,
    MACRO_FILENAME,
    MACRO_FILENAME_FIELD_TYPE,
    MACRO_SEGMENT_COUNT_FIELD_TYPE,
    MACRO_TERMINATE,
    PDF417_COLUMNS,
    PDF417_ROWS,
    PDF417_SECURITY_LEVEL,
    SEGMENT_INDEX_PAD_WIDTH,
)

PADDING_CODEWORD = 900


def _encode_base900_padded(value: int) -> list[int]:
    """Encode an integer as 2 base-900 codewords with zero-padded leading '1' prefix.

    TrueWealth/J4L convention: zero-pad the value to SEGMENT_INDEX_PAD_WIDTH digits,
    prepend '1', then base-900 encode. This produces [111, 100+value] for small values,
    which ZXing decodes by stripping the leading '1'.

    Example: value=0 -> int("100000") = 100000 -> [111, 100]
    Example: value=8 -> int("100008") = 100008 -> [111, 108]
    """
    n = int("1" + str(value).zfill(SEGMENT_INDEX_PAD_WIDTH))
    return [n // 900, n % 900]


def _build_macro_control(
    segment_index: int,
    segment_count: int,
    file_id_codewords: list[int],
    file_name: str,
) -> list[int]:
    """Build Macro PDF417 control codewords per ISO 15438 §5.5.

    Segment index: 2 codewords, base-900 encoded with leading '1' prefix,
    zero-padded to 5 digits (matching TrueWealth/J4L convention).

    File ID: list of codewords (spec: 4), each decoded as %03d and concatenated.

    fileName field: 923 + field type 0 (FILENAME) + text-compacted name.

    segmentCount field: 923 + field type 1 (SEGMENT_COUNT) + 2 base-900 codewords.
    Included on every segment (matches TrueWealth/J4L).

    Returns: [928, seg_hi, seg_lo, *file_id, 923, 0, text_cws...,
              923, 1, count_hi, count_lo, (922 if last)]
    """
    cws: list[int] = [MACRO_BEGIN]

    # Segment index: 2 codewords, base-900 with leading '1' prefix, zero-padded
    cws.extend(_encode_base900_padded(segment_index))

    # File ID: each codeword becomes 3-digit zero-padded string when decoded
    cws.extend(file_id_codewords)

    # Optional field: fileName (type 0)
    cws.append(MACRO_FILENAME)  # 923
    cws.append(MACRO_FILENAME_FIELD_TYPE)  # 0 = FILENAME
    text_cws = list(compact_text(file_name.encode("ascii")))
    cws.extend(text_cws)

    # Optional field: segmentCount (type 1) — included on every segment
    cws.append(MACRO_FILENAME)  # 923 = optional field indicator
    cws.append(MACRO_SEGMENT_COUNT_FIELD_TYPE)  # 1 = SEGMENT_COUNT
    cws.extend(_encode_base900_padded(segment_count))

    # Terminate marker on last segment only
    if segment_index == segment_count - 1:
        cws.append(MACRO_TERMINATE)

    return cws


def encode_segment(
    data_chunk: bytes,
    segment_index: int,
    segment_count: int,
    file_id_codewords: list[int],
    file_name: str,
    columns: int = PDF417_COLUMNS,
    security_level: int = PDF417_SECURITY_LEVEL,
    rows: int = PDF417_ROWS,
) -> list[list[int]]:
    """Encode one Macro PDF417 symbol. Returns encoded rows for render_image().

    Every symbol is padded to exactly `rows` rows (spec: 35 rows).

    Codeword layout: [LD] + [byte_data] + [padding] + [macro_control]

    Macro control block is placed at the END of the data area, with
    padding (900s) filling the gap between byte data and macro block.
    This matches TrueWealth / J4L Barcode and is required for ZH tax
    portal compatibility. With chunk_size=470 and the segmentCount
    optional field, full barcodes have exactly zero padding.

    Byte latch: uses 924 (mod-6) when data length is divisible by 6, and
    901 otherwise. The latch appears before the macro block, where 924
    unambiguously means "Byte Compaction mod-6".
    """
    # Step 1: Byte-compact the chunk data, choose correct latch
    byte_cws = list(compact_bytes(data_chunk))
    latch = 924 if len(data_chunk) % 6 == 0 else BYTE_LATCH
    byte_section = [latch] + byte_cws

    # Step 2: Build macro control block
    macro_control = _build_macro_control(
        segment_index, segment_count, file_id_codewords, file_name
    )

    # Step 3: Calculate EC count and pad to fill exactly `rows` rows
    ec_count = 2 ** (security_level + 1)
    target_data_cws = rows * columns - ec_count  # total data slots available
    meaningful_cws = len(byte_section) + len(macro_control)
    # target_data_cws includes: 1 LD + byte_section + padding + macro_control
    padding_count = target_data_cws - meaningful_cws - 1  # -1 for LD
    if padding_count < 0:
        raise ValueError(
            f"Data too large for {rows}x{columns} symbol with EC level {security_level}: "
            f"need {meaningful_cws + 1} data codewords but only {target_data_cws} available"
        )
    padding = [PADDING_CODEWORD] * padding_count

    # Step 4: Length descriptor = total data codewords in this symbol
    length_descriptor = target_data_cws

    # Step 5: Build extended codeword sequence
    # Layout: [LD] + [byte_data] + [padding] + [macro_control]
    extended = [length_descriptor] + byte_section + padding + macro_control

    # Step 6: Compute error correction
    ec_words = compute_error_correction_code_words(extended, security_level)

    # Step 7: Build final sequence and split into rows
    final = extended + ec_words
    row_list = list(chunks(final, columns))

    # Step 8: Encode rows with start/stop patterns and indicators
    return list(encode_rows(row_list, columns, security_level))


def render(
    encoded_rows: list[list[int]],
    scale: int = 1,
    ratio: int = 1,
    padding: int = 0,
) -> Image.Image:
    """Render encoded PDF417 rows to PIL Image (grayscale, 290x35 at defaults)."""
    return render_image(encoded_rows, scale=scale, ratio=ratio, padding=padding).convert("L")
