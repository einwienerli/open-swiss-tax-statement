#!/usr/bin/env python3
"""Try all permutations of segment ordering for zlib decompression."""

import sys
import zlib
import itertools
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

# Collect all PDF417 segments as raw bytes
segments = []
for page_idx, page in enumerate(reader.pages):
    resources = page.get("/Resources", {})
    xobjects = resources.get("/XObject", {})
    for name, xobj_ref in xobjects.items():
        xobj = xobj_ref.get_object()
        if str(xobj.get("/Subtype", "")) != "/Image":
            continue
        w = xobj.get("/Width", 0)
        h = xobj.get("/Height", 0)
        if not (280 <= w <= 300 and 30 <= h <= 40):
            continue
        img = extract_image_from_xobject(xobj)
        if img is None:
            continue
        if img.mode == "1":
            img = img.convert("L")
        results = zxingcpp.read_barcodes(img, return_errors=True)
        for r in results:
            if r.format == zxingcpp.BarcodeFormat.PDF417:
                segments.append(r.bytes)

print(f"Found {len(segments)} PDF417 segments")
for i, s in enumerate(segments):
    print(f"  Segment {i}: {len(s)} bytes, first 4 = {s[:4].hex()}")

print()
print("=" * 100)
print("TRYING ALL PERMUTATIONS OF SEGMENT ORDERING FOR ZLIB DECOMPRESS")
print("=" * 100)

for perm in itertools.permutations(range(len(segments))):
    assembled = b""
    for idx in perm:
        assembled += segments[idx]
    try:
        decompressed = zlib.decompress(assembled)
        print(f"\nSUCCESS with ordering: {perm}")
        print(f"  Assembled: {len(assembled)} bytes -> Decompressed: {len(decompressed)} bytes")
        text = decompressed.decode("utf-8", errors="replace")
        print(f"  First 500 chars:")
        print(text[:500])
        print(f"  Last 200 chars:")
        print(text[-200:])
        break
    except Exception as e:
        # Only print zlib header matches
        if assembled[:2] in (b'\x78\x9c', b'\x78\xda', b'\x78\x01'):
            print(f"  Ordering {perm} (starts with zlib header): {e}")

print()
print("=" * 100)
print("TRYING: segment 4 bytes might be the FULL zlib stream split across all 4 segments")
print("The ordering might be: seg4 (zlib header) + seg1 + seg2 + seg3")
print("=" * 100)

# Try with segment 4 first (has 78 da header), then others in order
for perm in itertools.permutations(range(len(segments))):
    if perm[0] != 3:  # Only try permutations starting with segment 4 (index 3)
        continue
    assembled = b""
    for idx in perm:
        assembled += segments[idx]
    try:
        decompressed = zlib.decompress(assembled)
        print(f"\nSUCCESS with ordering: {perm}")
        print(f"  Assembled: {len(assembled)} bytes -> Decompressed: {len(decompressed)} bytes")
        text = decompressed.decode("utf-8", errors="replace")
        print("--- FULL DECOMPRESSED XML ---")
        print(text)
        print("--- END ---")
        sys.exit(0)
    except Exception as e:
        print(f"  Ordering {perm}: {e}")

# If nothing works, the segments might already be in correct order but the
# assembled data is not a single zlib stream. Instead, each segment might
# contain a chunk of zlib that needs to be decompressed differently.
#
# Or: the eCH-0196 spec says segments are 0-indexed, and zxingcpp decodes
# them in the order we encounter them in the PDF. Let's check if the
# entire reassembled data with segment 4 first can be incrementally decompressed.

print()
print("=" * 100)
print("RAW ZLIB INCREMENTAL DECOMPRESSION")
print("=" * 100)

# Try segment 4 as zlib, then append others
decompressor = zlib.decompressobj()
for start_idx in [3]:
    remaining = [i for i in range(4) if i != start_idx]
    for perm in itertools.permutations(remaining):
        order = [start_idx] + list(perm)
        decompressor = zlib.decompressobj()
        try:
            result = b""
            for idx in order:
                result += decompressor.decompress(segments[idx])
            result += decompressor.flush()
            print(f"SUCCESS with incremental ordering: {order}")
            print(f"  Decompressed: {len(result)} bytes")
            text = result.decode("utf-8", errors="replace")
            print("--- FULL DECOMPRESSED XML ---")
            print(text)
            print("--- END ---")
            sys.exit(0)
        except Exception as e:
            pass

print()
print("No permutation worked for zlib decompression.")
print()
print("The segments may not be in zlib order from zxingcpp.")
print("zxingcpp doesn't expose segment_index for Macro PDF417.")
print("Let's check our generator code to understand the expected ordering.")
