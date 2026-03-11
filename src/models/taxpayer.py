from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TaxPayer:
    """Natural person for whom the tax statement is generated."""

    first_name: str
    last_name: str
    street: str
    zip_code: str
    city: str
    canton: str  # 2-letter cantonal code, e.g. "ZH", "BE"
    ahv_number: str | None = None
