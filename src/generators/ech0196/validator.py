"""XSD validation for eCH-0196 XML output."""

from __future__ import annotations

from dataclasses import dataclass, field

from lxml import etree

from src.generators.ech0196.constants import MAIN_XSD

_cached_schema: etree.XMLSchema | None = None


@dataclass
class ValidationResult:
    """Result of XSD validation."""

    is_valid: bool
    errors: list[str] = field(default_factory=list)


def _get_schema() -> etree.XMLSchema:
    """Load and cache the eCH-0196 XSD schema."""
    global _cached_schema
    if _cached_schema is None:
        schema_doc = etree.parse(str(MAIN_XSD))
        _cached_schema = etree.XMLSchema(schema_doc)
    return _cached_schema


def validate_xml(xml_input: str | bytes | etree._Element) -> ValidationResult:
    """Validate XML against the eCH-0196 XSD schema.

    Args:
        xml_input: XML as string, bytes, or lxml Element tree.

    Returns:
        ValidationResult with is_valid flag and list of error messages.
    """
    schema = _get_schema()

    if isinstance(xml_input, (str, bytes)):
        if isinstance(xml_input, str):
            xml_input = xml_input.encode("utf-8")
        doc = etree.fromstring(xml_input)
    else:
        doc = xml_input

    is_valid = schema.validate(doc)
    errors = [str(e) for e in schema.error_log] if not is_valid else []
    return ValidationResult(is_valid=is_valid, errors=errors)
