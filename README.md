# Open Swiss Tax Statement

[![Lint](https://github.com/einwienerli/open-swiss-tax-statement/actions/workflows/lint.yml/badge.svg)](https://github.com/einwienerli/open-swiss-tax-statement/actions/workflows/lint.yml)
[![Tests](https://github.com/einwienerli/open-swiss-tax-statement/actions/workflows/test.yml/badge.svg)](https://github.com/einwienerli/open-swiss-tax-statement/actions/workflows/test.yml)
[![100% Offline](https://github.com/einwienerli/open-swiss-tax-statement/actions/workflows/offline.yml/badge.svg)](https://github.com/einwienerli/open-swiss-tax-statement/actions/workflows/offline.yml)

Free, open-source tool that generates official Swiss electronic tax statements (E-Steuerauszug) from your broker's free exports. Saves CHF 85-100 compared to ordering one from your broker.

> **Disclaimer:** This is a hobby project. It has only been tested with a small number of Swissquote portfolios (stocks, ETFs, cash) and only verified against the Zurich cantonal tax portal (ZHprivateTax). Other cantons should work if they use the same eCH-0196 standard, but this has not been confirmed. There is no guarantee of correctness or completeness — always review the generated statement before submitting it to your tax authority.

## How It Works

Upload your broker's transaction export and portfolio PDF. The app enriches your data with official ESTV tax values and exchange rates, then generates a PDF with machine-readable barcodes that your cantonal tax portal can process electronically — the same format brokers charge CHF 85-100 to produce.

## Download

Download the latest `.dmg` from [GitHub Releases](https://github.com/einwienerli/open-swiss-tax-statement/releases).

> **macOS note:** The app is not code-signed. macOS will block it on first launch. To open it:
> 1. Open the `.dmg` and drag the app to Applications
> 2. Try to open the app — macOS will show a warning and refuse
> 3. Go to **System Settings > Privacy & Security**, scroll down, and click **Open Anyway**
> 4. Confirm in the dialog that appears
>
> You only need to do this once. After that, the app opens normally.

## Using the App

The app guides you through 6 steps:

1. **Broker** — Select your broker (currently Swissquote)
2. **Files** — Select your transaction export and portfolio PDF
3. **Tax Year** — Confirm the tax year and ICTAX data availability
4. **Details** — Review your personal details (auto-populated from portfolio PDF)
5. **Preview** — Review parsed holdings, dividends, cash balances, and trades
6. **Generate** — Save your E-Steuerauszug PDF

Upload the generated PDF to your cantonal tax portal (e.g. ZHprivateTax) — done.

## Getting Your Broker Exports

### Swissquote

You need two files from Swissquote (both free):

**Transaction CSV:** Log in &rarr; Transactions &rarr; Export as CSV (select the full tax year)

**Portfolio PDF:** Log in &rarr; Reports &rarr; Portfolio Performance Report (select year-end date, e.g. 31.12.2025). This provides accurate year-end holdings and cash balances, and auto-populates your name, address, and account number.

**Alternative — Kontoauszug PDF:** Log in &rarr; Documents &rarr; Account Statement. Can be used instead of the CSV. Contains explicit transaction amounts and correct dividend share counts.

## Supported Brokers

| Broker | Status |
|--------|--------|
| **Swissquote** | Full support |
| Interactive Brokers | Planned |
| Degiro | Planned |

## Languages

English, German, French, and Italian.

## Privacy & Security

**Offline by default.** The app makes zero network calls during normal operation. All parsing, enrichment, and PDF generation happen locally on your computer.

**No telemetry.** No analytics, tracking, crash reporting, or data collection. Ever.

**Update check is manual.** Click "Check for updates" in the page footer to check for new versions. This sends a single HTTPS request to `api.github.com` (no personal data). If you don't click, the app is fully offline.

**Your files stay on your computer.** Generated PDFs and preferences are stored in `~/Documents/OpenSwissTaxStatement/`. Delete the folder at any time to remove all data.

**Machine-verifiable.** The test suite includes an [offline verification test](tests/test_offline.py) that scans all source code for network-capable imports (`socket`, `requests`, `urllib`, etc.) and asserts none are used in the core app. This runs automatically on every commit — see the "100% Offline" badge above.

**Open-source dependencies** — lxml (BSD), reportlab (BSD), pypdf (BSD), pdf417gen (MIT), pywebview (BSD), Pillow (MIT). All AGPL-compatible.

## For Developers

### Run from Source

Requires Python 3.11+. No native dependencies — all PDF parsing and barcode generation is pure Python.

```bash
git clone https://github.com/einwienerli/open-swiss-tax-statement.git
cd open-swiss-tax-statement
pip install -e ".[dev]"

python run.py          # Native desktop window
python run.py --debug  # With developer tools
```

### Tests

```bash
pytest                 # 599 tests covering the full pipeline
```

### Project Structure

```
src/
├── models/          # Domain model (TaxStatement, Security, Holding, etc.)
├── parsers/
│   └── swissquote/  # CSV, Kontoauszug PDF, Portfolio PDF parsers
├── enrichment/      # ICTAX Kursliste, ESTV exchange rates, rounding
├── generators/
│   ├── ech0196/     # eCH-0196 v2.2 XML builder + XSD validator
│   ├── barcode/     # CODE128C + Macro PDF417 barcode generation
│   └── pdf/         # PDF assembly (reportlab + pypdf)
├── ui/              # pywebview desktop UI (HTML/CSS/JS + Python API)
└── admin/           # Year-end data preparation tools
```

### Preparing Data for a New Tax Year

Each release bundles ICTAX Kursliste data for the supported tax years. To add a new year:

1. Download the Kursliste V2.2 XML from [ICTAX](https://www.ictax.admin.ch)
2. Run: `python -m src.admin.prepare_year --year YYYY --kursliste /path/to/kursliste.xml`

### Technical Notes

- eCH-0196 v2.2 XML, XSD-validated
- Barcodes per eCH-0196 Technische Wegleitung (CODE128C + Macro PDF417)
- CHF tax values using ESTV exchange rates with DIN 1333 rounding
- Verified against the Zurich cantonal tax portal

## License

[AGPL-3.0](LICENSE)

- You can use, modify, and share this software freely
- If you distribute a modified version or run it as a service, you must share your source code under the same license
- Commercial use is allowed, but you cannot make it closed-source — any version you distribute or host must remain open
