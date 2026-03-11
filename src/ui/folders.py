"""Folder management for user_data/ directory structure.

Each pipeline run gets its own folder:
    user_data/{provider}_{year}_{account}_{first}_{last}/input/
    user_data/{provider}_{year}_{account}_{first}_{last}/output/
"""

from __future__ import annotations

import os
import re

PROVIDER_ABBREVS = {"swissquote": "sq"}


def _sanitize(name: str) -> str:
    """Lowercase, replace non-alphanumeric chars with underscore."""
    return re.sub(r"[^a-z0-9]", "_", name.lower()).strip("_")


def build_folder_name(
    provider: str,
    tax_year: int,
    account: str,
    first_name: str,
    last_name: str,
) -> str:
    """Build folder name like 'sq_2025_123456_max_muster'."""
    abbrev = PROVIDER_ABBREVS.get(provider.lower(), _sanitize(provider))
    return f"{abbrev}_{tax_year}_{_sanitize(account)}_{_sanitize(first_name)}_{_sanitize(last_name)}"


def create_run_folders(base_dir: str, folder_name: str) -> tuple[str, str]:
    """Create input/ and output/ dirs under base_dir/folder_name.

    Returns (input_dir, output_dir).
    """
    input_dir = os.path.join(base_dir, folder_name, "input")
    output_dir = os.path.join(base_dir, folder_name, "output")
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    return input_dir, output_dir


def extract_account_number(filename: str) -> str | None:
    """Extract account number from portfolio PDF filename.

    'Portfolio-Wertentwicklung_123456_20260108.pdf' -> '123456'
    Uses regex to find a 5-10 digit numeric account ID between underscores.
    Returns None if no match.
    """
    match = re.search(r"_(\d{5,10})_", filename)
    return match.group(1) if match else None


def list_statements(base_dir: str) -> list[dict]:
    """List existing statement folders with metadata.

    Returns list of dicts with: folder, provider, year, account, has_output, output_file.
    """
    if not os.path.isdir(base_dir):
        return []

    statements = []
    for folder in sorted(os.listdir(base_dir), reverse=True):
        folder_path = os.path.join(base_dir, folder)
        if not os.path.isdir(folder_path):
            continue

        # Parse folder name: {provider}_{year}_{account}_{first}_{last}
        parts = folder.split("_", 3)
        if len(parts) < 3:
            continue

        provider = parts[0]
        try:
            year = int(parts[1])
        except ValueError:
            continue
        account = parts[2]

        # Check for output PDFs
        output_dir = os.path.join(folder_path, "output")
        output_file = None
        if os.path.isdir(output_dir):
            pdfs = [f for f in os.listdir(output_dir) if f.endswith(".pdf")]
            if pdfs:
                output_file = pdfs[0]

        statements.append({
            "folder": folder,
            "provider": provider,
            "year": year,
            "account": account,
            "has_output": output_file is not None,
            "output_file": output_file,
        })

    return statements
