"""Admin CLI: prepare year-specific data (FX rates + Kursliste).

Usage:
    python -m src.admin.prepare_year --year 2024 \\
        --rates USD=0.906250,EUR=0.938450,GBP=1.135038 \\
        --kursliste /path/to/kursliste_2024.xml

Either --rates, --kursliste, or both may be provided.

FX rates are merged into ``src/enrichment/data/estv_rates.json``.
Kursliste is extracted to ``src/enrichment/data/kursliste_YYYY.json``.
"""

from __future__ import annotations

import argparse
import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "enrichment" / "data"
_RATES_FILE = _DATA_DIR / "estv_rates.json"


def parse_rates(raw: str) -> dict[str, str]:
    """Parse ``KEY=VALUE,...`` rate string into a dict.

    Values are kept as strings for JSON serialisation.
    Raises ``ValueError`` on malformed input.
    """
    result: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"Invalid rate pair (expected KEY=VALUE): {pair!r}")
        key, value = pair.split("=", 1)
        key = key.strip().upper()
        value = value.strip()
        # Validate it's a valid decimal
        try:
            Decimal(value)
        except InvalidOperation:
            raise ValueError(f"Invalid decimal value for {key}: {value!r}")
        result[key] = value
    if not result:
        raise ValueError("No rates parsed from input")
    return result


def update_rates_file(year: int, rates: dict[str, str]) -> None:
    """Merge rates for *year* into the bundled estv_rates.json."""
    if _RATES_FILE.exists():
        with _RATES_FILE.open() as f:
            data = json.load(f)
    else:
        data = {}

    data[str(year)] = rates

    with _RATES_FILE.open("w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Updated {_RATES_FILE} with {len(rates)} rates for {year}")


def extract_kursliste(year: int, xml_path: str, output_dir: Path | None = None) -> int:
    """Extract all entries from Kursliste XML and save as bundled JSON.

    Also extracts DA-1 rate data to a separate JSON file.

    Args:
        year: Tax year.
        xml_path: Path to the Kursliste XML file.
        output_dir: Directory for output JSON files. Defaults to bundled data dir.

    Returns:
        Number of securities extracted.
    """
    from src.enrichment.kursliste_parser import parse_da1_rates, parse_kursliste_all

    path = Path(xml_path)
    if not path.exists():
        raise FileNotFoundError(f"Kursliste XML not found: {xml_path}")

    dest = output_dir or _DATA_DIR

    print(f"Parsing Kursliste XML: {xml_path} ...")
    entries = parse_kursliste_all(xml_path)
    print(f"Extracted {len(entries)} securities")

    # Compact JSON format
    out: dict[str, dict] = {}
    for isin, entry in sorted(entries.items()):
        out[isin] = {
            "valor": entry.valor_number,
            "price": str(entry.year_end_price) if entry.year_end_price is not None else None,
            "quotation_type": entry.quotation_type,
            "currency": entry.currency,
            "country": entry.country,
            "security_group": entry.security_group,
            "security_type": entry.security_type,
            "tax_value_chf": str(entry.tax_value_chf) if entry.tax_value_chf is not None else None,
        }

    os.makedirs(dest, exist_ok=True)
    out_path = dest / f"kursliste_{year}.json"
    with out_path.open("w") as f:
        json.dump(out, f, ensure_ascii=False)
        f.write("\n")

    print(f"Saved {out_path} ({out_path.stat().st_size / 1024 / 1024:.1f} MB)")

    # Extract DA-1 rates
    print("Extracting DA-1 rates...")
    da1_rates = parse_da1_rates(xml_path)
    print(f"Extracted {len(da1_rates)} DA-1 rate entries (OTHER type)")

    da1_out: dict[str, dict] = {}
    for (country, security_group), rate in sorted(da1_rates.items()):
        key = f"{country}:{security_group}"
        da1_out[key] = {
            "withholding_rate": str(rate.withholding_rate) if rate.withholding_rate is not None else None,
            "release": str(rate.release) if rate.release is not None else None,
            "non_recoverable": str(rate.non_recoverable) if rate.non_recoverable is not None else None,
            "non_recoverable_max": str(rate.non_recoverable_max) if rate.non_recoverable_max is not None else None,
            "security_type": rate.security_type,
            "da1_rate_type": rate.da1_rate_type,
        }

    da1_path = dest / f"da1_rates_{year}.json"
    with da1_path.open("w") as f:
        json.dump(da1_out, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Saved {da1_path} ({da1_path.stat().st_size / 1024:.1f} KB)")

    return len(entries)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Prepare year-specific data (FX rates + Kursliste)"
    )
    parser.add_argument("--year", type=int, required=True, help="Tax year (e.g. 2024)")
    parser.add_argument(
        "--rates",
        type=str,
        help="Comma-separated FX rates: USD=0.906,EUR=0.938,...",
    )
    parser.add_argument(
        "--kursliste",
        type=str,
        help="Path to Kursliste XML file",
    )

    args = parser.parse_args(argv)

    if not args.rates and not args.kursliste:
        parser.error("At least one of --rates or --kursliste is required")

    if args.rates:
        rates = parse_rates(args.rates)
        update_rates_file(args.year, rates)

    if args.kursliste:
        extract_kursliste(args.year, args.kursliste)

    print("Done.")


if __name__ == "__main__":
    main()
