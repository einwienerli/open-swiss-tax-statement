"""Taxpayer profile persistence.

Stores profile at ~/.opensteuerauszug/profile.json so PII stays
outside the project tree and survives reinstalls.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields

from src.models import TaxPayer

PROFILE_DIR = os.path.expanduser("~/.opensteuerauszug")
PROFILE_PATH = os.path.join(PROFILE_DIR, "profile.json")


@dataclass
class UserProfile:
    first_name: str = ""
    last_name: str = ""
    street: str = ""
    zip_code: str = ""
    city: str = ""
    canton: str = ""
    ahv_number: str = ""
    account_number: str = ""


def load_profile(path: str = PROFILE_PATH) -> UserProfile:
    """Load saved profile. Returns empty UserProfile if missing or corrupt."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        known = {field.name for field in fields(UserProfile)}
        return UserProfile(**{k: v for k, v in data.items() if k in known})
    except (OSError, json.JSONDecodeError, TypeError):
        return UserProfile()


def save_profile(
    taxpayer: TaxPayer,
    account_number: str = "",
    path: str = PROFILE_PATH,
) -> None:
    """Persist taxpayer details + account number to disk."""
    profile = UserProfile(
        first_name=taxpayer.first_name,
        last_name=taxpayer.last_name,
        street=taxpayer.street,
        zip_code=taxpayer.zip_code,
        city=taxpayer.city,
        canton=taxpayer.canton,
        ahv_number=taxpayer.ahv_number or "",
        account_number=account_number,
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(profile), f, ensure_ascii=False, indent=2)
