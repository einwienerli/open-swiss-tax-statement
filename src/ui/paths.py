"""User data directory paths for persistent data storage.

Stores user data in ~/Documents/OpenSwissTaxStatement/ — a visible, user-facing
location since generated PDFs are needed for tax filings and accountants.

Directory structure:
    ~/Documents/OpenSwissTaxStatement/
        statements/     — generated PDFs + input copies
        data/           — kursliste JSON, DA-1 rates, etc.
        preferences.json
"""

from __future__ import annotations

import os
import platform

_APP_NAME = "OpenSwissTaxStatement"


def get_app_support_dir() -> str:
    """Return the user data directory path.

    macOS: ~/Documents/OpenSwissTaxStatement/
    Other: ~/.openswisstaxstatement/
    """
    if platform.system() == "Darwin":
        base = os.path.expanduser("~/Documents")
    else:
        base = os.path.expanduser("~")
        _APP_NAME_LOWER = f".{_APP_NAME.lower()}"
        return os.path.join(base, _APP_NAME_LOWER)
    return os.path.join(base, _APP_NAME)


def get_statements_dir() -> str:
    """Return the statements directory for generated PDFs + input copies."""
    return os.path.join(get_app_support_dir(), "statements")


def get_data_dir() -> str:
    """Return the data directory for kursliste JSON, DA-1 rates, etc."""
    return os.path.join(get_app_support_dir(), "data")


def get_preferences_path() -> str:
    """Return the path to preferences.json."""
    return os.path.join(get_app_support_dir(), "preferences.json")


def ensure_dirs() -> None:
    """Create all required directories if they don't exist."""
    os.makedirs(get_statements_dir(), exist_ok=True)
    os.makedirs(get_data_dir(), exist_ok=True)
