"""CLI: Generate barcode-only PDF from raw eCH-0196 XML.

Usage:
    python -m src.admin.xml_to_pdf input.xml --clearing 08750 -o output.pdf
    python -m src.admin.xml_to_pdf input.xml --clearing 00769 --file-id 206024035152 -o output.pdf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _parse_file_id(file_id_str: str) -> list[int]:
    """Parse a 12-digit FileId string into 4 codewords.

    FileId '206024035152' → [206, 24, 35, 152]
    Each 3-digit group is one codeword (0-899).
    """
    if len(file_id_str) != 12 or not file_id_str.isdigit():
        raise ValueError(f"FileId must be exactly 12 digits, got: {file_id_str!r}")
    codewords = [int(file_id_str[i : i + 3]) for i in range(0, 12, 3)]
    for cw in codewords:
        if cw > 899:
            raise ValueError(f"Codeword {cw} exceeds max 899")
    return codewords


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate barcode-only PDF from eCH-0196 XML."
    )
    parser.add_argument("xml_file", type=Path, help="Input XML file")
    parser.add_argument(
        "--clearing", default="08750", help="Clearing number (default: 08750)"
    )
    parser.add_argument(
        "--file-id",
        help="12-digit Macro PDF417 FileId (e.g. 206024035152). Random if omitted.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        help="Fixed chunk size in bytes for sequential chunking (e.g. 470).",
    )
    parser.add_argument("-o", "--output", type=Path, help="Output PDF path")
    args = parser.parse_args()

    if not args.xml_file.exists():
        print(f"Error: {args.xml_file} not found", file=sys.stderr)
        sys.exit(1)

    xml_string = args.xml_file.read_text(encoding="utf-8")
    output_path = args.output or args.xml_file.with_suffix(".pdf")

    file_id_codewords = None
    if args.file_id:
        file_id_codewords = _parse_file_id(args.file_id)
        print(f"Using FileId {args.file_id} → codewords {file_id_codewords}")

    from src.generators.pdf.assembler import generate_pdf_from_xml

    print(f"Generating barcode PDF from {args.xml_file}...")
    pdf_bytes = generate_pdf_from_xml(
        xml_string, args.clearing, file_id_codewords, args.chunk_size
    )
    output_path.write_bytes(pdf_bytes)
    print(f"Written {len(pdf_bytes):,} bytes to {output_path}")

    # Print XObject summary
    from src.admin.inspect_pdf import inspect_pdf
    inspect_pdf(str(output_path))


if __name__ == "__main__":
    main()
