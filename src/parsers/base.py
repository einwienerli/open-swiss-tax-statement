from __future__ import annotations

from abc import ABC, abstractmethod

from src.models.institution import Institution
from src.models.tax_statement import TaxStatement
from src.models.taxpayer import TaxPayer


class BrokerParser(ABC):
    """Base class for broker-specific CSV/data parsers.

    Each broker parser knows how to transform a broker-specific export file
    into the common ``TaxStatement`` domain model.  The caller supplies the
    ``TaxPayer`` (personal data) and the ``tax_year``; the parser fills in
    everything else -- holdings, payments, cash balances, expenses, etc.
    """

    @abstractmethod
    def parse(
        self,
        file_content: str,
        tax_year: int,
        taxpayer: TaxPayer,
        portfolio_content: str | None = None,
        account_number: str = "",
    ) -> TaxStatement:
        """Parse a broker export into the domain model.

        Args:
            file_content: Raw file content (e.g. CSV text).
            tax_year: The fiscal year for which the statement is produced.
            taxpayer: Personal details of the taxpayer.
            portfolio_content: Optional portfolio PDF text (from ``pdftotext``)
                for year-end holdings and cash balances.
            account_number: Broker account number for statement ID.

        Returns:
            A fully populated ``TaxStatement``.
        """
        ...

    @property
    @abstractmethod
    def institution(self) -> Institution:
        """Return the institution details for this broker."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable broker name."""
        ...
