"""One-time schema downloader for eCH-0196 XSD files.

Downloads the 5 required XSD schemas to schema/ and patches import
paths to use local relative references.

Usage:
    python -m src.generators.ech0196.download_schemas
"""

from __future__ import annotations

from pathlib import Path
from urllib.request import urlretrieve

SCHEMA_DIR = Path(__file__).parent / "schema"

SCHEMAS = {
    "eCH-0196-2-2.xsd": "https://share.ech.ch/xmlns/eCH-0196/2.2/eCH-0196-2-2.xsd",
    "eCH-0007-6-0.xsd": "https://share.ech.ch/xmlns/eCH-0007/6/eCH-0007-6-0.xsd",
    "eCH-0008-3-0.xsd": "https://share.ech.ch/xmlns/eCH-0008/3/eCH-0008-3-0.xsd",
    "eCH-0010-7-0.xsd": "https://share.ech.ch/xmlns/eCH-0010/7/eCH-0010-7-0.xsd",
    "eCH-0097-4-0.xsd": "https://share.ech.ch/xmlns/eCH-0097/4/eCH-0097-4-0.xsd",
}

# Maps remote schemaLocation URLs to local filenames
LOCATION_PATCHES = {
    url: filename for filename, url in SCHEMAS.items()
}


def download_schemas() -> None:
    """Download all schemas and patch import locations."""
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)

    for filename, url in SCHEMAS.items():
        target = SCHEMA_DIR / filename
        print(f"Downloading {filename}...")
        urlretrieve(url, target)

    # Patch the main schema's import schemaLocation attributes
    main_xsd = SCHEMA_DIR / "eCH-0196-2-2.xsd"
    content = main_xsd.read_text(encoding="utf-8")
    for remote_url, local_name in LOCATION_PATCHES.items():
        content = content.replace(
            f'schemaLocation="{remote_url}"',
            f'schemaLocation="{local_name}"',
        )
    main_xsd.write_text(content, encoding="utf-8")
    print("Schema locations patched to local paths.")


if __name__ == "__main__":
    download_schemas()
