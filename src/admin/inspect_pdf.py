"""CLI: Inspect PDF XObject image properties.

Usage:
    python -m src.admin.inspect_pdf some_file.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

from pypdf import PdfReader


def inspect_pdf(pdf_path: str) -> None:
    """Print XObject image properties for each page."""
    reader = PdfReader(pdf_path)

    for page_idx, page in enumerate(reader.pages):
        rotate = page.get("/Rotate", 0)
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)
        print(f"\nPage {page_idx + 1}: {w:.0f}x{h:.0f}pt, /Rotate={rotate}")

        resources = page.get("/Resources")
        if not resources:
            print("  (no resources)")
            continue
        xobjects = resources.get("/XObject")
        if not xobjects:
            print("  (no XObjects)")
            continue

        xobj_dict = xobjects.get_object()
        for name in sorted(xobj_dict.keys()):
            xobj = xobj_dict[name].get_object()
            if xobj.get("/Subtype") != "/Image":
                continue

            img_w = xobj.get("/Width", "?")
            img_h = xobj.get("/Height", "?")
            cs = xobj.get("/ColorSpace", "?")
            bpc = xobj.get("/BitsPerComponent", "?")
            filt = xobj.get("/Filter", "?")

            # Get compressed data size
            try:
                data_len = len(xobj._data) if hasattr(xobj, "_data") else "?"
            except Exception:
                data_len = "?"

            print(
                f"  {name}: {img_w}x{img_h} "
                f"ColorSpace={cs} BPC={bpc} "
                f"Filter={filt} compressed={data_len}B"
            )


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m src.admin.inspect_pdf <pdf_file>", file=sys.stderr)
        sys.exit(1)

    pdf_path = sys.argv[1]
    if not Path(pdf_path).exists():
        print(f"Error: {pdf_path} not found", file=sys.stderr)
        sys.exit(1)

    inspect_pdf(pdf_path)


if __name__ == "__main__":
    main()
