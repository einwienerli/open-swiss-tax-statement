"""Barcode orchestrator: XML → compressed chunks → Macro PDF417 images."""

import logging
import math
import random
import zlib
from dataclasses import dataclass

from PIL import Image

from .code128c import generate as generate_code128c
from .constants import CHUNK_SIZE_BYTES, MAX_BARCODES_PER_SHEET, ZLIB_LEVEL
from .macro_pdf417 import encode_segment, render

logger = logging.getLogger(__name__)


@dataclass
class BarcodeSet:
    """Collection of PDF417 Macro barcode images for the data sheets."""

    data_barcodes: list[Image.Image]
    num_barcode_sheets: int


def generate_data_barcodes(
    xml_string: str,
    statement_id: str,
    file_id_codewords: list[int] | None = None,
    chunk_size: int | None = None,
) -> BarcodeSet:
    """XML → zlib compress → chunk → Macro PDF417 images.

    Args:
        xml_string: Full eCH-0196 XML as string.
        statement_id: Unique statement identifier (used as Macro fileName).
        file_id_codewords: Optional 4 codewords (0-899) for Macro PDF417 file ID.
            If None, generates random values.
        chunk_size: Optional fixed chunk size in bytes. If provided, uses
            sequential chunking (last chunk may be smaller). If None, uses
            even distribution with max CHUNK_SIZE_BYTES.

    Returns:
        BarcodeSet with rendered PDF417 images and sheet count.
    """
    # Compress
    xml_bytes = xml_string.encode("utf-8")
    compressed = zlib.compress(xml_bytes, ZLIB_LEVEL)
    logger.info("Compressed XML: %d bytes -> %d bytes (%.0f%%)",
                len(xml_bytes), len(compressed),
                len(compressed) / len(xml_bytes) * 100)

    # Sequential chunking at fixed size (matching TrueWealth/J4L).
    # CHUNK_SIZE_BYTES=470 is the exact maximum that fits in a 35x13 symbol
    # with EC4 + full macro block (including segmentCount field) for
    # non-last segments. The last segment also includes the terminate
    # marker (922), requiring 1 fewer byte of data capacity.
    size = chunk_size if chunk_size is not None else CHUNK_SIZE_BYTES
    data_chunks = [
        compressed[i : i + size]
        for i in range(0, len(compressed), size)
    ]

    # Last segment includes terminate marker (1 extra codeword).
    # If last chunk is at full capacity, split off 1 byte to a new segment.
    if len(data_chunks[-1]) == size:
        last = data_chunks[-1]
        data_chunks[-1] = last[: size - 1]
        data_chunks.append(last[size - 1 :])

    segment_count = len(data_chunks)
    if file_id_codewords is None:
        file_id_codewords = [random.randint(0, 899) for _ in range(4)]

    # Generate each Macro PDF417 segment
    images: list[Image.Image] = []
    for idx, chunk in enumerate(data_chunks):
        encoded_rows = encode_segment(
            data_chunk=chunk,
            segment_index=idx,
            segment_count=segment_count,
            file_id_codewords=file_id_codewords,
            file_name=statement_id,
        )
        images.append(render(encoded_rows))

    num_sheets = math.ceil(len(images) / MAX_BARCODES_PER_SHEET)
    logger.info("Generated %d barcode segments across %d sheets",
                len(images), num_sheets)
    return BarcodeSet(data_barcodes=images, num_barcode_sheets=num_sheets)


def generate_page_barcode(
    clearing_number: str,
    page_number: int,
    has_pdf417: bool,
) -> Image.Image:
    """Generate one CODE128C page barcode."""
    return generate_code128c(clearing_number, page_number, has_pdf417)
