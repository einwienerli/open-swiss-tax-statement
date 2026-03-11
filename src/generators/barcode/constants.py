"""Constants for barcode generation (eCH-0196 spec)."""

# CODE128C identifier fields
FORM_NUMBER = "196"
VERSION = "22"

# PDF417 encoding parameters (eCH-0196 Technische Wegleitung §2.2-2.4)
PDF417_COLUMNS = 13
PDF417_SECURITY_LEVEL = 4
PDF417_ROWS = 35
CHUNK_SIZE_BYTES = 470
MAX_BARCODES_PER_SHEET = 6
ZLIB_LEVEL = 9

# Macro PDF417 codewords (ISO 15438)
MACRO_BEGIN = 928
MACRO_TERMINATE = 922
MACRO_FILENAME = 923
BYTE_LATCH = 901
MACRO_FILENAME_FIELD_TYPE = 0  # Optional field type for fileName (ISO 15438 §5.5)
MACRO_SEGMENT_COUNT_FIELD_TYPE = 1  # Optional field type for segmentCount (ISO 15438 §5.5)
SEGMENT_INDEX_PAD_WIDTH = 5  # Zero-pad segment index/count to 5 digits before base-900 encoding
