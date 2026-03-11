# Open Swiss Tax Statement

## Purpose

Generate eCH-0196-compliant E-Steuerauszug (Swiss tax statement PDF with barcodes) from free broker CSV exports. Saves CHF 85-100 vs broker-charged statements. Licensed under AGPL-3.0.

## Architecture

Multi-provider model with a shared domain model and broker-specific parsers. Swissquote is the MVP provider.

**Pipeline:** CSV Parse -> ICTAX Enrichment -> eCH-0196 XML Build -> Barcode Generation -> PDF Assembly

The domain model is the core abstraction: all parsers produce `TaxStatement`, all generators consume it.

## Technology Stack

Python 3.11+, pywebview (JS API bridge, no HTTP server), lxml, reportlab, pdf417gen, pypdf, Pillow, pytest

## Running

```bash
python run.py          # Native window
python run.py --debug  # Native window with dev tools
```

## Testing

```bash
pytest                 # Run from project root
```

## Building

```bash
scripts/build.sh        # Build .app bundle (requires PyInstaller: pip install -e ".[dev]")
scripts/build_dmg.sh    # Create DMG disk image from built .app
```

Output in `dist/`. The .app is unsigned (macOS Gatekeeper warning; bypass with right-click → Open).

PyInstaller config: `OpenSwissTaxStatement.spec`. Bundles all data (UI assets, XSD schemas, kursliste JSON). Uses `file://` protocol to avoid localhost HTTP server and macOS local network prompt. Frozen-mode path resolution in `src/main.py:_get_project_root()`.

## Releasing

CalVer versioning (`YYYY.N.N`). Single source of truth: `src/__init__.py:__version__`.

```bash
scripts/release.sh 2025.3.0    # Bump version, commit, tag, push
```

The script:
1. Validates CalVer format, clean working tree, on `main` branch
2. Updates `__version__` in `src/__init__.py`
3. Commits as `release: vX.Y.Z`, creates git tag `vX.Y.Z`, pushes both

Pushing the `v*` tag triggers `.github/workflows/release.yml`:
1. **Lint** — `ruff check src/ tests/`
2. **Test** — `pytest --cov` on Ubuntu
3. **Verify offline** — `tests/test_offline.py` (network import scan)
4. **Build** — macOS runner: validates tag matches `__version__`, builds `.app` via PyInstaller, creates DMG, generates SHA256 checksums
5. **Release** — creates a **draft** GitHub Release with DMG + checksums (review and publish manually)

## Key Directories

- `src/models/` -- Domain model (dataclasses, shared types)
- `src/parsers/` -- Broker-specific parsers (e.g. `src/parsers/swissquote/`)
- `src/enrichment/` -- ICTAX lookup and FX rate enrichment
- `src/generators/ech0196/` -- eCH-0196 XML builder, XSD validator, schema files
- `src/generators/barcode/` -- Barcode generation (CODE128C, PDF417)
- `src/generators/pdf/` -- PDF assembly
- `src/ui/` -- pywebview API class, static HTML + JS + CSS
- `tests/` -- pytest test suite

## Swissquote Parser

The parser in `src/parsers/swissquote/` handles the real Swissquote 15-column CSV export format plus optional portfolio PDF integration.

```python
from src.parsers.swissquote import SwissquoteCsvParser, PortfolioParser

parser = SwissquoteCsvParser()
statement = parser.parse(csv_text, tax_year=2025, taxpayer=taxpayer)

# With portfolio PDF for accurate year-end holdings/cash:
statement = parser.parse(csv_text, 2025, taxpayer, portfolio_content=pdf_text)
```

### CSV Format (15 columns)
`Datum;Auftrag #;Transaktionen;Name;ISIN;Symbol;Anzahl;Stückpreis;Währung Stückpreis;Kosten;Aufgelaufene Zinsen;Nettobetrag;Währung Nettobetrag;Saldo;Währung Saldo`

- Date format: `DD-MM-YYYY HH:MM:SS`
- ISIN, name, symbol are in dedicated columns (not parsed from transaction text)
- WHT is in the Kosten column on DIVIDEND rows: `WHT = abs(costs)`, `gross = net + WHT`
- Storno handling: all transactions sharing an order number with a storno row are removed
- Special value `"-"` in numeric fields is treated as zero
- Supported transaction types: Kauf/Buy, Verkauf/Sell, Dividende/Dividend, Zinsen auf Einlagen/Interest, Depotgebühren/Custody Fee, Devisengutschrift/FX Credit, Devisenbelastung/FX Debit, Storno, Ausgabe von Anrechten

### Portfolio PDF Parser
Parses Swissquote "Portfolio-Wertentwicklung" PDF (via pypdf layout mode) for year-end holdings and cash balances. Detects section headers (e.g. "Aktien in CHF", "Anlagefonds in EUR", "Liquidität") and extracts security positions and cash accounts. Also extracts client info (name, address, account number, year) from the cover page via `extract_client_info()` to auto-populate the UI form. No external binaries required (pypdf is pure Python).

## eCH-0196 XML Builder

The XML builder in `src/generators/ech0196/` transforms a `TaxStatement` into XSD-valid eCH-0196 v2.2 XML.

```python
from src.generators.ech0196 import build_xml, serialize, validate_xml

root = build_xml(statement)      # TaxStatement -> lxml Element
xml = serialize(root)            # Element -> XML string
result = validate_xml(xml)       # Validate against XSD (returns ValidationResult)
```

- XSD schemas are stored locally in `src/generators/ech0196/schema/` with patched import paths
- Security category heuristic: name keywords (ETF/VANGUARD/ISHARES/FUND) -> FUND, else SHARE
- Income category heuristic: CH ISIN + WHT > 0 -> RUBRIK_A, else RUBRIK_B
- TIN field: Swiss AHV numbers (13 digits) exceed XSD maxLength=11, so are omitted
- Re-download schemas: `python -m src.generators.ech0196.download_schemas`

## ICTAX Kursliste Parser (V2.2)

The kursliste parser in `src/enrichment/kursliste_parser.py` extracts valor numbers and year-end tax prices from the ICTAX Kursliste XML (V2.2 format).

```python
from src.enrichment.kursliste_parser import parse_kursliste, parse_kursliste_indexed

# Stream parse (for full-file scan)
entries = parse_kursliste("kursliste.xml", {"CH0038863350", "IE00B3XXRP09"})

# Index-based O(1) lookup (requires companion .ix file)
entries = parse_kursliste_indexed("kursliste.xml", "kursliste.ix", isins)
```

### V2.2 Format Details
- Namespace: `http://xmlns.estv.admin.ch/ictax/2.2.0/kursliste`
- Security elements: `<share>`, `<fund>`, `<bond>` with attributes `securityGroup`, `securityType`
- Year-end data: `<yearend>` element with `taxValue` (original currency), `taxValueCHF` (pre-computed CHF), `exchangeRate`
- Foreign securities may have only `taxValueCHF` (no original-currency price)
- ISO date format: `YYYY-MM-DD`

### .ix Index File
Companion file with format: `valorNumber;ISIN;byteOffset;byteLength`
Enables O(1) random access instead of streaming through 363MB XML.

### Enricher Integration
The enricher (`src/enrichment/enricher.py`) prefers `taxValueCHF` from the Kursliste when available (ESTV's authoritative CHF conversion), falling back to `year_end_price * fx_rate` when absent.

### Bundled JSON
Pre-extracted kursliste data is stored in `src/enrichment/data/kursliste_YYYY.json`. Without this file, the enricher silently skips valor numbers, tax prices, and unitPrice — the XML output will be unenriched.

**Yearly preparation (required for each new tax year):**
1. Download the Kursliste V2.2 XML from ICTAX for the tax year
2. Place it in `data/kursliste_YYYY-v2.2/kursliste_YYYY.xml`
3. Generate the bundled JSON:
```bash
python -m src.admin.prepare_year --year YYYY --kursliste data/kursliste_YYYY-v2.2/kursliste_YYYY.xml
```
4. Verify: `src/enrichment/data/kursliste_YYYY.json` should exist (~24MB, ~120K securities)

**Source data locations** (not checked into git):
- `data/kursliste_YYYY-v2.2/` — Kursliste V2.2 XML + .ix index + XSD
- `data/kursliste_YYYY/` — Kursliste V1.2 XML + XSD (legacy, not used)
- `data/devisen kursliste_YYYY.pdf` — ESTV FX rates PDF (reference only; rates are in `src/enrichment/data/estv_rates.json`)

## Web UI

6-step wizard: Broker -> Files -> Tax Year -> Details -> Preview -> Complete. Built with pywebview JS API bridge + Pico CSS.

Architecture: `JS -> pywebview.api.method() -> Python Api class -> pipeline`. No HTTP server, no Flask.

- **Landing page (Step 1)**: Hero title + 5 key-attribute cards above step indicator (visible on Step 1 only). Lean broker cards (name only, no icon circles). Step label: "Broker".
- **Tax Year (Step 3)**: Detects year from uploaded files, checks Kursliste availability (bundled vs user vs missing). If missing: warning with year number, link to ICTAX XML download page, file select button (equal-sized). Continue button turns yellow (caution) when kursliste not loaded; clicking it shows a warning dialog with year number before allowing proceed.
- **Dialogs**: Unified `.popup-dialog` system — `.popup-header` (title bar + close button), `.popup-body` (scrollable content), `.popup-footer` (action buttons). Variants: `.popup-wide` (900px, history), `.popup-lg` (700px, FAQ), `.popup-header-warning` (amber). All use native `<dialog>` with backdrop-click-to-close. Pico CSS override: `flex-direction: column` on `.popup-dialog`.
- **Header**: Brand (OPEN / Swiss Tax Statement) + uniformly-styled pill buttons (EN|DE|FR|IT, FAQ, Previous Statements with badge)
- **Update banner**: Info-styled banner between header and wizard when a new version is available (opt-in check via GitHub Releases API)
- Native OS file dialogs for CSV and portfolio PDF selection (no drag-and-drop, no HTML file inputs)
- Portfolio PDF auto-extracts client info (name, address, account, year) to pre-populate form fields
- Preview step parses/enriches data and shows holdings, cash, payments, expenses, trades for review
- Generate step produces PDF; native Save dialog for export + archive in `~/Documents/OpenSwissTaxStatement/statements/`
- `Api` class in `src/ui/api.py` is stateless — JS stores file paths and passes them to preview/generate
- Static HTML in `src/ui/index.html` (no templating engine)
- Cache-busting via `?v=N` query params on static assets (CSS v23, JS v17)
- Status bar hidden via CSS (JS still writes to it for debugging)
- **i18n**: 4-language support (EN/DE/FR/IT) via JSON translation files in `src/ui/static/i18n/`. Lightweight JS module (`src/ui/static/js/i18n.js`) with `data-i18n` HTML attributes. Language detection: localStorage > browser locale > English fallback. `setLanguage()` saves preference and reloads page.
- **Branding**: App constants in `src/__init__.py` (version, display name, author, license, repo). PDF provenance footer on every page. Single-line page footer: `v{version} · GitHub · Security & Privacy · Check for updates` (replaces About dialog).

### Data Persistence

User data is stored in `~/Documents/OpenSwissTaxStatement/` — a visible, user-facing location since generated PDFs are needed for tax filings and accountants:
- `statements/` — generated PDFs + input file copies (formerly `user_data/`)
- `data/` — user-provided kursliste JSON (overrides bundled data)
- `preferences.json` — app preferences (currently unused, may contain legacy keys)

On first launch, legacy directories are automatically migrated: `~/Library/Application Support/OpenSwissTaxStatement/` (previous location) and `~/Library/Application Support/OpenESteuerauszug/` (original name).

### Update Checker

User-triggered update check via "Check for updates" link in page footer (`src/ui/updater.py`):
- No startup dialog, no automatic checks — fully offline by default
- Click "Check for updates" → single HTTPS request to `api.github.com`
- If update available: info banner appears below header with version + release link
- If up to date: link shows "Up to date ✔" for 3 seconds, then reverts
- No stored consent, no background thread, no cached results across sessions
- Uses `urllib.request` (stdlib) — excluded from offline test via `EXCLUDED_FILES`

### UI Pipeline

The preview/summary pipeline in `src/ui/pipeline.py` converts a `TaxStatement` into a JSON-serializable dict for the web UI.

- Trades summary includes securities from `holdings`, `security_payments`, and securities that were bought during the year (fully sold positions). This excludes corporate actions like rights issuances (no BUY, only SELL) that have no year-end holding and no dividend income.
- Capital gains from rights sales are tax-free for Swiss private investors and don't belong in the E-Steuerauszug.
- Fully sold positions (bought and sold within the year, no year-end holding) appear in the XML with `quantity="0"` and `value="0.00"` on their taxValue, matching TrueWealth's format.

## Barcode Compliance Review

Systematic review against `doc/eCH-0196_Barcode_Generierung_Technische_Wegleitung.pdf` and the working TrueWealth 2024 reference PDF (`user_data/2024/truewealth2024.pdf`).

### Review Progress

| Section | Spec Reference | Status |
|---------|---------------|--------|
| Statement ID (§2.1) | XML `id` attribute, Macro PDF417 FileName | Verified — 31-char format matches TrueWealth |
| CODE128C identifier (§2.4a) | 16-digit value per page | Verified — all 6 pages produce correct values |
| CODE128C image (§2.4b) | 123×46px, 1-bit B&W, bars + text | Rewritten from scratch, bit-for-bit match with TrueWealth |
| CODE128C quiet zone (§2.4b) | 5mm around barcode image | Fixed — MARGIN_LEFT increased from 20mm to 25mm (7mm gap) |
| PDF417 image (§2.2) | 290×35px, Macro PDF417, 13 cols, 35 rows, EC4 | Verified — codeword-identical to TrueWealth, portal accepted |
| Barcode sheet layout (§2.3) | 6 per sheet, portrait+/Rotate=90, 97% scaling | Verified — 2×3 grouped layout, fixed grid positions match TrueWealth within 0.1pt |
| XML extraction (§2.5) | XObject image extraction by portal | Verified — DeviceGray BPC=1, /Im sequential naming, portal accepted |

### Barcode Tooling

**Writing:**
- **CODE128C**: Custom pure-Python implementation (`src/generators/barcode/code128c.py`). No external barcode library — encodes Code 128 Subset C from the ISO/IEC 15417 pattern table at 1 pixel per module. Renders 123×46 1-bit image with 5×7 bitmap font for human-readable number. Bar pattern verified bit-for-bit against TrueWealth reference.
- **Macro PDF417**: Uses `pdf417gen` library for codeword-to-row encoding (`src/generators/barcode/macro_pdf417.py`). Custom macro control block assembly (segment index, file ID, fileName, segmentCount). Codeword-identical to TrueWealth/J4L. Layout: `[LD][byte_data][padding][macro_control]`. Segment index/count zero-padded to 5 digits. Sequential chunking at 470 bytes. Renders at 290×35 pixels (scale=1, ratio=1, padding=0).
- **PDF assembly**: reportlab generates portrait A4 pages with landscape CTM transform. pypdf post-processing adds `/Rotate=90` on all pages. Barcodes placed unrotated in portrait coordinates via inverse CTM. PDF417 barcode sheets use a fixed 6-slot grid (2 groups of 3, spacing = multiples of unit G where 12G = available space). Partial pages fill top-down, leaving bottom slots empty.

**Reading/Verification:**
- **zxing-cpp** (Python bindings `zxingcpp`): Decodes both CODE128C and PDF417 from PIL images. Used for roundtrip verification of generated barcodes.
- **pypdf**: Extracts XObject images from PDF for inspection (dimensions, BPC, colorspace, raw pixel data). Also used for PDF text extraction (`extraction_mode="layout"`) in the portfolio parser — replaces pdfplumber (pure Python, zero native deps).

## Coding Conventions

- Python 3.11+ features are allowed
- Dataclasses for all models
- Type hints everywhere
- pytest for all tests

## Tax Rules

- All tax values must be in CHF
- Use ESTV official exchange rates for currency conversion
- Apply DIN 1333 rounding (nearest 0.05 CHF)

## Swissquote Constants

- UID: CHE-106.787.734
- Clearing number: 08750

## TODO

- [ ] **Force push initial commit** — Before making repo public, squash all commits into a single initial commit to remove PII (personal names, account numbers) from git history. Run after testing with second SQ account and IBKR account is complete.
