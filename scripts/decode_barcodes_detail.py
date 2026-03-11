#!/usr/bin/env python3
"""Additional detail: full hex dumps, segment reassembly, zlib inflate test."""

import sys
import io
import zlib
from pathlib import Path

PDF_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/e-steuerauszug.pdf")

import warnings
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from pypdf import PdfReader

from PIL import Image
import zxingcpp


def extract_image_from_xobject(xobj):
    width = xobj["/Width"]
    height = xobj["/Height"]
    bpc = xobj.get("/BitsPerComponent", 8)
    color_space = str(xobj.get("/ColorSpace", "/DeviceGray"))
    data = xobj.get_data()

    if "Gray" in color_space:
        if bpc == 1:
            return Image.frombytes("1", (width, height), data)
        return Image.frombytes("L", (width, height), data)
    elif "RGB" in color_space:
        expected = width * height * 3
        if len(data) == expected:
            return Image.frombytes("RGB", (width, height), data)
    return None


reader = PdfReader(str(PDF_PATH))

# Collect all PDF417 segments
pdf417_segments = []
code128_values = []

for page_idx, page in enumerate(reader.pages):
    resources = page.get("/Resources", {})
    xobjects = resources.get("/XObject", {})

    for name, xobj_ref in xobjects.items():
        xobj = xobj_ref.get_object()
        if str(xobj.get("/Subtype", "")) != "/Image":
            continue

        w = xobj.get("/Width", 0)
        h = xobj.get("/Height", 0)

        img = extract_image_from_xobject(xobj)
        if img is None:
            continue

        if img.mode == "1":
            img = img.convert("L")

        results = zxingcpp.read_barcodes(img, return_errors=True)
        for r in results:
            if r.format == zxingcpp.BarcodeFormat.PDF417:
                pdf417_segments.append(r)
            elif r.format == zxingcpp.BarcodeFormat.Code128:
                code128_values.append(r)


print("=" * 100)
print("CODE128C BARCODES - COMPLETE DATA")
print("=" * 100)

for i, r in enumerate(code128_values):
    print(f"\n  CODE128C #{i+1}:")
    print(f"    text: {r.text!r}")
    print(f"    bytes (hex): {r.bytes.hex()}")
    print(f"    bytes (ascii): {r.bytes}")
    print(f"    symbology_identifier: {r.symbology_identifier!r}")
    print(f"    valid: {r.valid}")

print()
print("=" * 100)
print("PDF417 BARCODES - COMPLETE DATA")
print("=" * 100)

for i, r in enumerate(pdf417_segments):
    raw = r.bytes
    print(f"\n  PDF417 Segment #{i+1}:")
    print(f"    content_type: {r.content_type}")
    print(f"    ec_level: {r.ec_level!r}")
    print(f"    symbology_identifier: {r.symbology_identifier!r}")
    print(f"    valid: {r.valid}")
    print(f"    extra: {r.extra}")
    print(f"    bytes length: {len(raw)}")
    print(f"    FULL HEX DUMP:")
    # Print in 32-byte rows
    for offset in range(0, len(raw), 32):
        chunk = raw[offset:offset+32]
        hex_str = chunk.hex()
        # Format with spaces every 2 chars for readability
        hex_formatted = " ".join(hex_str[j:j+2] for j in range(0, len(hex_str), 2))
        print(f"      {offset:04d}: {hex_formatted}")


# Reassemble all segments
print()
print("=" * 100)
print("REASSEMBLED PDF417 DATA")
print("=" * 100)

all_bytes = b""
for r in pdf417_segments:
    all_bytes += r.bytes

print(f"Total reassembled length: {len(all_bytes)} bytes")
print(f"First 4 bytes hex: {all_bytes[:4].hex()}")

# Check if any segment starts with zlib magic
print()
print("Checking for zlib deflate signatures (78 9C, 78 DA, 78 01):")
for i, r in enumerate(pdf417_segments):
    raw = r.bytes
    magic = raw[:2].hex()
    is_zlib = magic in ("789c", "78da", "7801")
    print(f"  Segment #{i+1}: first 2 bytes = {magic} {'<-- ZLIB!' if is_zlib else ''}")

# Try zlib decompress on last segment (often contains start of deflate stream)
# Actually, let's try the reassembled data
print()
print("Attempting zlib decompress on reassembled data...")
try:
    decompressed = zlib.decompress(all_bytes)
    print(f"SUCCESS! Decompressed {len(all_bytes)} -> {len(decompressed)} bytes")
    print(f"First 500 chars of decompressed data:")
    print(decompressed[:500].decode("utf-8", errors="replace"))
except Exception as e:
    print(f"Failed: {e}")

# Try each segment individually
print()
print("Attempting zlib decompress on each individual segment...")
for i, r in enumerate(pdf417_segments):
    raw = r.bytes
    try:
        decompressed = zlib.decompress(raw)
        print(f"  Segment #{i+1}: SUCCESS! {len(raw)} -> {len(decompressed)} bytes")
        print(f"    First 200 chars: {decompressed[:200].decode('utf-8', errors='replace')}")
    except Exception as e:
        print(f"  Segment #{i+1}: Failed ({e})")

# The eCH-0196 spec says the XML is zlib-compressed, split across segments.
# The last segment (index 3) starts with 78 da which is zlib.
# But segments are ordered by segment index in the Macro PDF417.
# Let's check the extra dict for segment ordering info
print()
print("=" * 100)
print("SEGMENT ORDERING ANALYSIS")
print("=" * 100)
for i, r in enumerate(pdf417_segments):
    print(f"  Segment #{i+1}: extra={r.extra}")
    print(f"    FileId: {r.extra.get('FileId', 'N/A') if r.extra else 'N/A'}")
    print(f"    FileName: {r.extra.get('FileName', 'N/A') if r.extra else 'N/A'}")
    # Check if there's segment index info in extra
    if r.extra:
        for k, v in r.extra.items():
            print(f"    {k}: {v!r}")

# Try: maybe segment 4 (index 3) which starts with 78da contains the full compressed XML
print()
print("=" * 100)
print("SEGMENT 4 (starts with 78 DA) ZLIB TEST")
print("=" * 100)
if len(pdf417_segments) >= 4:
    seg4 = pdf417_segments[3].bytes
    print(f"Segment 4 length: {len(seg4)} bytes")
    print(f"First 8 bytes: {seg4[:8].hex()}")
    try:
        d = zlib.decompress(seg4)
        print(f"DECOMPRESSED: {len(d)} bytes")
        text = d.decode("utf-8", errors="replace")
        print("--- DECOMPRESSED XML ---")
        print(text)
        print("--- END ---")
    except Exception as e:
        print(f"Failed: {e}")
