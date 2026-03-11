"""Barcode generation for eCH-0196 Swiss tax statements."""

from .code128c import build_identifier, generate
from .macro_pdf417 import encode_segment, render
from .orchestrator import BarcodeSet, generate_data_barcodes, generate_page_barcode

__all__ = [
    "build_identifier",
    "generate",
    "encode_segment",
    "render",
    "BarcodeSet",
    "generate_data_barcodes",
    "generate_page_barcode",
]
