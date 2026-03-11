# ZH Tax Portal Acceptance Guide

Complete reference for generating E-Steuerauszug PDFs that are accepted by the Zurich cantonal tax portal. Based on the eCH-0196 Technische Wegleitung v2.2.0 (2022-06-07), systematic validation against the working TrueWealth 2024/2025 reference PDFs, and confirmed portal acceptance of our generated output.

Last verified: February 2026 (portal accepted both TrueWealth-XML and Swissquote-XML test PDFs).

---

## Table of Contents

0. [Primer: How eCH-0196 Works](#0-primer-how-ech-0196-works)
1. [Architecture Overview](#1-architecture-overview)
2. [eCH-0196 XML Requirements](#2-ech-0196-xml-requirements)
3. [PDF417 Macro Barcode Generation](#3-pdf417-macro-barcode-generation)
4. [CODE128C Page Barcode Generation](#4-code128c-page-barcode-generation)
5. [PDF Assembly and Post-Processing](#5-pdf-assembly-and-post-processing)
6. [Barcode Sheet Layout](#6-barcode-sheet-layout)
7. [Portal Validation Findings](#7-portal-validation-findings)
8. [Debugging Methodology](#8-debugging-methodology)
9. [Quick Reference: Critical Parameters](#9-quick-reference-critical-parameters)

---

## 0. Primer: How eCH-0196 Works

### What is an E-Steuerauszug?

An E-Steuerauszug (electronic tax statement) is a machine-readable PDF that Swiss banks produce for their clients' annual tax filings. It contains all securities holdings, cash balances, dividends, interest, and custody fees for a given tax year. Taxpayers upload this PDF to their canton's tax portal, which automatically extracts the data — eliminating manual entry of potentially hundreds of positions.

Banks charge CHF 85-100 for this service. This project generates the same PDF from free broker CSV exports.

### The eCH-0196 Standard

eCH-0196 is the Swiss federal standard (version 2.2) that defines the XML schema and barcode format for E-Steuerauszug PDFs. It specifies:

- **XML schema**: Structure and validation rules for the tax data (XSD-based)
- **Barcode encoding**: How the XML is compressed and embedded as barcode images in the PDF
- **PDF layout**: Page structure, barcode placement, and page identification

The key insight: **the PDF is just a delivery vehicle for the barcodes.** The tax portal doesn't OCR the text — it extracts barcode images from the PDF's internal structure (XObject resources), decodes them back to compressed XML, decompresses, and validates against the XSD schema.

### Two Types of Barcodes

Every E-Steuerauszug PDF contains two types of barcodes serving different purposes:

**PDF417 (data barcodes)** — 2D barcodes that carry the actual tax data:
- The full eCH-0196 XML is zlib-compressed, then split into 470-byte chunks
- Each chunk becomes one PDF417 "segment" (a 290x35 pixel barcode image)
- Segments are linked via Macro PDF417 structured append (ISO 15438) — the portal reassembles them in order using segment indices and a shared file ID
- Typical statement: 8-12 segments across 1-2 barcode sheets (6 per sheet)
- These appear as tall vertical bars on dedicated barcode pages at the end of the PDF

**CODE128C (page barcodes)** — 1D barcodes that identify each page:
- Every page (data pages AND barcode pages) gets one CODE128C barcode
- Encodes a 16-digit identifier: form number, version, bank clearing number, page number, and flags
- Tells the portal which page it's looking at, whether the page has PDF417 barcodes, and the reading orientation
- Small (123x46 pixels), appears in the upper-left corner of each landscape page

### How the Portal Processes the PDF

```
Upload PDF
  -> Extract XObject images from last pages backward
  -> Identify barcode images by dimensions (290x35 = PDF417, 123x46 = CODE128C)
  -> Decode CODE128C to get page metadata
  -> Decode all PDF417 segments
  -> Reassemble segments using Macro PDF417 structured append metadata
  -> Decompress zlib stream -> eCH-0196 XML
  -> Validate XML against XSD schema
  -> Import data into tax filing
```

The portal works backwards from the last page because barcode sheets are always appended after the data pages. If image extraction from XObjects fails, the portal falls back to rendering pages at 300 DPI and scanning for barcodes in the rendered image — but the primary path is direct XObject extraction, which is why the PDF internal structure (XObject naming, colorspace, bit depth) matters.

### Key Concepts

#### PDF Internals

**XObjects (External Objects)** — Reusable resources embedded inside a PDF file. Images are stored as "Image XObjects": self-contained objects with their own width, height, colorspace, compression filter, and pixel data. Each XObject is referenced by name (e.g. `/Im1`) from the page's resource dictionary. When a page wants to draw an image, its content stream issues a `Do` operator: `/Im1 Do`. The portal extracts barcode images by iterating over a page's XObject dictionary and checking image dimensions — this is why XObject naming (`/Im1` vs `/FormXob.<hash>`) and image properties (colorspace, bit depth) matter.

**Content Stream** — The sequence of drawing instructions for a PDF page, written in PDF's postfix operator language. Contains text commands (`BT`/`ET` blocks), coordinate transforms (`cm` operator), and image placements (`Do` operator). Can be a single stream or an array of streams. The portal parses content streams to find `Do` operators that reference barcode XObjects.

**MediaBox** — Defines the physical page dimensions in points (1 point = 1/72 inch). A portrait A4 page has `MediaBox [0 0 595.276 841.89]` (210mm x 297mm). This is the actual page size stored in the PDF, regardless of how the page is displayed.

**`/Rotate`** — A page-level property that tells PDF viewers to rotate the page for display. `/Rotate 90` means "display this portrait page as landscape by rotating 90 degrees clockwise." The underlying coordinate system and XObject images remain unrotated — only the viewer's presentation changes. The eCH-0196 spec mandates this approach: "rotate the page, not the images."

**CTM (Current Transformation Matrix)** — A 3x2 affine matrix that transforms drawing coordinates. The `cm` operator applies a CTM: `a b c d e f cm` maps `(x,y)` to `(ax+cy+e, bx+dy+f)`. We use `0 1 -1 0 W 0 cm` to establish a landscape coordinate system on a portrait page, then an inverse CTM `0 -1 1 0 0 W cm` to switch back to portrait coordinates for barcode placement.

**DeviceGray / BitsPerComponent** — PDF colorspace and bit depth for images. `DeviceGray` = single-channel grayscale (vs `DeviceRGB` = 3-channel color). `BitsPerComponent=1` = 1 bit per pixel (pure black/white, no gray). Barcode images must be DeviceGray with BPC=1: each pixel is either black (0) or white (1), packed 8 pixels per byte, MSB-first, rows padded to byte boundaries.

**FlateDecode** — PDF's name for zlib/deflate compression applied to stream data (content streams, image data). When `set_data()` is called on a pypdf stream with `/Filter /FlateDecode`, pypdf automatically compresses the raw bytes. This is the same algorithm as Python's `zlib` module but used at the PDF container level (compressing image pixel data or drawing instructions), as opposed to the application-level zlib compression of the XML payload inside the barcodes.

**IndirectObject** — A PDF reference to an object stored elsewhere in the file (like a pointer). XObjects are typically stored as indirect objects — the page's resource dictionary holds a reference, not the image data itself. When manipulating XObject dictionaries with pypdf, using `raw_get()` preserves these references; using direct dictionary access resolves the reference and inlines the full object, which can break the PDF structure.

#### Barcode Encoding

**Codeword** — The fundamental data unit of PDF417. Each codeword is an integer in range 0-928. A 35-row x 13-column symbol contains 455 codewords total, split between data codewords and error correction codewords. Special codewords have reserved meanings: 900 = padding, 901 = byte compaction latch, 924 = mod-6 byte compaction latch, 922 = macro terminate, 923 = optional field indicator, 928 = macro begin.

**Base-900 Encoding** — A number representation using codeword values 0-899 as digits (similar to how base-10 uses digits 0-9). Used to encode numeric values like segment indices and segment counts into codeword pairs: `value = high * 900 + low`. The TrueWealth/J4L convention adds a leading-'1' prefix with zero-padding before encoding, producing predictable 2-codeword values.

**Byte Compaction** — A PDF417 encoding mode that converts raw bytes into codewords. Standard mode (latch 901) encodes groups of 6 bytes into 5 codewords with a remainder handler. Mod-6 mode (latch 924) is used when the data length is exactly divisible by 6, producing a slightly more efficient encoding with no remainder. The latch codeword tells the decoder which mode was used.

**Text Compaction** — A PDF417 encoding mode optimized for ASCII text. More efficient than byte compaction for alphanumeric data. Used to encode the statement ID (fileName) in the macro control block, since it's a 31-character alphanumeric string.

**Structured Append (Macro PDF417)** — An ISO 15438 mechanism for splitting data across multiple PDF417 symbols. Each symbol carries a macro control block with: a segment index (its position in the sequence), a file ID (shared across all segments to link them), a fileName (the statement ID), and optionally a segment count. The last segment has a terminate marker (codeword 922). The portal reassembles the full data by collecting all segments with the same file ID and concatenating their data payloads in segment index order.

**Error Correction (EC)** — Reed-Solomon error correction codewords appended after the data codewords. EC level 4 produces `2^(4+1) = 32` EC codewords, which can correct up to 16 codeword errors or detect up to 32 errors. These are computed from the data codewords using polynomial division and are always placed at the end of the symbol. The EC level determines how much of the symbol's capacity is reserved for error correction vs data.

**Length Descriptor (LD)** — The first codeword in every PDF417 symbol. Its value equals the total number of data codewords in the symbol (including itself). For a fully-utilized 35x13 EC4 symbol: LD = 423 (= 455 total - 32 EC). The decoder uses this to know where data ends and EC begins.

**Quiet Zone** — The minimum blank margin around a barcode image required for reliable scanning. For CODE128C, the spec requires 5mm around the barcode. In our implementation, quiet zones are handled by page margins and barcode placement coordinates, not by adding whitespace to the barcode images themselves (PDF417 images have `padding=0`).

#### Compression

**zlib** — A general-purpose lossless compression algorithm (RFC 1950). Used at two levels in E-Steuerauszug: (1) application-level compression of the XML payload before chunking into barcode segments (`zlib.compress(xml, level=9)`), and (2) PDF-level compression of stream data via FlateDecode. Level 9 (maximum compression) is required for the XML payload to match TrueWealth's output byte-for-byte — different compression levels produce different byte sequences, which means different codewords, different EC values, and ultimately a non-matching barcode. The `78da` magic bytes in the compressed output indicate zlib level 9 with default strategy.

---

## 1. Architecture Overview

### Pipeline

```
TaxStatement
  -> build_xml() + serialize()           # eCH-0196 XML string
  -> zlib.compress(xml, level=9)          # compressed bytes
  -> chunk at 470 bytes                   # sequential chunks
  -> encode_segment() per chunk           # Macro PDF417 codewords -> row patterns
  -> render() per segment                 # 290x35 PIL images
  -> PdfRenderer.render()                 # reportlab portrait A4 pages
  -> pypdf post-processing                # /Rotate=90, DeviceGray/1-bit, XObject renaming
  -> final PDF bytes
```

### Key Files

| File | Purpose |
|------|---------|
| `src/generators/ech0196/xml_builder.py` | TaxStatement -> eCH-0196 XML |
| `src/generators/ech0196/constants.py` | XML namespaces, schema refs, enum values |
| `src/generators/barcode/orchestrator.py` | XML -> zlib -> chunks -> PDF417 images |
| `src/generators/barcode/macro_pdf417.py` | Macro PDF417 segment encoding |
| `src/generators/barcode/code128c.py` | CODE128C page barcode generation |
| `src/generators/barcode/constants.py` | Barcode spec parameters |
| `src/generators/pdf/renderer.py` | reportlab page rendering |
| `src/generators/pdf/assembler.py` | pypdf post-processing + public API |
| `src/generators/pdf/constants.py` | Page layout constants |

---

## 2. eCH-0196 XML Requirements

### Root Element Attributes

The portal is strict about these attributes on `<taxStatement>`:

| Attribute | Required Value | Notes |
|-----------|---------------|-------|
| `minorVersion` | `"22"` | NOT `"2"` — must be the full minor version number |
| `country` | `"CH"` | Must be present on root element |
| `id` | 31-char statement ID | `CH{clearing5}{account14}{YYYYMMDD}01` |
| `taxPeriod` | Year string | e.g. `"2025"` |
| `periodFrom` / `periodTo` | `YYYY-MM-DD` | ISO date format |

### Statement ID Format

```
CH + clearing(5, zero-padded) + account(14, zero-padded) + stichtag(YYYYMMDD) + sequence(2)
= 2 + 5 + 14 + 8 + 2 = 31 characters total
```

This ID must be identical in three places:
1. XML root `id` attribute
2. Macro PDF417 `fileName` field
3. Derived from the same account/clearing data

### Decimal Formatting

- All monetary values: **2 decimal places** (e.g. `"1234.50"`, not `"1234.5"`)
- Exchange rates: **full precision**, trailing zeros stripped (e.g. `"0.9385"`)
- Quantities: 2 decimal places in XML attributes

### Client and Depot Numbers

- `clientNumber` and `depotNumber` = account number (not clearing number)
- XSD requires `minLength=1` — if account number is empty, fall back to clearing number
- Swiss AHV numbers (13 digits without dots) exceed `TIN maxLength=11` in XSD — omit TIN for these

### Security Tax Values

TrueWealth convention (which the portal accepts):
- `balanceCurrency="CHF"` on all security `<taxValue>` elements
- `unitPrice` in CHF (from ESTV Kursliste `taxValueCHF`)
- No `exchangeRate` attribute on security tax values
- Bank accounts use original currency + `exchangeRate`

### Fully Sold Positions

Securities bought and sold within the tax year (no year-end holding) must still appear:
- `quantity="0.00"` and `value="0.00"` on `<taxValue>`
- `balanceCurrency="CHF"` (not the security's original currency — there's no FX amount to convert)
- Opening balance stock mutation with calculated starting quantity

---

## 3. PDF417 Macro Barcode Generation

### Spec Parameters (eCH-0196 Wegleitung SS2.2)

| Parameter | Value | Source |
|-----------|-------|--------|
| Columns | 13 | Spec SS2.2 |
| Rows | 35 (fixed, all segments) | Spec SS2.2 |
| EC Level | 4 | Spec SS2.2 |
| Chunk size | 470 bytes (sequential) | Empirical (TW match) |
| Zlib level | 9 (`78da` header) | Spec + TW match |
| Image dimensions | 290 x 35 pixels | Spec SS2.2 |
| Segments per sheet | 6 | Spec SS2.3 |

### Capacity Math

Understanding the capacity is critical for avoiding overflow bugs:

```
Total codewords per symbol:  35 rows x 13 cols = 455
EC codewords (level 4):      2^(4+1) = 32
Available data codewords:    455 - 32 = 423

Data area layout:
  [1 length descriptor] + [byte section] + [padding] + [macro control]
  = 423 codewords total

Macro control block size (non-last segment):
  [928] + [seg_hi, seg_lo] + [file_id x4] + [923, 0, text_cws...] + [923, 1, count_hi, count_lo]
  = typically ~18-20 codewords (depends on fileName text compaction)

For fileName of 31 chars: macro block = ~20 codewords
Available for byte data: 423 - 1(LD) - 20(macro) = 402 codewords
At byte compaction ratio of ~1.2 bytes/codeword: ~470 bytes max
```

### Compression

```python
compressed = zlib.compress(xml_string.encode("utf-8"), 9)
```

Level 9 is required for byte-identical output matching TrueWealth (produces `78da` zlib header). Default level 6 produces different compressed bytes, which means different codewords.

### Sequential Chunking

Chunks are created sequentially at exactly 470 bytes (not evenly distributed):

```python
chunks = [compressed[i:i+470] for i in range(0, len(compressed), 470)]
```

**Last-chunk terminate overflow**: The last segment includes a terminate marker (codeword 922), which takes 1 extra codeword. If the last chunk is exactly 470 bytes, it would overflow the 423 data codeword capacity. Fix: split off the last byte to a new segment.

```python
if len(chunks[-1]) == 470:
    last = chunks[-1]
    chunks[-1] = last[:469]
    chunks.append(last[469:])
```

### Byte Compaction Latch

- Use **924** (mod-6 byte compaction) when `len(chunk) % 6 == 0`
- Use **901** (standard byte compaction) otherwise
- The latch appears before the byte data, after the length descriptor

### Macro Control Block (ISO 15438 SS5.5)

The macro control block encodes structured append metadata. It MUST be placed at the END of the data area, with padding between the byte data and the macro block:

```
Layout: [LD] + [byte_data] + [padding_900s] + [macro_control]
```

**This ordering is critical.** Placing macro before padding, or padding after macro, causes portal rejection.

#### Macro Block Structure

```
[928]                         # Macro PDF417 begin marker
[seg_hi, seg_lo]              # Segment index (base-900 with zero-padded leading '1')
[fid_0, fid_1, fid_2, fid_3] # File ID (4 codewords, each 0-899)
[923, 0, text_cws...]         # Optional: fileName field (type 0)
[923, 1, count_hi, count_lo]  # Optional: segmentCount field (type 1)
[922]                         # Terminate marker (LAST segment only)
```

#### Segment Index Encoding (TrueWealth/J4L Convention)

The segment index is NOT directly base-900 encoded. It uses a zero-padded leading-'1' prefix convention:

```python
def encode_segment_index(index: int) -> list[int]:
    n = int("1" + str(index).zfill(5))  # e.g. index=0 -> 100000
    return [n // 900, n % 900]           # -> [111, 100]
```

| Index | Padded String | Integer | Codewords |
|-------|--------------|---------|-----------|
| 0 | `"100000"` | 100000 | [111, 100] |
| 1 | `"100001"` | 100001 | [111, 101] |
| 8 | `"100008"` | 100008 | [111, 108] |

Without zero-padding (naive `int("1" + str(i))`), index 0 would produce `[0, 10]` instead of `[111, 100]`. This was one of the three critical differences that caused portal rejection.

#### segmentCount Optional Field

The `segmentCount` field (type 1, indicator 923) must be included on **every** segment, not just the last one. This matches TrueWealth/J4L behavior. The segment count uses the same zero-padded encoding as the segment index:

```python
# On every segment:
macro_block.extend([923, 1])  # optional field indicator + type SEGMENT_COUNT
macro_block.extend(encode_segment_index(segment_count))  # same encoding
```

For full 470-byte segments, this field fills exactly the 4 remaining codeword slots that would otherwise be padding — meaning full segments have zero padding codewords.

#### File ID

Four codewords, each in range 0-899. When decoded, each codeword is formatted as a 3-digit zero-padded string and concatenated: e.g., codewords `[206, 024, 035, 152]` decode to string `"206024035152"`.

For production use, generate random values. For testing against a known reference, extract the exact values from the reference PDF's barcodes.

#### fileName Field

The fileName (type 0, indicator 923) contains the statement ID, text-compacted using pdf417gen's `compact_text()` function. The statement ID is ASCII-only (alphanumeric), so text compaction is efficient.

### Rendering

```python
from pdf417gen.rendering import render_image
image = render_image(encoded_rows, scale=1, ratio=1, padding=0).convert("L")
# Result: 290x35 pixel grayscale image
```

- `scale=1`: 1 pixel per module
- `ratio=1`: aspect ratio 1:1
- `padding=0`: no quiet zone in the image (quiet zone is handled by PDF placement)
- Convert to mode `"L"` (8-bit grayscale) for intermediate processing; final PDF post-processing converts to 1-bit

---

## 4. CODE128C Page Barcode Generation

### Identifier Format (16 digits)

```
196 + 22 + CCCCC + PPP + D + O + R
```

| Field | Length | Value |
|-------|--------|-------|
| Form number | 3 | `196` |
| Version | 2 | `22` (matches `minorVersion`) |
| Organisation number | 5 | Clearing number, zero-padded |
| Page number | 3 | Continuous across ALL pages (data + barcode), zero-padded |
| 2D barcode flag | 1 | `1` if page has PDF417, `0` otherwise |
| Orientation | 1 | `0` (always landscape/Querformat) |
| Reading direction | 1 | `2` (left/right, reading top-to-bottom) |

**Reading direction = 2**: This means the CODE128C barcode is vertical on the page, read from top to bottom. This is set to `2` on ALL pages (data pages AND barcode pages), matching TrueWealth.

### Image Specifications

| Property | Value |
|----------|-------|
| Dimensions | 123 x 46 pixels |
| Color mode | 1-bit B&W (PIL mode `"1"`) |
| Encoding | Code 128 Subset C (pairs of digits) |

### Image Layout (46 rows)

```
Rows  0-2:   quiet zone (3px white)
Rows  3-28:  barcode bars (26px, all rows identical)
Rows 29-37:  gap (9px white)
Rows 38-44:  human-readable number (7px, 5x7 bitmap font)
Row  45:     bottom padding (1px white)
```

### Implementation

Custom pure-Python implementation (no external barcode library). Encodes from the ISO/IEC 15417 pattern table at 1 pixel per module. Bar pattern has been verified bit-for-bit against TrueWealth reference.

The checksum calculation is: `start_value(105) + sum(position_i * pair_value_i)`, mod 103.

---

## 5. PDF Assembly and Post-Processing

The PDF is built in two phases: initial rendering with reportlab, then post-processing with pypdf.

### Phase 1: reportlab Rendering

reportlab generates portrait A4 pages with a landscape coordinate system established via CTM (Current Transformation Matrix):

```python
# Portrait page setup
canvas.setPageSize(A4)  # 595.27 x 841.89 pt

# Landscape drawing CTM: maps (x,y) landscape -> (W-y, x) portrait
canvas.transform(0, 1, -1, 0, PORTRAIT_WIDTH, 0)
```

After this transform, all drawing operations use landscape coordinates (0-842 wide, 0-595 tall), but the underlying page is portrait.

#### Barcode Placement

Barcodes are placed using an **inverse CTM** that maps back to portrait coordinates:

```python
canvas.saveState()
canvas.transform(0, -1, 1, 0, 0, PORTRAIT_WIDTH)  # inverse CTM
canvas.drawImage(barcode_image, x_portrait, y_portrait, width, height)
canvas.restoreState()
```

This ensures barcode images are stored unrotated in the PDF — the `/Rotate=90` page property handles the visual rotation.

### Phase 2: pypdf Post-Processing

Three critical fixes are applied after reportlab generates the raw PDF:

#### 1. Page Rotation (`/Rotate=90`)

```python
for page in reader.pages:
    page[NameObject("/Rotate")] = NumberObject(90)
    writer.add_page(page)
```

Every page gets `/Rotate=90`. This is the spec-mandated approach: "rotate the page, not the images."

#### 2. Barcode XObject Colorspace (DeviceGray, 1-bit)

reportlab normalizes PIL images to RGB (3 bytes/pixel). The portal requires DeviceGray with BitsPerComponent=1:

```python
# For CODE128C (123x46, originally RGB):
gray8 = bytes(raw[i] for i in range(0, len(raw), 3))  # extract R channel
packed = pack_1bit(gray8, width, height)

# For PDF417 (290x35, already grayscale):
packed = pack_1bit(raw, width, height)

xobj["/ColorSpace"] = "/DeviceGray"
xobj["/BitsPerComponent"] = 1
xobj["/Filter"] = "/FlateDecode"
xobj.set_data(packed)
```

1-bit packing: `>= 128 -> white (1), < 128 -> black (0)`, MSB-first, rows padded to byte boundaries.

#### 3. XObject Renaming (Sequential `/Im` Names)

**This was the root cause of portal rejection for our reportlab pages.** reportlab names XObjects `/FormXob.<hex-hash>`. The portal ONLY accepts sequential names like `/Im1`, `/Im2`, etc.

```python
# Rename: /FormXob.abc123 -> /Im1, /FormXob.def456 -> /Im2, ...
rename_map = {old: f"/Im{i}" for i, old in enumerate(sorted(xobj_dict.keys()), 1)}

# Must update BOTH:
# 1. The XObject resource dictionary (page-level)
# 2. The content stream Do operators (e.g., "/FormXob.abc123 Do" -> "/Im1 Do")
```

**Critical implementation detail**: When rebuilding the XObject dictionary, use `raw_get()` to preserve `IndirectObject` references. Using direct access (`xobj_dict[name]`) resolves to the stream object, causing pypdf to inline the entire image in the page resources. macOS CoreGraphics rejects PDFs with inlined XObject streams.

**Critical implementation detail**: When renaming content stream references, decompress with the original filter, apply renames, then recompress as FlateDecode. Writing uncompressed streams breaks CoreGraphics rendering.

---

## 6. Barcode Sheet Layout

### Grid System

6 barcodes per sheet in 2 groups of 3, using a unit-based spacing system:

```
Available space = page_height - (6 * barcode_height)
Unit G = available_space / 12

Top margin:        3G
Within-group gap:  1G (between barcodes 1-2, 2-3, 4-5, 5-6)
Between-group gap: 2G (between barcodes 3 and 4)
Bottom margin:     3G
```

Partial pages fill top-to-bottom, leaving bottom slots empty. The 6 fixed Y-positions are pre-computed and match TrueWealth within 0.1 pt.

### Barcode Display Dimensions

The spec mandates 97% print scaling compensation:

| Dimension | Raw | With 97% compensation |
|-----------|-----|----------------------|
| PDF417 width | 290 x 0.042cm = 12.18cm | 12.18cm / 0.97 = 12.56cm = 355.9pt |
| PDF417 height | 35 x 0.08cm = 2.8cm | 2.8cm / 0.97 = 2.89cm = 81.8pt |
| CODE128C | 123 x 46 px | Placed at 123pt x 37pt |

### CODE128C Position

On every page (data and barcode pages), CODE128C is placed at portrait coordinates:
- X = 29pt from left edge of portrait page
- Y = 14pt from bottom of portrait page
- After `/Rotate=90`: appears at upper-left of landscape view, reading top-to-bottom

---

## 7. Portal Validation Findings

### What the Portal Checks

Based on systematic isolation testing (10 test variants + 6 structural variants):

| Aspect | Portal Cares? | Details |
|--------|:------------:|---------|
| XObject naming | **YES** | Rejects `/FormXob.*`, requires `/Im1`, `/Im2`, ... |
| PDF417 codeword encoding | **YES** | Must decode to valid XML; macro block must be parseable |
| XML content | **YES** | Validates against XSD; checks `minorVersion`, `country`, `id` |
| Image colorspace | Unknown | We use DeviceGray/1-bit (matching TW). Untested with RGB. |
| MediaBox values | No | reportlab's `[0,0,595.276,841.89]` vs TW's `[0,0,595.27563,841.8898]` — both accepted |
| CTM precision | No | Different floating-point values for rotation matrix are fine |
| Content stream format | No | Single stream vs array of streams — both work |
| Text preamble | No | `BT/ET` blocks in content stream are optional |
| `/Trans` dictionary | No | Transition effects dictionary is optional |
| `/ProcSet` array | No | Resource procedure sets are optional |
| Font resources | No | TW uses CID fonts, we use Helvetica — both work |
| Visual content | No | Barcodes-only pages (zero text/logos) are accepted |

### What Was NOT the Issue

These were systematically ruled out during validation:

1. **Page structure**: Our reportlab page structure is fine — only XObject naming was the issue
2. **Barcode image data**: Our pdf417gen encoding was always correct (codewords were right)
3. **PDF417 row patterns**: EC and row encoding from pdf417gen are correct
4. **CODE128C bar patterns**: Bit-for-bit identical to TrueWealth

### The Three Macro Encoding Fixes

These three differences in the Macro PDF417 control block caused our barcodes to decode to different XML, preventing portal acceptance. Each fix was identified by comparing exact codewords between our output and TrueWealth's:

| # | Issue | Our Value | TrueWealth Value | Impact |
|---|-------|-----------|-------------------|--------|
| 1 | Segment index encoding | `[0, 10+i]` | `[111, 100+i]` | Different macro metadata — ZXing decoded different segment indices |
| 2 | segmentCount field | Omitted | `[923, 1, hi, lo]` on every segment | Missing optional field changed padding, shifted codewords |
| 3 | Padding placement | `[data][macro][padding]` | `[data][padding][macro]` | Macro block at wrong position — last segment terminate marker in wrong place |

After fixing all three, every barcode (9 segments) was codeword-identical to TrueWealth across all 455 codewords per symbol.

---

## 8. Debugging Methodology

### Codeword Extraction and Comparison

The breakthrough debugging approach was extracting exact codewords from both TrueWealth's accepted PDFs and our generated PDFs, then comparing them row by row.

**Script**: `validation_data/extract_codewords.py`

**Approach**:
1. Extract PDF417 XObject images from PDF using pypdf
2. For each image, decode using zxing-cpp to get the raw data
3. Re-encode the data through our pipeline to get our codewords
4. Use pdf417gen internals to get TrueWealth's codewords from their images
5. Compare codeword-by-codeword, identifying exact positions of differences

**Key pdf417gen functions for codeword inspection**:

| Function | Purpose |
|----------|---------|
| `compact_bytes(data)` | Data -> data codewords |
| `encode_rows(codewords, cols, ec_level)` | Codewords -> row patterns (with EC + indicators) |
| `compute_error_correction_code_words(cw, ec_level)` | Data codewords -> EC codewords |
| `render_image(codes, scale, ratio, padding)` | Row patterns -> PIL Image |
| `CODES` (3 clusters x 929 entries) | Row pattern lookup table |

### Portal Testing Matrix

Documented in `validation_data/PORTAL_TESTING.md`. Key variants that isolated the XObject naming issue:

| Test | Description | Result |
|------|-------------|--------|
| TW round-tripped through pypdf | Zero changes | ACCEPTED |
| TW with our PDF417 images swapped in | Only image data changed | ACCEPTED |
| Our reportlab pages with TW's exact barcodes | Only page structure changed | REJECTED |
| Variant B: rename XObjects to `/FormXob.*` | Only naming changed | REJECTED |
| Variants A/C/D/E/F: other structural changes | Various | All ACCEPTED |

### pypdf Gotcha: `set_data()` on Copied Pages

When a page is copied via `writer.add_page(page)`, calling `xobj.set_data()` on its XObjects may NOT persist. The writer can use the original data, causing a false positive where the "modified" file is actually unchanged. Always verify by extracting and inspecting the actual XObject data from the written PDF.

---

## 9. Quick Reference: Critical Parameters

### Portal-Critical (rejection if wrong)

```python
# XML
MINOR_VERSION = 22                    # Not "2"
COUNTRY = "CH"                        # Required on root element
STATEMENT_ID_LENGTH = 31              # CH + 5 + 14 + 8 + 2
DECIMAL_PLACES = 2                    # All monetary values

# Barcode encoding
PDF417_COLUMNS = 13
PDF417_ROWS = 35
PDF417_SECURITY_LEVEL = 4
CHUNK_SIZE_BYTES = 470                # Sequential chunking
ZLIB_LEVEL = 9                        # Must match TW's 78da header
SEGMENT_INDEX_PAD_WIDTH = 5           # Zero-pad before base-900
MACRO_SEGMENT_COUNT_FIELD = True      # Include on EVERY segment
MACRO_LAYOUT = "data-padding-macro"   # NOT data-macro-padding

# PDF structure
XOBJECT_NAMING = "/Im1, /Im2, ..."    # NOT /FormXob.<hash>
COLORSPACE = "DeviceGray"             # 1-bit black/white
BITS_PER_COMPONENT = 1
PAGE_ROTATE = 90                      # On every page

# CODE128C
ORIENTATION = 0                       # Querformat
READING_DIRECTION = 2                 # All pages
```

### Image Dimensions

```python
CODE128C_DIMS = (123, 46)     # pixels
PDF417_DIMS = (290, 35)       # pixels
```

### Macro PDF417 Codeword Constants

```python
MACRO_BEGIN = 928              # Start of macro control block
MACRO_TERMINATE = 922          # Last segment only
MACRO_FILENAME = 923           # Optional field indicator
FILENAME_FIELD_TYPE = 0        # fileName
SEGMENT_COUNT_FIELD_TYPE = 1   # segmentCount
BYTE_LATCH = 901               # Standard byte compaction
BYTE_LATCH_MOD6 = 924          # Mod-6 byte compaction
PADDING_CODEWORD = 900         # Fills unused data slots
```

---

## Appendix: Related Files

| File | Description |
|------|-------------|
| `doc/eCH-0196_Barcode_Generierung_Technische_Wegleitung.pdf` | Official eCH-0196 barcode spec (German) |
| `doc/eCH-0196_Technische_Wegleitung_v2.2.pdf` | Official eCH-0196 XML spec (German) |
| `validation_data/PORTAL_TESTING.md` | Detailed portal test matrix with 10+6 variants |
| `validation_data/extract_codewords.py` | Codeword extraction/comparison script |
| `user_data/2024/truewealth2024.pdf` | TrueWealth 2024 reference PDF (accepted by portal) |
