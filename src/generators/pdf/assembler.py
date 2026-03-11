"""Public API for E-Steuerauszug PDF generation.

Single entry point: TaxStatement -> PDF bytes.
Also: generate_pdf_from_xml() for barcode-only PDF from raw XML (testing).
"""

from __future__ import annotations

import io
import math

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject, NumberObject

from src.generators.barcode.orchestrator import generate_data_barcodes
from src.generators.ech0196.xml_builder import build_statement_id, build_xml, serialize
from src.models import TaxStatement

from .renderer import PdfRenderer

# Barcode image dimensions (pixels) used to identify XObjects
_CODE128C_DIMS = (123, 46)
_PDF417_DIMS = (290, 35)


def _pack_1bit(gray8: bytes, width: int, height: int) -> bytes:
    """Convert 8-bit grayscale pixels to 1-bit packed (MSB-first).

    Threshold: >= 128 → white (1), < 128 → black (0).
    Each row is padded to a byte boundary.
    """
    row_bytes = math.ceil(width / 8)
    out = bytearray(row_bytes * height)
    for y in range(height):
        row_start = y * width
        for x in range(width):
            if gray8[row_start + x] >= 128:
                byte_idx = y * row_bytes + x // 8
                out[byte_idx] |= 0x80 >> (x % 8)
    return bytes(out)


def _fix_barcode_xobjects(writer: PdfWriter) -> None:
    """Fix barcode XObjects: DeviceGray/1-bit colorspace + sequential naming.

    reportlab's drawImage() normalizes PIL mode "1" to RGB BPC=8 and uses
    hash-based names like /FormXob.<hex>. The ZH tax portal requires:
      - DeviceGray BPC=1 (matching TrueWealth)
      - Sequential names like /Im1, /Im2, ... (rejects /FormXob.* names)

    Identifies barcodes by pixel dimensions:
      - 123×46 = CODE128C (RGB → extract R channel → 1-bit)
      - 290×35 = PDF417 (gray8 → 1-bit)
    """
    for page in writer.pages:
        resources = page.get("/Resources")
        if not resources:
            continue
        xobjects = resources.get("/XObject")
        if not xobjects:
            continue
        xobj_dict = xobjects.get_object()

        # Fix colorspace and bit depth
        for name in list(xobj_dict.keys()):
            xobj = xobj_dict[name].get_object()
            if xobj.get("/Subtype") != "/Image":
                continue

            w = int(xobj["/Width"])
            h = int(xobj["/Height"])
            dims = (w, h)

            if dims == _CODE128C_DIMS:
                # RGB (3 bytes/pixel) → extract R channel → 1-bit
                raw = xobj.get_data()
                gray8 = bytes(raw[i] for i in range(0, len(raw), 3))
                packed = _pack_1bit(gray8, w, h)
            elif dims == _PDF417_DIMS:
                # Gray8 (1 byte/pixel) → 1-bit
                raw = xobj.get_data()
                packed = _pack_1bit(raw, w, h)
            else:
                continue

            # Update colorspace and bit depth
            xobj[NameObject("/ColorSpace")] = NameObject("/DeviceGray")
            xobj[NameObject("/BitsPerComponent")] = NumberObject(1)

            # Replace filter with FlateDecode (set_data auto-compresses)
            xobj[NameObject("/Filter")] = NameObject("/FlateDecode")

            # Write 1-bit packed data (pypdf auto-applies FlateDecode)
            xobj.set_data(packed)

        # Rename XObjects to sequential /Im1, /Im2, ... pattern
        # Portal rejects reportlab's /FormXob.<hash> naming convention
        _rename_xobjects(writer, page, xobj_dict)


def _rename_xobjects(writer: PdfWriter, page, xobj_dict) -> None:
    """Rename XObjects from /FormXob.<hash> to /Im1, /Im2, ..."""
    old_names = sorted(xobj_dict.keys())

    # Skip if already using /Im\d+ names
    if all(name.startswith("/Im") and name[3:].isdigit() for name in old_names):
        return

    rename_map: dict[str, str] = {}
    for i, old_name in enumerate(old_names, start=1):
        rename_map[old_name] = f"/Im{i}"

    # Rebuild XObject dictionary with new names
    from pypdf.generic import DictionaryObject

    new_xobj = DictionaryObject()
    for old_name, new_name in rename_map.items():
        # Use raw_get() to preserve IndirectObject references.
        # xobj_dict[name] resolves to the stream object, causing pypdf to
        # inline the entire image dict in the page resources. CoreGraphics
        # rejects PDFs with inlined XObject streams.
        new_xobj[NameObject(new_name)] = xobj_dict.raw_get(old_name)

    resources = page["/Resources"]
    if hasattr(resources, "get_object"):
        resources = resources.get_object()
    resources[NameObject("/XObject")] = new_xobj

    # Update content stream Do references
    # Must get_data() BEFORE changing filter, then set filter, then set_data()
    # to properly re-encode. DecodedStreamObject or raw _data writes produce
    # uncompressed streams that break macOS CoreGraphics rendering.
    contents = page.get("/Contents")
    if not contents:
        return
    contents_obj = contents.get_object() if hasattr(contents, "get_object") else contents

    def _rename_in_stream(stream) -> None:
        """Decompress with original filter, rename, recompress as FlateDecode."""
        # Step 1: decompress with original filter chain (creates decoded_self)
        data = stream.get_data()
        # Step 2: apply renames
        for old_name, new_name in rename_map.items():
            data = data.replace(old_name.encode(), new_name.encode())
        # Step 3: switch to FlateDecode and re-encode
        stream[NameObject("/Filter")] = NameObject("/FlateDecode")
        stream.set_data(data)

    if isinstance(contents_obj, ArrayObject):
        for i in range(len(contents_obj)):
            _rename_in_stream(contents_obj[i].get_object())
    else:
        _rename_in_stream(contents_obj)


def generate_pdf(
    statement: TaxStatement,
    input_filenames: list[str] | None = None,
) -> bytes:
    """Full pipeline: TaxStatement -> XML -> Barcodes -> PDF bytes.

    1. Build XML via build_xml() + serialize()
    2. Generate barcodes via generate_data_barcodes()
    3. Render PDF via PdfRenderer (portrait pages with landscape CTM)
    4. Post-process: /Rotate=90, convert barcodes to DeviceGray/1-bit
    5. Return PDF bytes
    """
    # Build eCH-0196 XML
    root = build_xml(statement)
    xml_string = serialize(root)

    # Generate PDF417 data barcodes
    statement_id = build_statement_id(statement)
    barcode_set = generate_data_barcodes(xml_string, statement_id)

    # Render complete PDF (portrait pages with landscape drawing CTM)
    renderer = PdfRenderer(statement, xml_string, barcode_set, input_filenames=input_filenames)
    pdf_bytes = renderer.render()

    # Post-process with pypdf
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        page[NameObject("/Rotate")] = NumberObject(90)
        writer.add_page(page)

    # Convert barcode XObjects to DeviceGray/1-bit
    _fix_barcode_xobjects(writer)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def generate_pdf_from_xml(
    xml_string: str,
    clearing_number: str,
    file_id_codewords: list[int] | None = None,
    chunk_size: int | None = None,
) -> bytes:
    """Generate barcode-only PDF from raw XML string.

    Used for portal format testing: feed known-good XML through our
    barcode + PDF pipeline to isolate format vs content issues.

    Args:
        xml_string: Full eCH-0196 XML as string.
        clearing_number: Bank clearing number (e.g. "08750").
        file_id_codewords: Optional 4 codewords (0-899) for Macro PDF417 file ID.
            If None, generates random values.
        chunk_size: Optional fixed chunk size for sequential chunking.
            If None, uses even distribution (production default).

    Returns:
        PDF bytes with barcode sheets only (no data pages).
    """
    from lxml import etree
    from PIL import Image as PILImage
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen.canvas import Canvas

    from src.generators.barcode.orchestrator import generate_page_barcode

    from .constants import (
        BARCODES_PER_SHEET,
        CODE128C_PORTRAIT_H,
        CODE128C_PORTRAIT_W,
        CODE128C_PORTRAIT_X,
        CODE128C_PORTRAIT_Y,
        PDF417_PORTRAIT_H,
        PDF417_PORTRAIT_W,
        PDF417_PORTRAIT_X,
        PDF417_SLOT_Y,
        PORTRAIT_WIDTH,
    )

    # Extract statement_id from XML root element's "id" attribute
    root = etree.fromstring(xml_string.encode("utf-8"))
    statement_id = root.get("id", "")

    # Generate PDF417 data barcodes
    barcode_set = generate_data_barcodes(
        xml_string, statement_id, file_id_codewords, chunk_size
    )

    def pil_to_reader(img: PILImage.Image) -> ImageReader:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return ImageReader(buf)

    # Render barcode-only PDF
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=A4)
    page_num = 0

    barcodes = barcode_set.data_barcodes
    num_sheets = barcode_set.num_barcode_sheets

    for sheet_idx in range(num_sheets):
        if page_num > 0:
            c.showPage()
        page_num += 1
        c.setPageSize(A4)

        # Landscape drawing CTM
        c.transform(0, 1, -1, 0, PORTRAIT_WIDTH, 0)

        start = sheet_idx * BARCODES_PER_SHEET
        end = min(start + BARCODES_PER_SHEET, len(barcodes))
        sheet_barcodes = barcodes[start:end]

        # Place PDF417 barcodes in portrait coordinates
        c.saveState()
        c.transform(0, -1, 1, 0, 0, PORTRAIT_WIDTH)  # inverse CTM
        for i, bc_img in enumerate(sheet_barcodes):
            img_reader = pil_to_reader(bc_img)
            c.drawImage(
                img_reader, PDF417_PORTRAIT_X, PDF417_SLOT_Y[i],
                width=PDF417_PORTRAIT_W, height=PDF417_PORTRAIT_H,
            )
        c.restoreState()

        # CODE128C barcode
        code128c_img = generate_page_barcode(
            clearing_number=clearing_number,
            page_number=page_num,
            has_pdf417=True,
        )
        c.saveState()
        c.transform(0, -1, 1, 0, 0, PORTRAIT_WIDTH)  # inverse CTM
        c.drawImage(
            pil_to_reader(code128c_img),
            CODE128C_PORTRAIT_X, CODE128C_PORTRAIT_Y,
            width=CODE128C_PORTRAIT_W, height=CODE128C_PORTRAIT_H,
        )
        c.restoreState()

    c.save()
    raw_pdf = buf.getvalue()

    # Post-process: /Rotate=90 + DeviceGray/1-bit barcodes
    reader = PdfReader(io.BytesIO(raw_pdf))
    writer = PdfWriter()
    for page in reader.pages:
        page[NameObject("/Rotate")] = NumberObject(90)
        writer.add_page(page)

    _fix_barcode_xobjects(writer)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
