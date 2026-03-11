"""Memory-efficient parser for ICTAX Kursliste XML V2.2 (363MB+).

Uses lxml iterparse to stream-parse and extract only entries for
requested ISINs, keeping memory usage low.  Also supports:
- Index-based random access via companion .ix file
- Loading pre-extracted bundled Kursliste JSON files per year
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from lxml import etree

logger = logging.getLogger(__name__)

# Kursliste V2.2 XML namespace
_NS = "http://xmlns.estv.admin.ch/ictax/2.2.0/kursliste"
_SECURITY_TAGS = {f"{{{_NS}}}share", f"{{{_NS}}}fund", f"{{{_NS}}}bond"}
_DA1_TAG = f"{{{_NS}}}da1Rate"


@dataclass
class KurslisteEntry:
    """Extracted data for a single security from the Kursliste."""

    valor_number: str
    isin: str
    security_group: str
    security_type: str
    currency: str
    country: str
    year_end_price: Decimal | None
    quotation_type: str
    tax_value_chf: Decimal | None = None
    exchange_rate: Decimal | None = None


@dataclass
class Da1Rate:
    """DA-1 withholding tax rate for a country/securityGroup combination.

    Used to compute non-recoverable foreign WHT and lump-sum tax credit
    amounts for Rubrik B payments (DA-1 form filing).
    """

    country: str
    security_group: str  # SHARE, FUND, BOND
    security_type: str  # "" if not specified
    da1_rate_type: str  # "OTHER" is the default
    withholding_rate: Decimal | None  # "value" attribute — treaty WHT %
    release: Decimal | None  # tax relief %
    non_recoverable: Decimal | None  # non-reclaimable %
    non_recoverable_max: Decimal | None  # max treaty rate %


def _decimal_or_none(value: str | None) -> Decimal | None:
    """Parse a string to Decimal, returning None on failure."""
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _parse_element(elem: etree._Element) -> KurslisteEntry:
    """Extract a KurslisteEntry from a V2.2 security element."""
    year_end_price = None
    tax_value_chf = None
    exchange_rate = None
    quotation_type = "PIECE"

    ye = elem.find(f"{{{_NS}}}yearend")
    if ye is not None:
        year_end_price = _decimal_or_none(ye.get("taxValue"))
        tax_value_chf = _decimal_or_none(ye.get("taxValueCHF"))
        exchange_rate = _decimal_or_none(ye.get("exchangeRate"))
        qt = ye.get("quotationType")
        if qt:
            quotation_type = qt

    return KurslisteEntry(
        valor_number=elem.get("valorNumber", ""),
        isin=elem.get("isin", ""),
        security_group=elem.get("securityGroup", ""),
        security_type=elem.get("securityType", ""),
        currency=elem.get("currency", ""),
        country=elem.get("country", ""),
        year_end_price=year_end_price,
        quotation_type=quotation_type,
        tax_value_chf=tax_value_chf,
        exchange_rate=exchange_rate,
    )


def parse_kursliste(
    xml_path: str, isins: set[str]
) -> dict[str, KurslisteEntry]:
    """Stream-parse Kursliste XML, returning entries for requested ISINs only.

    Args:
        xml_path: Path to the Kursliste XML file.
        isins: Set of ISIN codes to extract.

    Returns:
        Dict mapping ISIN to KurslisteEntry for each found ISIN.
    """
    if not isins:
        return {}

    result: dict[str, KurslisteEntry] = {}
    remaining = set(isins)

    context = etree.iterparse(
        xml_path, events=("end",), tag=[t for t in _SECURITY_TAGS]
    )

    for _, elem in context:
        isin = elem.get("isin")
        if isin not in remaining:
            elem.clear()
            continue

        entry = _parse_element(elem)
        result[isin] = entry
        remaining.discard(isin)

        elem.clear()

        if not remaining:
            break

    return result


def parse_kursliste_all(xml_path: str) -> dict[str, KurslisteEntry]:
    """Stream-parse Kursliste XML, returning ALL security entries.

    Unlike ``parse_kursliste``, this extracts every security without
    filtering by ISIN.  Used by the admin CLI to build bundled JSON.

    Args:
        xml_path: Path to the Kursliste XML file.

    Returns:
        Dict mapping ISIN to KurslisteEntry for every security in the file.
    """
    result: dict[str, KurslisteEntry] = {}

    context = etree.iterparse(
        xml_path, events=("end",), tag=[t for t in _SECURITY_TAGS]
    )

    for _, elem in context:
        isin = elem.get("isin")
        if not isin:
            elem.clear()
            continue

        entry = _parse_element(elem)
        result[isin] = entry
        elem.clear()

    return result


def parse_kursliste_indexed(
    xml_path: str, ix_path: str, isins: set[str]
) -> dict[str, KurslisteEntry]:
    """Look up specific ISINs using the .ix index file for O(1) access.

    The .ix companion file has lines: ``valorNumber;ISIN;byteOffset;byteLength``
    This allows seeking directly to each security's XML fragment instead of
    streaming through the entire 363MB file.

    Falls back to ``parse_kursliste`` if the .ix file doesn't exist.

    Args:
        xml_path: Path to the Kursliste XML file.
        ix_path: Path to the .ix index file.
        isins: Set of ISIN codes to extract.

    Returns:
        Dict mapping ISIN to KurslisteEntry for each found ISIN.
    """
    if not isins:
        return {}

    ix = Path(ix_path)
    if not ix.exists():
        logger.info("Index file %s not found, falling back to stream parse", ix_path)
        return parse_kursliste(xml_path, isins)

    # Build index: ISIN -> (offset, length)
    index: dict[str, tuple[int, int]] = {}
    with ix.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(";")
            if len(parts) < 4:
                continue
            isin = parts[1]
            if isin in isins:
                try:
                    offset = int(parts[2])
                    length = int(parts[3])
                    index[isin] = (offset, length)
                except (ValueError, IndexError):
                    continue

    if not index:
        return {}

    # Read and parse each fragment.
    # Fragments may lack the namespace declaration (inherited from root in the
    # original file), so we wrap them in a namespace-aware root if needed.
    ns_prefix = f' xmlns="{_NS}"'.encode()
    result: dict[str, KurslisteEntry] = {}
    with open(xml_path, "rb") as f:
        for isin, (offset, length) in index.items():
            f.seek(offset)
            fragment = f.read(length)
            # Inject namespace if the fragment doesn't already declare it
            if b"xmlns=" not in fragment:
                # Insert xmlns after the opening tag name: <share ... -> <share xmlns="..." ...
                first_space = fragment.find(b" ")
                if first_space != -1:
                    fragment = fragment[:first_space] + ns_prefix + fragment[first_space:]
            try:
                elem = etree.fromstring(fragment)
                entry = _parse_element(elem)
                result[isin] = entry
            except etree.XMLSyntaxError:
                logger.warning("Failed to parse XML fragment for ISIN %s at offset %d", isin, offset)
                continue

    return result


def _parse_da1_rate_element(elem: etree._Element) -> Da1Rate:
    """Extract a Da1Rate from a V2.2 <da1Rate> element."""
    return Da1Rate(
        country=elem.get("country", ""),
        security_group=elem.get("securityGroup", ""),
        security_type=elem.get("securityType", ""),
        da1_rate_type=elem.get("da1RateType", "OTHER"),
        withholding_rate=_decimal_or_none(elem.get("value")),
        release=_decimal_or_none(elem.get("release")),
        non_recoverable=_decimal_or_none(elem.get("nonRecoverable")),
        non_recoverable_max=_decimal_or_none(elem.get("nonRecoverableMax")),
    )


def parse_da1_rates(xml_path: str) -> dict[tuple[str, str], Da1Rate]:
    """Stream-parse all <da1Rate> elements from Kursliste XML.

    Filters to ``da1RateType="OTHER"`` (the default rate for private
    investors without participation thresholds).  Keyed by
    ``(country, securityGroup)``.

    When multiple entries exist for the same key (e.g. with and without
    securityType), the entry WITHOUT securityType (broadest match) is
    preferred.

    Args:
        xml_path: Path to the Kursliste XML file.

    Returns:
        Dict mapping (country, securityGroup) to Da1Rate.
    """
    result: dict[tuple[str, str], Da1Rate] = {}

    context = etree.iterparse(xml_path, events=("end",), tag=[_DA1_TAG])

    for _, elem in context:
        rate = _parse_da1_rate_element(elem)
        elem.clear()

        # Only keep the default OTHER rate type (private investors)
        if rate.da1_rate_type != "OTHER":
            continue

        key = (rate.country, rate.security_group)
        # Prefer the entry without securityType (broadest match)
        if key not in result or not rate.security_type:
            result[key] = rate

    return result


def parse_da1_rates_all(xml_path: str) -> list[Da1Rate]:
    """Stream-parse ALL <da1Rate> elements from Kursliste XML.

    Used by admin CLI to extract all rates for bundled JSON.

    Returns:
        List of all Da1Rate entries (including non-OTHER types).
    """
    result: list[Da1Rate] = []

    context = etree.iterparse(xml_path, events=("end",), tag=[_DA1_TAG])

    for _, elem in context:
        rate = _parse_da1_rate_element(elem)
        result.append(rate)
        elem.clear()

    return result


_DATA_DIR = Path(__file__).parent / "data"


def _get_user_data_dir() -> Path | None:
    """Return user data dir from paths module, or None if unavailable."""
    try:
        from src.ui.paths import get_data_dir
        return Path(get_data_dir())
    except Exception:
        return None


def _find_data_file(filename: str) -> tuple[Path | None, str]:
    """Find a data file, checking user data dir first, then bundled.

    Returns (path, source) where source is "user", "bundled", or "missing".
    """
    # Check user user directory first
    user_dir = _get_user_data_dir()
    if user_dir is not None:
        user_path = user_dir / filename
        if user_path.exists():
            return user_path, "user"

    # Fall back to bundled data
    bundled_path = _DATA_DIR / filename
    if bundled_path.exists():
        return bundled_path, "bundled"

    return None, "missing"


def check_kursliste_availability(year: int) -> dict:
    """Check whether kursliste data is available for the given year.

    Returns dict with keys:
        available: bool
        source: "user" | "bundled" | "missing"
    """
    _, source = _find_data_file(f"kursliste_{year}.json")
    return {"available": source != "missing", "source": source}


def load_bundled_kursliste(year: int) -> dict[str, KurslisteEntry] | None:
    """Load a pre-extracted bundled Kursliste JSON for a given year.

    Checks user user data dir first, then falls back to
    bundled ``data/kursliste_{year}.json``.  Returns a dict mapping
    ISIN to ``KurslisteEntry``, or ``None`` if the file does not exist.
    """
    path, source = _find_data_file(f"kursliste_{year}.json")
    if path is None:
        return None

    logger.info("Loading kursliste for %d from %s (%s)", year, path.name, source)
    with path.open() as f:
        raw = json.load(f)

    result: dict[str, KurslisteEntry] = {}
    for isin, data in raw.items():
        year_end_price = None
        if data.get("price") is not None:
            year_end_price = Decimal(str(data["price"]))
        tax_value_chf = None
        if data.get("tax_value_chf") is not None:
            tax_value_chf = Decimal(str(data["tax_value_chf"]))
        result[isin] = KurslisteEntry(
            valor_number=data.get("valor", ""),
            isin=isin,
            security_group=data.get("security_group", ""),
            security_type=data.get("security_type", ""),
            currency=data.get("currency", ""),
            country=data.get("country", ""),
            year_end_price=year_end_price,
            quotation_type=data.get("quotation_type", "PIECE"),
            tax_value_chf=tax_value_chf,
        )
    return result


def load_bundled_da1_rates(year: int) -> dict[tuple[str, str], Da1Rate] | None:
    """Load pre-extracted bundled DA-1 rates JSON for a given year.

    Checks user user data dir first, then falls back to
    bundled ``data/da1_rates_{year}.json``.  Returns a dict mapping
    ``(country, securityGroup)`` to ``Da1Rate``, or ``None`` if the file
    does not exist.
    """
    path, source = _find_data_file(f"da1_rates_{year}.json")
    if path is None:
        return None

    logger.info("Loading da1 rates for %d from %s (%s)", year, path.name, source)
    with path.open() as f:
        raw = json.load(f)

    result: dict[tuple[str, str], Da1Rate] = {}
    for key_str, data in raw.items():
        # Key format: "US:SHARE"
        parts = key_str.split(":", 1)
        if len(parts) != 2:
            continue
        country, security_group = parts
        result[(country, security_group)] = Da1Rate(
            country=country,
            security_group=security_group,
            security_type=data.get("security_type", ""),
            da1_rate_type=data.get("da1_rate_type", "OTHER"),
            withholding_rate=_decimal_or_none(data.get("withholding_rate")),
            release=_decimal_or_none(data.get("release")),
            non_recoverable=_decimal_or_none(data.get("non_recoverable")),
            non_recoverable_max=_decimal_or_none(data.get("non_recoverable_max")),
        )
    return result
