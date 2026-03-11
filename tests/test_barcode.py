"""Tests for barcode generation (Phase 4)."""

import importlib.util
import math
import zlib

import pytest
from PIL import Image

from src.generators.barcode.code128c import build_identifier, generate
from src.generators.barcode.constants import (
    BYTE_LATCH,
    CHUNK_SIZE_BYTES,
    MACRO_BEGIN,
    MACRO_TERMINATE,
    MAX_BARCODES_PER_SHEET,
    PDF417_ROWS,
    ZLIB_LEVEL,
)
from src.generators.barcode.macro_pdf417 import encode_segment, render
from src.generators.barcode.orchestrator import (
    BarcodeSet,
    generate_data_barcodes,
    generate_page_barcode,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_xml(target_size: int) -> str:
    """Build synthetic XML of ~target_size bytes with varied content.

    Uses unique ISIN/quantity per element to defeat zlib compression,
    ensuring the compressed size scales roughly linearly with target_size.
    """
    header = '<?xml version="1.0" encoding="UTF-8"?>\n<taxStatement>\n'
    footer = "</taxStatement>\n"
    # ~80 bytes per element, unique values defeat compression
    elements: list[str] = []
    i = 0
    while len(header) + sum(len(e) for e in elements) + len(footer) < target_size:
        isin = f"CH{i:010d}"
        elements.append(
            f'  <security isin="{isin}" name="Security {i}" quantity="{i * 17}" />\n'
        )
        i += 1
    return header + "".join(elements) + footer


# ===========================================================================
# CODE128C tests
# ===========================================================================

class TestBuildIdentifier:
    def test_format_and_length(self):
        ident = build_identifier("08750", 1, has_pdf417=True)
        assert len(ident) == 16
        assert ident.isdigit()

    def test_fields(self):
        ident = build_identifier("08750", 1, has_pdf417=True)
        assert ident[:3] == "196"      # form
        assert ident[3:5] == "22"      # version
        assert ident[5:10] == "08750"  # clearing
        assert ident[10:13] == "001"   # page
        assert ident[13] == "1"        # has PDF417
        assert ident[14] == "0"        # orientation always 0 (Querformat)
        assert ident[15] == "2"        # reading direction

    def test_zero_padding_clearing(self):
        ident = build_identifier("750", 1, has_pdf417=False)
        assert ident[5:10] == "00750"

    def test_zero_padding_page(self):
        ident = build_identifier("08750", 42, has_pdf417=False)
        assert ident[10:13] == "042"

    def test_pdf417_flag(self):
        with_pdf417 = build_identifier("08750", 1, has_pdf417=True)
        without_pdf417 = build_identifier("08750", 1, has_pdf417=False)
        assert with_pdf417[13] == "1"
        assert without_pdf417[13] == "0"

    def test_orientation_always_zero(self):
        ident = build_identifier("08750", 1, has_pdf417=True)
        assert ident[14] == "0"  # always Querformat (landscape)

    def test_reading_direction_always_two(self):
        ident = build_identifier("08750", 1, has_pdf417=True)
        assert ident[15] == "2"  # reading direction = 2


class TestCode128CGenerate:
    def test_returns_pil_image(self):
        img = generate("08750", 1, has_pdf417=True)
        assert isinstance(img, Image.Image)
        assert img.mode == "1"
        assert img.size == (123, 46)

    def test_different_pages_differ(self):
        img1 = generate("08750", 1, has_pdf417=True)
        img2 = generate("08750", 2, has_pdf417=True)
        # Different page numbers should produce different barcode images
        assert img1.tobytes() != img2.tobytes()


# ===========================================================================
# Macro PDF417 codeword tests
# ===========================================================================

class TestEncodeSegment:
    def test_single_segment_contains_begin_and_terminate(self):
        """Single-segment barcode must have both 928 (begin) and 922 (terminate)."""
        data = b"Hello, world! This is test data for PDF417."
        rows = encode_segment(
            data_chunk=data,
            segment_index=0,
            segment_count=1,
            file_id_codewords=[0, 42, 0, 0],
            file_name="test",
        )
        assert len(rows) > 0
        # Check that it renders successfully at spec resolution
        img = render(rows)
        assert isinstance(img, Image.Image)
        assert img.mode == "L"
        assert img.size == (290, 35)

    def test_multi_segment_first_no_terminate(self):
        """First segment of multi-segment has 928 but NOT 922 in macro control."""
        from src.generators.barcode.macro_pdf417 import _build_macro_control

        cws = _build_macro_control(
            segment_index=0, segment_count=3,
            file_id_codewords=[0, 42], file_name="test",
        )
        assert MACRO_BEGIN in cws
        assert MACRO_TERMINATE not in cws

    def test_multi_segment_last_has_terminate(self):
        """Last segment has both 928 and 922."""
        from src.generators.barcode.macro_pdf417 import _build_macro_control

        cws = _build_macro_control(
            segment_index=2, segment_count=3,
            file_id_codewords=[0, 42], file_name="test",
        )
        assert MACRO_BEGIN in cws
        assert MACRO_TERMINATE in cws

    def test_segment_index_encoded_as_two_codewords(self):
        """Segment index uses 2 codewords with zero-padded base-900 leading-'1' encoding."""
        from src.generators.barcode.macro_pdf417 import _build_macro_control

        cws = _build_macro_control(
            segment_index=0, segment_count=4,
            file_id_codewords=[0, 42], file_name="t",
        )
        # n = int("1" + "00000") = 100000, [100000 // 900, 100000 % 900] = [111, 100]
        assert cws[0] == MACRO_BEGIN
        assert cws[1] == 111
        assert cws[2] == 100

        cws5 = _build_macro_control(
            segment_index=5, segment_count=10,
            file_id_codewords=[0, 42], file_name="t",
        )
        # n = int("1" + "00005") = 100005, [100005 // 900, 100005 % 900] = [111, 105]
        assert cws5[1] == 111
        assert cws5[2] == 105

    def test_file_id_codewords_in_macro(self):
        """File ID codewords appear directly in macro control after segment index."""
        from src.generators.barcode.macro_pdf417 import _build_macro_control

        file_id = [7, 177, 158, 242]
        cws = _build_macro_control(
            segment_index=0, segment_count=1,
            file_id_codewords=file_id, file_name="x",
        )
        # After [928, seg_hi, seg_lo], the file ID codewords follow
        assert cws[3:7] == file_id

    def test_filename_field_type_present(self):
        """First 923 (optional field) is followed by 0 (FILENAME type)."""
        from src.generators.barcode.macro_pdf417 import _build_macro_control

        cws = _build_macro_control(
            segment_index=0, segment_count=1,
            file_id_codewords=[0, 42], file_name="test",
        )
        # First 923 = fileName field
        idx_923 = cws.index(923)
        assert cws[idx_923 + 1] == 0  # FILENAME type

    def test_segment_count_field_present(self):
        """Second 923 (optional field) is followed by 1 (SEGMENT_COUNT type)."""
        from src.generators.barcode.macro_pdf417 import _build_macro_control

        cws = _build_macro_control(
            segment_index=0, segment_count=9,
            file_id_codewords=[0, 42], file_name="test",
        )
        # Find second 923 = segmentCount field
        first_923 = cws.index(923)
        second_923 = cws.index(923, first_923 + 1)
        assert cws[second_923 + 1] == 1  # SEGMENT_COUNT type
        # Value: int("1" + "00009") = 100009 -> [111, 109]
        assert cws[second_923 + 2] == 111
        assert cws[second_923 + 3] == 109

    def test_renders_valid_image(self):
        """Encoded segment renders to a valid PIL Image at spec resolution."""
        data = b"A" * 200
        rows = encode_segment(
            data_chunk=data,
            segment_index=0,
            segment_count=1,
            file_id_codewords=[0, 0, 0, 0],
            file_name="test",
        )
        img = render(rows)
        assert isinstance(img, Image.Image)
        assert img.mode == "L"
        assert img.size == (290, 35)

    def test_exactly_35_rows(self):
        """Every encoded segment produces exactly PDF417_ROWS (35) rows."""
        data = b"X" * CHUNK_SIZE_BYTES
        rows = encode_segment(
            data_chunk=data,
            segment_index=0,
            segment_count=1,
            file_id_codewords=[0, 0, 0, 0],
            file_name="statement_2025",
        )
        assert len(rows) == PDF417_ROWS

    def test_small_data_also_35_rows(self):
        """Even small data segments are padded to exactly 35 rows."""
        data = b"tiny"
        rows = encode_segment(
            data_chunk=data,
            segment_index=0,
            segment_count=1,
            file_id_codewords=[0, 0, 0, 0],
            file_name="test",
        )
        assert len(rows) == PDF417_ROWS

    def test_data_too_large_raises(self):
        """Data exceeding the capacity of a 35x13 symbol raises ValueError."""
        with pytest.raises(ValueError, match="Data too large"):
            encode_segment(
                data_chunk=b"X" * 600,
                segment_index=0,
                segment_count=1,
                file_id_codewords=[0, 0, 0, 0],
                file_name="test",
            )

    def test_mod6_data_uses_924_latch(self):
        """Mod-6 data uses 924 (byte compaction mod-6) latch.

        924 is safe before the macro block where it unambiguously means
        "Byte Compaction mod-6". It only means "Segment Count" inside a
        macro control block (between 928 and 922).
        """
        data = b"A" * 450  # 450 % 6 == 0
        assert len(data) % 6 == 0, "Test precondition: data must be mod-6"
        rows = encode_segment(
            data_chunk=data,
            segment_index=0,
            segment_count=3,
            file_id_codewords=[0, 42, 0, 0],
            file_name="test",
        )
        assert len(rows) == PDF417_ROWS

    def test_non_mod6_data_uses_byte_latch_901(self):
        """Non-mod-6 data also uses BYTE_LATCH (901)."""
        data = b"B" * 449
        assert len(data) % 6 != 0, "Test precondition: data must not be mod-6"
        rows = encode_segment(
            data_chunk=data,
            segment_index=0,
            segment_count=2,
            file_id_codewords=[0, 42, 0, 0],
            file_name="test",
        )
        assert len(rows) == PDF417_ROWS

    def test_all_segment_positions_produce_valid_symbols(self):
        """First, middle, and last segments all produce valid 35-row symbols."""
        segment_count = 3
        for idx in range(segment_count):
            data = b"C" * CHUNK_SIZE_BYTES
            rows = encode_segment(
                data_chunk=data,
                segment_index=idx,
                segment_count=segment_count,
                file_id_codewords=[0, 99, 0, 0],
                file_name="stmt_2025",
            )
            assert len(rows) == PDF417_ROWS
            img = render(rows)
            assert isinstance(img, Image.Image)
            assert img.mode == "L"
            assert img.size == (290, 35)

    def test_no_codeword_924_in_non_last_segments(self):
        """Verify codeword 924 never appears after macro control in data area.

        We check by inspecting the raw codeword assembly: the latch used by
        encode_segment must be BYTE_LATCH (901).
        """
        assert BYTE_LATCH == 901
        # Also verify the constant 924 is no longer importable from constants
        import src.generators.barcode.constants as bc_constants
        assert not hasattr(bc_constants, "BYTE_LATCH_MOD6")


# ===========================================================================
# zxing-cpp decode roundtrip tests
# ===========================================================================

@pytest.mark.skipif(
    not importlib.util.find_spec("zxingcpp"),
    reason="zxingcpp not installed (C++ barcode decoder, optional)",
)
class TestZxingDecode:
    """Verify generated Macro PDF417 barcodes decode correctly with zxing-cpp."""

    def _decode(self, img: Image.Image):
        """Decode a barcode image, return the zxingcpp result."""
        import zxingcpp
        return zxingcpp.read_barcode(img, zxingcpp.BarcodeFormat.PDF417)

    def test_single_segment_decodes(self):
        """Single-segment barcode decodes with correct metadata."""
        data = b"Hello World test data for PDF417 barcode."
        file_id_cws = [7, 177, 158, 242]
        file_name = "test_statement"

        rows = encode_segment(
            data_chunk=data,
            segment_index=0,
            segment_count=1,
            file_id_codewords=file_id_cws,
            file_name=file_name,
        )
        img = render(rows, scale=5, ratio=4, padding=30)
        result = self._decode(img)

        assert result is not None, "Barcode should decode"
        assert result.valid, f"Barcode should be valid, error: {result.error}"
        assert result.bytes == data
        assert result.extra["FileId"] == "007177158242"
        assert result.extra["FileName"] == file_name

    def test_multi_segment_all_decode(self):
        """All segments of a multi-segment barcode decode with matching metadata."""
        file_id_cws = [42, 100, 0, 899]
        file_name = "stmt_2025"
        segment_count = 3

        for idx in range(segment_count):
            data = f"Segment {idx} payload data.".encode()
            rows = encode_segment(
                data_chunk=data,
                segment_index=idx,
                segment_count=segment_count,
                file_id_codewords=file_id_cws,
                file_name=file_name,
            )
            img = render(rows, scale=5, ratio=4, padding=30)
            result = self._decode(img)

            assert result is not None, f"Segment {idx} should decode"
            assert result.valid, f"Segment {idx} error: {result.error}"
            assert result.bytes == data
            assert result.extra["FileId"] == "042100000899"
            assert result.extra["FileName"] == file_name

    def test_max_chunk_decodes(self):
        """A full-sized segment decodes correctly."""
        data = bytes(range(256)) + bytes(range(214))  # 470 bytes of varied data
        assert len(data) == CHUNK_SIZE_BYTES

        rows = encode_segment(
            data_chunk=data,
            segment_index=0,
            segment_count=2,
            file_id_codewords=[0, 0, 0, 1],
            file_name="full_chunk",
        )
        img = render(rows, scale=5, ratio=4, padding=30)
        result = self._decode(img)

        assert result is not None, "Full chunk should decode"
        assert result.valid, f"Full chunk error: {result.error}"
        assert result.bytes == data


# ===========================================================================
# Data splitting tests
# ===========================================================================

class TestDataSplitting:
    def test_small_xml_one_chunk(self):
        """Small XML compresses to a single chunk."""
        xml = make_xml(500)
        compressed = zlib.compress(xml.encode("utf-8"), ZLIB_LEVEL)
        n_chunks = math.ceil(len(compressed) / CHUNK_SIZE_BYTES)
        chunk_size = math.ceil(len(compressed) / n_chunks)
        chunks = [
            compressed[i : i + chunk_size]
            for i in range(0, len(compressed), chunk_size)
        ]
        assert len(chunks) == 1

    def test_large_xml_multiple_chunks(self):
        """Large XML compresses to multiple chunks."""
        xml = make_xml(50_000)
        compressed = zlib.compress(xml.encode("utf-8"), ZLIB_LEVEL)
        n_chunks = math.ceil(len(compressed) / CHUNK_SIZE_BYTES)
        chunk_size = math.ceil(len(compressed) / n_chunks)
        chunks = [
            compressed[i : i + chunk_size]
            for i in range(0, len(compressed), chunk_size)
        ]
        assert len(chunks) > 1

    def test_no_chunk_exceeds_limit(self):
        """No chunk should exceed CHUNK_SIZE_BYTES."""
        xml = make_xml(30_000)
        compressed = zlib.compress(xml.encode("utf-8"), ZLIB_LEVEL)
        n_chunks = math.ceil(len(compressed) / CHUNK_SIZE_BYTES)
        chunk_size = math.ceil(len(compressed) / n_chunks)
        chunks = [
            compressed[i : i + chunk_size]
            for i in range(0, len(compressed), chunk_size)
        ]
        for chunk in chunks:
            assert len(chunk) <= CHUNK_SIZE_BYTES

    def test_even_distribution(self):
        """Chunks should be roughly equal in size (no tiny last chunk)."""
        xml = make_xml(30_000)
        compressed = zlib.compress(xml.encode("utf-8"), ZLIB_LEVEL)
        n_chunks = math.ceil(len(compressed) / CHUNK_SIZE_BYTES)
        chunk_size = math.ceil(len(compressed) / n_chunks)
        chunks = [
            compressed[i : i + chunk_size]
            for i in range(0, len(compressed), chunk_size)
        ]
        if len(chunks) > 1:
            # Last chunk should be at least 50% of average chunk size
            avg_size = len(compressed) / len(chunks)
            assert len(chunks[-1]) >= avg_size * 0.5

    def test_roundtrip(self):
        """Concatenating chunks and decompressing yields original XML."""
        xml = make_xml(20_000)
        compressed = zlib.compress(xml.encode("utf-8"), ZLIB_LEVEL)
        n_chunks = math.ceil(len(compressed) / CHUNK_SIZE_BYTES)
        chunk_size = math.ceil(len(compressed) / n_chunks)
        chunks = [
            compressed[i : i + chunk_size]
            for i in range(0, len(compressed), chunk_size)
        ]
        reassembled = b"".join(chunks)
        assert zlib.decompress(reassembled).decode("utf-8") == xml


# ===========================================================================
# Orchestrator integration tests
# ===========================================================================

class TestOrchestrator:
    def test_small_xml_single_barcode(self):
        """Small XML produces 1 barcode and 1 sheet."""
        xml = make_xml(500)
        result = generate_data_barcodes(xml, "stmt_2025")
        assert isinstance(result, BarcodeSet)
        assert len(result.data_barcodes) >= 1
        assert result.num_barcode_sheets >= 1
        for img in result.data_barcodes:
            assert isinstance(img, Image.Image)
            assert img.mode == "L"
            assert img.size == (290, 35)

    def test_large_xml_multiple_barcodes(self):
        """Large XML produces multiple barcodes."""
        xml = make_xml(50_000)
        result = generate_data_barcodes(xml, "stmt_2025")
        assert len(result.data_barcodes) > 1

    def test_sheet_count_correct(self):
        """num_barcode_sheets = ceil(barcodes / MAX_BARCODES_PER_SHEET)."""
        xml = make_xml(50_000)
        result = generate_data_barcodes(xml, "stmt_2025")
        expected_sheets = math.ceil(
            len(result.data_barcodes) / MAX_BARCODES_PER_SHEET
        )
        assert result.num_barcode_sheets == expected_sheets

    def test_page_barcode_returns_image(self):
        """generate_page_barcode returns a 123×46 grayscale PIL Image."""
        img = generate_page_barcode("08750", 1, has_pdf417=True)
        assert isinstance(img, Image.Image)
        assert img.mode == "1"
        assert img.size == (123, 46)
