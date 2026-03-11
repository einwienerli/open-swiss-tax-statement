#!/usr/bin/env python3
"""Extract and decode all barcodes from generated E-Steuerauszug PDF.

Extracts XObject images via pypdf, decodes with zxingcpp, and prints
every available field from the decoder result.
"""

import sys
import io
from pathlib import Path
from decimal import Decimal

# Use the PDF path from command line, or find the most recent one
PDF_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else None

if PDF_PATH is None or not PDF_PATH.exists():
    # Fallback: find any output PDF
    base = Path("user_data")
    candidates = sorted(base.glob("*/output/*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        print("ERROR: No PDF found in user_data/*/output/")
        sys.exit(1)
    PDF_PATH = candidates[0]
    print(f"Using most recent PDF: {PDF_PATH}")

print(f"PDF: {PDF_PATH}")
print(f"File size: {PDF_PATH.stat().st_size:,} bytes")
print("=" * 100)

import warnings
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from pypdf import PdfReader

from PIL import Image
import zxingcpp


def extract_image_from_xobject(xobj):
    """Convert a pypdf XObject to a PIL Image."""
    width = xobj["/Width"]
    height = xobj["/Height"]
    bpc = xobj.get("/BitsPerComponent", "N/A")
    color_space = xobj.get("/ColorSpace", "N/A")

    data = xobj.get_data()

    # Determine mode
    if str(color_space) in ("/DeviceGray", "[/DeviceGray]") or color_space == "/DeviceGray":
        if bpc == 1:
            img = Image.frombytes("1", (width, height), data)
        elif bpc == 8:
            img = Image.frombytes("L", (width, height), data)
        else:
            img = Image.frombytes("L", (width, height), data)
    elif str(color_space) in ("/DeviceRGB", "[/DeviceRGB]"):
        if bpc == 8:
            expected = width * height * 3
            if len(data) == expected:
                img = Image.frombytes("RGB", (width, height), data)
            else:
                # Maybe has alpha
                img = Image.frombytes("RGBA", (width, height), data)
        else:
            img = Image.frombytes("RGB", (width, height), data)
    else:
        # Try RGB, then L
        expected_rgb = width * height * 3
        expected_l = width * height
        if len(data) == expected_rgb:
            img = Image.frombytes("RGB", (width, height), data)
        elif len(data) == expected_l:
            img = Image.frombytes("L", (width, height), data)
        else:
            # Attempt 1-bit
            expected_1bit = (width + 7) // 8 * height
            if len(data) == expected_1bit:
                img = Image.frombytes("1", (width, height), data)
            else:
                print(f"    WARNING: Cannot determine image format. data={len(data)}, w={width}, h={height}")
                return None
    return img


def print_result_attrs(result, indent="    "):
    """Print every attribute of a zxingcpp Result/Barcode object."""
    print(f"{indent}--- ALL ATTRIBUTES (via dir) ---")
    all_attrs = [a for a in dir(result) if not a.startswith("_")]
    for attr_name in sorted(all_attrs):
        try:
            val = getattr(result, attr_name)
            if callable(val) and attr_name not in ("bytes", "extra"):
                print(f"{indent}  {attr_name}: <method>")
            else:
                if attr_name == "bytes":
                    raw = val
                    hex_preview = raw[:64].hex() if raw else "(empty)"
                    print(f"{indent}  bytes: length={len(raw)}, first 64 bytes hex={hex_preview}")
                elif attr_name == "text":
                    # Show repr to see control chars
                    print(f"{indent}  text: length={len(val)}")
                    # Show first 200 chars as repr
                    print(f"{indent}        repr(first 200)={repr(val[:200])}")
                    # Show full text
                    if len(val) <= 2000:
                        print(f"{indent}        full text follows between markers ---TEXT-START--- and ---TEXT-END---")
                        print(f"{indent}---TEXT-START---")
                        print(val)
                        print(f"{indent}---TEXT-END---")
                    else:
                        print(f"{indent}        (text too long, showing first 2000 chars)")
                        print(f"{indent}---TEXT-START---")
                        print(val[:2000])
                        print(f"{indent}---TEXT-END---")
                elif attr_name == "position":
                    print(f"{indent}  position: {val}")
                elif attr_name == "extra":
                    # extra might be a dict or object with structured append info
                    print(f"{indent}  extra: type={type(val).__name__}, value={val}")
                    if hasattr(val, '__dict__'):
                        for k, v in val.__dict__.items():
                            print(f"{indent}    extra.{k} = {v}")
                    if hasattr(val, '__dir__'):
                        extra_attrs = [a for a in dir(val) if not a.startswith("_")]
                        if extra_attrs:
                            print(f"{indent}    extra attributes: {extra_attrs}")
                            for ea in extra_attrs:
                                try:
                                    ev = getattr(val, ea)
                                    print(f"{indent}    extra.{ea} = {ev!r}")
                                except Exception as e:
                                    print(f"{indent}    extra.{ea} = ERROR: {e}")
                else:
                    print(f"{indent}  {attr_name}: {val!r}")
        except Exception as e:
            print(f"{indent}  {attr_name}: ERROR accessing: {e}")
    print(f"{indent}--- END ATTRIBUTES ---")


def main():
    reader = PdfReader(str(PDF_PATH))
    print(f"Total pages: {len(reader.pages)}")
    print()

    barcode_count = 0

    for page_idx, page in enumerate(reader.pages):
        print(f"{'=' * 100}")
        print(f"PAGE {page_idx + 1}")
        print(f"{'=' * 100}")

        rotation = page.get("/Rotate", 0)
        mediabox = page.mediabox
        print(f"  Rotation: {rotation}")
        print(f"  MediaBox: {mediabox}")

        resources = page.get("/Resources", {})
        xobjects = resources.get("/XObject", {})

        if not xobjects:
            print("  No XObjects on this page.")
            print()
            continue

        print(f"  XObjects found: {len(xobjects)}")
        print()

        images_on_page = []

        for name, xobj_ref in xobjects.items():
            xobj = xobj_ref.get_object()
            subtype = xobj.get("/Subtype", "N/A")

            if str(subtype) != "/Image":
                print(f"  [{name}] Subtype={subtype} (not an image, skipping)")
                continue

            width = xobj.get("/Width", "N/A")
            height = xobj.get("/Height", "N/A")
            bpc = xobj.get("/BitsPerComponent", "N/A")
            color_space = xobj.get("/ColorSpace", "N/A")
            filters = xobj.get("/Filter", "N/A")

            print(f"  [{name}]")
            print(f"    Dimensions: {width} x {height}")
            print(f"    BitsPerComponent: {bpc}")
            print(f"    ColorSpace: {color_space}")
            print(f"    Filter: {filters}")

            # Get raw data size
            raw_data = xobj.get_data()
            print(f"    Decoded data size: {len(raw_data)} bytes")

            # Classify barcode type
            barcode_type = None
            if isinstance(width, int) and isinstance(height, int):
                if 280 <= width <= 300 and 30 <= height <= 40:
                    barcode_type = "PDF417"
                elif 115 <= width <= 130 and 40 <= height <= 55:
                    barcode_type = "CODE128C"
                else:
                    barcode_type = f"UNKNOWN ({width}x{height})"

            print(f"    Classified as: {barcode_type}")

            # Extract and decode
            img = extract_image_from_xobject(xobj)
            if img is None:
                print(f"    FAILED to extract image")
                print()
                continue

            print(f"    PIL Image: mode={img.mode}, size={img.size}")

            # Convert to grayscale for better decoding
            if img.mode == "1":
                img_decode = img.convert("L")
            elif img.mode == "RGBA":
                img_decode = img.convert("RGB")
            else:
                img_decode = img

            # Try decoding with all formats first
            print(f"    Decoding with zxingcpp.read_barcodes()...")
            results = zxingcpp.read_barcodes(img_decode, return_errors=True)

            if not results:
                # Try with specific format hints
                print(f"    No results with default settings. Trying with is_pure=True...")
                results = zxingcpp.read_barcodes(img_decode, is_pure=True, return_errors=True)

            if not results:
                print(f"    No barcodes decoded from this image.")
                print()
                continue

            for res_idx, result in enumerate(results):
                barcode_count += 1
                print()
                print(f"    ===== DECODED BARCODE #{barcode_count} (result {res_idx + 1}/{len(results)}) =====")
                print_result_attrs(result)
                print()

            images_on_page.append((name, barcode_type, img))

        print()

    print(f"{'=' * 100}")
    print(f"SUMMARY: Decoded {barcode_count} barcodes total from {len(reader.pages)} pages")
    print(f"{'=' * 100}")


if __name__ == "__main__":
    main()
