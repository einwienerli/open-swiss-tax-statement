from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Institution:
    """Financial institution (bank/broker) that issued the statement."""

    name: str
    uid: str  # Swiss UID, e.g. "CHE-106.787.734"
    clearing_number: str  # SIC/euroSIC clearing number, e.g. "08750"
