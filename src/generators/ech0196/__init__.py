"""eCH-0196 XML generation and validation."""

from src.generators.ech0196.validator import validate_xml
from src.generators.ech0196.xml_builder import build_statement_id, build_xml, serialize

__all__ = ["build_statement_id", "build_xml", "serialize", "validate_xml"]
