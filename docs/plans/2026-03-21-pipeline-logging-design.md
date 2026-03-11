# Pipeline Logging Design

## Goal

Add structured logging to capture pipeline progress and failure points for debugging, without logging PII. Logs should enable contributors to fix locale/asset class issues from a log file alone, without access to user files.

## Architecture

### New File

**`src/logging_config.py`** (~80 lines) — single module, no new dependencies.

- `setup_logging(debug=False)` — called once from `main.py`, configures root logger
- `SessionRotator` — trims log file to last 3 sessions on startup

### Log File

- **Location:** `~/Documents/OpenSwissTaxStatement/app.log`
- **Format:** `2026-03-21 14:30:00 [INFO] src.parsers.swissquote.csv_parser — Parsed 247 rows: 12 BUY, 8 SELL, 15 DIVIDEND, 3 skipped`
- **Session retention:** 3 sessions. On startup, scan for session markers, truncate everything before the 3rd-most-recent.
- **Session header:**
```
================================================================================
SESSION START — 2026-03-21 14:30:00 — v2025.0.9
================================================================================
```

### Log Levels

- **INFO** (default): pipeline progress, counts, flags, warnings, errors — written to file always
- **DEBUG** (`--debug` flag): per-ISIN processing, per-row parse details, XML element construction — also written to file + stderr
- **Console:** no console output in normal mode. With `--debug`, also logs to stderr.

## What Gets Logged (INFO level)

| Step | Log content |
|------|-------------|
| **Startup** | App version |
| **Broker** | Selected broker |
| **File selection** | Transaction input: type (CSV/Kontoauszug PDF) + file size (basename only, no full path). Portfolio PDF: file size |
| **Parsing — format** | Detected CSV locale (DE/EN) + header validation (match or mismatch + expected vs actual column count). For Kontoauszug: detected language |
| **Parsing — broker check** | Selected broker vs file format compatibility (header columns recognized or not) |
| **Parsing — results** | Row count, transaction count by type, skipped/storno row count, year range found in transactions |
| **Parsing — asset warnings** | Unrecognized portfolio sections with raw header text (e.g. "Actions en CHF"), transaction types that mapped to OTHER with raw type string |
| **Parsing — validation status** | Which matched labels are "confirmed" vs "unverified" per constants.py annotations, flagging first real usage of unverified mappings |
| **Portfolio** | Year identified in PDF, holdings count by currency, cash account count |
| **Portfolio — client info** | Extraction success flags: name Y/N, address Y/N, account Y/N (no actual values) |
| **Form validation** | Tax year (form) vs year (detected in files), required field groups populated Y/N (personal info / address / account / canton) |
| **Enrichment** | Kursliste source (bundled/user/missing), match rate (e.g. 42/45 securities), unmatched ISINs (list), missing FX rate currencies, DA-1 rates applied count |
| **XML build** | Holdings count, payments count (Rubrik A / B split), expenses count, cash count, XSD validation pass/fail |
| **Barcode** | Segment count, sheet count |
| **PDF output** | Page count, file size |
| **Pipeline** | Total duration |
| **Errors** | Full exception context at every catch point in api.py / pipeline.py (stack trace at WARNING/ERROR level) |

## What Never Gets Logged

Taxpayer names, addresses, AHV numbers, account numbers, IBANs, file paths containing usernames. File paths logged as basename only.

## Self-Service Locale/Asset Debugging

Key design goal: a user in an unsupported locale (FR/IT) can submit their log file, and a contributor can add support without accessing any user documents.

### What the log captures on mismatch

- Unrecognized portfolio section headers (e.g. `"Actions en CHF"`, `"Fonds de placement en EUR"`)
- Unrecognized cash/liquidity headers (e.g. `"Liquidites"`)
- Unrecognized Kontoauszug title/period patterns (e.g. `"Releve de compte"`)
- Unknown transaction type keywords (e.g. `"Bezugsrecht"`)
- Unrecognized detail keys in Kontoauszug blocks

### What the log does NOT capture

Row content, amounts, names, dates — only the structural label/keyword that failed to match.

### Validation status tracking

Constants in `src/parsers/swissquote/constants.py` carry `# confirmed` / `# unverified` annotations. The log reports which mappings were exercised and whether they're confirmed or unverified:

```
INFO — Portfolio sections matched: Aktien (confirmed), Obligationen (unverified)
```

This tells contributors which mappings need real-world validation.

### Workflow

1. User in unsupported locale runs the app, hits parsing errors
2. User submits `app.log` via GitHub issue
3. Contributor reads log: sees raw section headers, transaction types, detail keys
4. Contributor adds missing labels to `constants.py`
5. New locale/asset class supported — no PII exchanged

## Implementation Scope

### New file
- `src/logging_config.py` — setup, session rotation, file handler

### Modified files — add log calls
- `src/main.py` — call `setup_logging()`, log session start + version
- `src/ui/api.py` — log broker selection, file picks (type+size), form validation flags, error catch points
- `src/ui/pipeline.py` — log enrichment summary, pipeline duration, error wrapping
- `src/parsers/swissquote/csv_parser.py` — log locale detection, header validation, parse summary, unknown types
- `src/parsers/swissquote/portfolio_parser.py` — log year, section detection with validation status, client info flags, unrecognized sections
- `src/parsers/swissquote/kontoauszug_parser.py` — log language, parse summary, unrecognized labels
- `src/generators/ech0196/xml_builder.py` — log build summary counts, XSD result
- `src/generators/pdf/assembler.py` — log page count, file size
- `src/generators/barcode/orchestrator.py` — log segment/sheet count

### Tests
- Test session rotation (3-session retention, correct trimming)
- Test PII absence (log output from a full pipeline run doesn't contain taxpayer fields)
- Test log file creation in correct location
- Test debug vs normal log level behavior
