"""eCH-0196 XML builder: transforms a TaxStatement into an lxml Element tree.

Public API:
    build_xml(statement) -> etree._Element
    serialize(root, pretty_print=True) -> str
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from lxml import etree

from src.generators.ech0196.constants import (
    CATEGORY_FUND,
    CATEGORY_SHARE,
    EXPENSE_TYPE_CUSTODY_FEE,
    FUND_KEYWORDS,
    MINOR_VERSION,
    NS_ECH0097,
    NS_ECH0196,
    NS_XSI,
    NSMAP,
    QUOTATION_PIECE,
    SCHEMA_LOCATION,
)
from src.models.enums import IncomeCategory, TransactionType

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.models.security import Holding, Security, SecurityPayment
    from src.models.tax_statement import TaxStatement
    from src.models.transaction import Transaction


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_xml(statement: TaxStatement) -> etree._Element:
    """Build eCH-0196 XML Element tree from a TaxStatement."""
    totals = _compute_totals(statement)
    root = _build_root(statement, totals)
    _build_institution(root, statement)
    _build_client(root, statement)
    # Optional sections — only emit if data exists
    if statement.cash_balances:
        _build_bank_accounts(root, statement, totals)
    if statement.expenses:
        _build_expenses(root, statement, totals)
    has_bought = any(
        t.security and t.type == TransactionType.BUY
        for t in statement.transactions
    )
    if statement.holdings or statement.security_payments or has_bought:
        _build_securities(root, statement, totals)
    rubrik_a = sum(1 for p in statement.security_payments
                   if _determine_income_category(p) == IncomeCategory.RUBRIK_A)
    rubrik_b = len(statement.security_payments) - rubrik_a
    logger.info("XML built: %d holdings, %d payments (A:%d B:%d), %d expenses, %d cash",
                len(statement.holdings), len(statement.security_payments),
                rubrik_a, rubrik_b, len(statement.expenses), len(statement.cash_balances))
    return root


def serialize(root: etree._Element, pretty_print: bool = True) -> str:
    """Serialize Element tree to XML string with declaration."""
    return etree.tostring(
        root,
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=pretty_print,
    ).decode("utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_uid(uid_string: str) -> tuple[str, int]:
    """Parse 'CHE-106.787.734' into ('CHE', 106787734)."""
    parts = uid_string.split("-", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid UID format: {uid_string}")
    category = parts[0]
    number = int(parts[1].replace(".", ""))
    return category, number


def build_statement_id(statement: TaxStatement) -> str:
    """Build statement ID matching the format used by working E-Steuerauszug.

    Format: CH + clearing(5) + account(14,zero-padded) + stichtag(8,YYYYMMDD) + sequence(2)
    Total: 31 chars.
    """
    clearing = statement.institution.clearing_number.zfill(5)
    account = statement.account_number.zfill(14)
    stichtag = statement.period_to.strftime("%Y%m%d")
    return f"CH{clearing}{account}{stichtag}01"


def _format_decimal(value: Decimal | None, default: str = "0") -> str:
    """Format a monetary Decimal for XML output, rounded to 2dp. None → default."""
    if value is None:
        return default
    return str(value.quantize(Decimal("0.01")))


def _format_rate(value: Decimal | None) -> str | None:
    """Format an exchange rate at full precision (trailing zeros stripped). None → None (omit attribute)."""
    if value is None:
        return None
    return str(value.normalize())


def _format_date(d: date) -> str:
    """Format a date as YYYY-MM-DD."""
    return d.isoformat()


def _determine_security_category(security: Security) -> str:
    """Heuristic: determine security category from name."""
    name_upper = security.name.upper()
    for keyword in FUND_KEYWORDS:
        if keyword in name_upper:
            return CATEGORY_FUND
    return CATEGORY_SHARE


def _determine_country(isin: str) -> str:
    """Derive 2-letter country code from ISIN prefix."""
    if len(isin) >= 2:
        return isin[:2].upper()
    return "CH"


def _determine_income_category(
    payment: SecurityPayment,
) -> IncomeCategory:
    """Determine income category for a payment.

    If explicitly set, use it. Otherwise apply heuristic:
    - Swiss ISIN (CH) with withholding tax > 0 → RUBRIK_A
    - Everything else → RUBRIK_B
    """
    if payment.income_category is not None:
        return payment.income_category
    if payment.security.income_category is not None:
        return payment.security.income_category
    country = _determine_country(payment.security.isin)
    if country == "CH" and payment.withholding_tax > 0:
        return IncomeCategory.RUBRIK_A
    return IncomeCategory.RUBRIK_B


def _truncate(s: str, max_len: int) -> str:
    """Truncate string to max_len characters."""
    return s[:max_len] if len(s) > max_len else s


def _chf_or_original(chf_val: Decimal | None, original: Decimal) -> Decimal:
    """Return CHF-converted value if available, otherwise the original."""
    return chf_val if chf_val is not None else original


# ---------------------------------------------------------------------------
# Totals computation
# ---------------------------------------------------------------------------


def _compute_totals(statement: TaxStatement) -> dict[str, Decimal]:
    """Compute all required totals from statement data.

    Uses pre-computed values from statement if available, otherwise
    derives from holdings/payments/cash_balances.
    """
    zero = Decimal("0")

    # Tax values
    sec_tax_value = sum(
        (h.tax_value_chf or zero for h in statement.holdings), zero
    )
    cash_tax_value = sum(
        (cb.tax_value_chf or zero for cb in statement.cash_balances), zero
    )
    total_tax_value = (
        statement.total_tax_value
        if statement.total_tax_value is not None
        else sec_tax_value + cash_tax_value
    )

    # Revenue A (with withholding tax claim)
    revenue_a = zero
    wht_claim = zero
    for p in statement.security_payments:
        cat = _determine_income_category(p)
        if cat == IncomeCategory.RUBRIK_A:
            revenue_a += _chf_or_original(p.gross_amount_chf, p.gross_amount)
            wht_claim += _chf_or_original(p.withholding_tax_chf, p.withholding_tax)

    total_revenue_a = (
        statement.total_gross_revenue_a
        if statement.total_gross_revenue_a is not None
        else revenue_a
    )

    # Revenue B (without withholding tax claim) — includes interest
    revenue_b = zero
    for p in statement.security_payments:
        cat = _determine_income_category(p)
        if cat == IncomeCategory.RUBRIK_B:
            revenue_b += _chf_or_original(p.gross_amount_chf, p.gross_amount)
    # Add interest from cash balances
    for cb in statement.cash_balances:
        revenue_b += _chf_or_original(cb.interest_earned_chf, cb.interest_earned)

    total_revenue_b = (
        statement.total_gross_revenue_b
        if statement.total_gross_revenue_b is not None
        else revenue_b
    )

    total_wht = (
        statement.total_withholding_tax_claim
        if statement.total_withholding_tax_claim is not None
        else wht_claim
    )

    # Expenses
    total_expenses = sum(
        (abs(e.net_amount) for e in statement.expenses), zero
    )

    # Securities sub-totals
    sec_revenue_a = zero
    sec_revenue_b = zero
    sec_wht = zero
    for p in statement.security_payments:
        cat = _determine_income_category(p)
        if cat == IncomeCategory.RUBRIK_A:
            sec_revenue_a += _chf_or_original(p.gross_amount_chf, p.gross_amount)
            sec_wht += _chf_or_original(p.withholding_tax_chf, p.withholding_tax)
        else:
            sec_revenue_b += _chf_or_original(p.gross_amount_chf, p.gross_amount)

    # DA-1 totals for securities
    sec_lump_sum_credit = zero
    sec_non_recoverable = zero
    sec_rubrik_b_wht = zero
    for p in statement.security_payments:
        cat = _determine_income_category(p)
        if cat == IncomeCategory.RUBRIK_B:
            if p.lump_sum_tax_credit_chf is not None:
                sec_lump_sum_credit += p.lump_sum_tax_credit_chf
            if p.non_recoverable_tax_chf is not None:
                sec_non_recoverable += p.non_recoverable_tax_chf
            sec_rubrik_b_wht += _chf_or_original(p.withholding_tax_chf, p.withholding_tax)

    # Bank account sub-totals
    bank_revenue_a = zero  # Bank interest is typically Rubrik B
    bank_revenue_b = sum(
        (_chf_or_original(cb.interest_earned_chf, cb.interest_earned)
         for cb in statement.cash_balances), zero
    )
    bank_wht = zero

    return {
        "total_tax_value": total_tax_value,
        "total_revenue_a": total_revenue_a,
        "total_revenue_b": total_revenue_b,
        "total_wht": total_wht,
        "total_expenses": total_expenses,
        "sec_tax_value": sec_tax_value,
        "sec_revenue_a": sec_revenue_a,
        "sec_revenue_b": sec_revenue_b,
        "sec_wht": sec_wht,
        "sec_lump_sum_credit": sec_lump_sum_credit,
        "sec_non_recoverable": sec_non_recoverable,
        "sec_rubrik_b_wht": sec_rubrik_b_wht,
        "cash_tax_value": cash_tax_value,
        "bank_revenue_a": bank_revenue_a,
        "bank_revenue_b": bank_revenue_b,
        "bank_wht": bank_wht,
    }


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _build_root(
    statement: TaxStatement, totals: dict[str, Decimal]
) -> etree._Element:
    """Create root <taxStatement> element with all required attributes."""
    root = etree.Element(f"{{{NS_ECH0196}}}taxStatement", nsmap=NSMAP)
    root.set(f"{{{NS_XSI}}}schemaLocation", SCHEMA_LOCATION)
    root.set("minorVersion", str(MINOR_VERSION))
    root.set("id", build_statement_id(statement))
    root.set("creationDate", datetime.now().isoformat(timespec="seconds"))
    root.set("taxPeriod", str(statement.tax_year))
    root.set("periodFrom", _format_date(statement.period_from))
    root.set("periodTo", _format_date(statement.period_to))
    root.set("canton", statement.taxpayer.canton)
    root.set("country", "CH")
    root.set("totalTaxValue", _format_decimal(totals["total_tax_value"]))
    root.set("totalGrossRevenueA", _format_decimal(totals["total_revenue_a"]))
    root.set("totalGrossRevenueB", _format_decimal(totals["total_revenue_b"]))
    root.set("totalWithHoldingTaxClaim", _format_decimal(totals["total_wht"]))
    return root


def _build_institution(parent: etree._Element, statement: TaxStatement) -> None:
    """Build <institution> element with UID child."""
    inst = etree.SubElement(parent, f"{{{NS_ECH0196}}}institution")
    inst.set("name", _truncate(statement.institution.name, 60))

    # UID element in eCH-0196 namespace, children in eCH-0097 namespace
    uid_el = etree.SubElement(inst, f"{{{NS_ECH0196}}}uid")
    category, number = _parse_uid(statement.institution.uid)
    cat_el = etree.SubElement(
        uid_el, f"{{{NS_ECH0097}}}uidOrganisationIdCategorie"
    )
    cat_el.text = category
    num_el = etree.SubElement(uid_el, f"{{{NS_ECH0097}}}uidOrganisationId")
    num_el.text = str(number)


def _build_client(parent: etree._Element, statement: TaxStatement) -> None:
    """Build <client> element."""
    client = etree.SubElement(parent, f"{{{NS_ECH0196}}}client")
    client_number = statement.account_number or statement.institution.clearing_number
    client.set("clientNumber", client_number)
    if statement.taxpayer.first_name:
        client.set("firstName", _truncate(statement.taxpayer.first_name, 30))
    if statement.taxpayer.last_name:
        client.set("lastName", _truncate(statement.taxpayer.last_name, 30))
    if statement.taxpayer.ahv_number:
        # TIN maxLength=11 in XSD; strip dots from AHV format "756.1234.5678.90"
        tin = statement.taxpayer.ahv_number.replace(".", "")
        if len(tin) <= 11:
            client.set("tin", tin)


def _build_bank_accounts(
    parent: etree._Element,
    statement: TaxStatement,
    totals: dict[str, Decimal],
) -> None:
    """Build <listOfBankAccounts> with one <bankAccount> per cash balance."""
    zero = Decimal("0")
    list_el = etree.SubElement(parent, f"{{{NS_ECH0196}}}listOfBankAccounts")
    list_el.set("totalTaxValue", _format_decimal(totals["cash_tax_value"]))
    list_el.set("totalGrossRevenueA", _format_decimal(totals["bank_revenue_a"]))
    list_el.set("totalGrossRevenueB", _format_decimal(totals["bank_revenue_b"]))
    list_el.set("totalWithHoldingTaxClaim", _format_decimal(totals["bank_wht"]))

    ref_date = _format_date(statement.period_to)

    for cb in statement.cash_balances:
        acct = etree.SubElement(list_el, f"{{{NS_ECH0196}}}bankAccount")
        acct.set("bankAccountName", f"Cash {cb.currency}")
        if cb.iban:
            acct.set("iban", cb.iban)
        acct.set("bankAccountCountry", "CH")
        acct.set("bankAccountCurrency", cb.currency)
        acct.set("totalTaxValue", _format_decimal(cb.tax_value_chf))
        interest_chf = _chf_or_original(cb.interest_earned_chf, cb.interest_earned)
        acct.set(
            "totalGrossRevenueA", "0"
        )  # Bank interest is typically Rubrik B
        acct.set("totalGrossRevenueB", _format_decimal(interest_chf))
        acct.set("totalWithHoldingTaxClaim", "0")

        # Tax value child
        tv = etree.SubElement(acct, f"{{{NS_ECH0196}}}taxValue")
        tv.set("referenceDate", ref_date)
        tv.set("balanceCurrency", cb.currency)
        tv.set("balance", _format_decimal(cb.balance))
        tv.set("value", _format_decimal(cb.tax_value_chf))
        if cb.exchange_rate is not None and cb.currency != "CHF":
            tv.set("exchangeRate", _format_rate(cb.exchange_rate))

        # Interest payment (if any)
        if cb.interest_earned and cb.interest_earned > zero:
            pmt = etree.SubElement(acct, f"{{{NS_ECH0196}}}payment")
            pmt.set("paymentDate", ref_date)
            pmt.set("amountCurrency", cb.currency)
            pmt.set("amount", _format_decimal(cb.interest_earned))
            pmt.set("grossRevenueA", "0")
            pmt.set("grossRevenueB", _format_decimal(interest_chf))
            pmt.set("withHoldingTaxClaim", "0")
            if cb.exchange_rate is not None and cb.currency != "CHF":
                pmt.set("exchangeRate", _format_rate(cb.exchange_rate))


def _build_expenses(
    parent: etree._Element,
    statement: TaxStatement,
    totals: dict[str, Decimal],
) -> None:
    """Build <listOfExpenses> from custody fee transactions."""
    list_el = etree.SubElement(parent, f"{{{NS_ECH0196}}}listOfExpenses")
    total = totals["total_expenses"]
    list_el.set("totalExpenses", _format_decimal(total))
    list_el.set("totalExpensesDeductible", _format_decimal(total))

    for exp in statement.expenses:
        expense_el = etree.SubElement(list_el, f"{{{NS_ECH0196}}}expense")
        expense_el.set("name", _truncate("Custody Fees", 200))
        expense_el.set("amountCurrency", exp.currency)
        amount = _format_decimal(abs(exp.net_amount))
        expense_el.set("expenses", amount)
        expense_el.set("expensesDeductible", amount)
        expense_el.set("expenseType", EXPENSE_TYPE_CUSTODY_FEE)
        if exp.date:
            expense_el.set("referenceDate", _format_date(exp.date))


def _build_securities(
    parent: etree._Element,
    statement: TaxStatement,
    totals: dict[str, Decimal],
) -> None:
    """Build <listOfSecurities> with depot/security/payment/stock elements."""
    list_el = etree.SubElement(parent, f"{{{NS_ECH0196}}}listOfSecurities")
    list_el.set("totalTaxValue", _format_decimal(totals["sec_tax_value"]))
    list_el.set("totalGrossRevenueA", _format_decimal(totals["sec_revenue_a"]))
    list_el.set("totalGrossRevenueB", _format_decimal(totals["sec_revenue_b"]))
    list_el.set("totalWithHoldingTaxClaim", _format_decimal(totals["sec_wht"]))
    list_el.set("totalLumpSumTaxCredit", _format_decimal(totals["sec_lump_sum_credit"]))
    list_el.set("totalNonRecoverableTax", _format_decimal(totals["sec_non_recoverable"]))
    list_el.set("totalAdditionalWithHoldingTaxUSA", "0")
    list_el.set("totalGrossRevenueIUP", "0")
    list_el.set("totalGrossRevenueConversion", "0")

    # Single depot for all securities
    depot = etree.SubElement(list_el, f"{{{NS_ECH0196}}}depot")
    depot_number = statement.account_number or statement.institution.clearing_number
    depot.set("depotNumber", depot_number)

    # Index holdings, payments, and transactions by ISIN
    holdings_by_isin: dict[str, Holding] = {}
    for h in statement.holdings:
        holdings_by_isin[h.security.isin] = h

    payments_by_isin: dict[str, list[SecurityPayment]] = defaultdict(list)
    for p in statement.security_payments:
        payments_by_isin[p.security.isin].append(p)

    txns_by_isin: dict[str, list[Transaction]] = defaultdict(list)
    for t in statement.transactions:
        if t.security and t.type in (TransactionType.BUY, TransactionType.SELL):
            txns_by_isin[t.security.isin].append(t)

    # ISINs that were actually bought (excludes rights/corporate actions that
    # only have SELL without a preceding BUY)
    bought_isins = {
        isin for isin, txns in txns_by_isin.items()
        if any(t.type == TransactionType.BUY for t in txns)
    }

    # Union of ISINs from holdings, payments, and bought-then-sold positions
    all_isins = sorted(
        set(holdings_by_isin) | set(payments_by_isin) | bought_isins
    )

    ref_date = _format_date(statement.period_to)
    period_from = _format_date(statement.period_from)

    for position_id, isin in enumerate(all_isins, start=1):
        holding = holdings_by_isin.get(isin)
        payments = payments_by_isin.get(isin, [])
        transactions = txns_by_isin.get(isin, [])
        # Resolve the Security object: prefer holding's enriched version,
        # then payment's, then transaction's
        if holding:
            security = holding.security
        elif payments:
            security = payments[0].security
        else:
            security = transactions[0].security
        _build_security_element(
            depot, position_id, security, holding, payments, transactions,
            ref_date, period_from,
        )


def _build_security_element(
    parent: etree._Element,
    position_id: int,
    security: Security,
    holding: Holding | None,
    payments: list[SecurityPayment],
    transactions: list[Transaction],
    ref_date: str,
    period_from: str,
) -> None:
    """Build a single <security> element with taxValue, payment(s), stock(s)."""
    zero = Decimal("0")

    security_el = etree.SubElement(parent, f"{{{NS_ECH0196}}}security")
    security_el.set("positionId", str(position_id))
    if security.valor_number:
        security_el.set("valorNumber", security.valor_number)
    security_el.set("isin", security.isin)
    security_el.set("country", _determine_country(security.isin))
    security_el.set("currency", security.currency)
    security_el.set("quotationType", QUOTATION_PIECE)
    security_el.set("securityCategory", _determine_security_category(security))
    security_el.set("securityName", _truncate(security.name, 60))

    # Tax value: from holding, or zero-quantity for fully sold positions
    # TrueWealth convention: always balanceCurrency=CHF, unitPrice in CHF,
    # no exchangeRate on security taxValue (only on bank accounts).
    if holding is not None:
        tv = etree.SubElement(security_el, f"{{{NS_ECH0196}}}taxValue")
        tv.set("referenceDate", ref_date)
        tv.set("quotationType", QUOTATION_PIECE)
        tv.set("quantity", _format_decimal(holding.quantity))
        tv.set("balanceCurrency", "CHF")
        if security.tax_value_per_unit_chf is not None:
            tv.set("unitPrice", _format_decimal(security.tax_value_per_unit_chf))
        if holding.tax_value_chf is not None:
            tv.set("value", _format_decimal(holding.tax_value_chf))
    elif transactions:
        # Fully sold position: emit taxValue with quantity=0 and value=0
        tv = etree.SubElement(security_el, f"{{{NS_ECH0196}}}taxValue")
        tv.set("referenceDate", ref_date)
        tv.set("quotationType", QUOTATION_PIECE)
        tv.set("quantity", "0.00")
        tv.set("balanceCurrency", "CHF")
        tv.set("value", "0.00")

    # Payments (dividends)
    for p in payments:
        pmt = etree.SubElement(security_el, f"{{{NS_ECH0196}}}payment")
        pmt.set("name", "Bardividende")
        pmt.set("paymentDate", _format_date(p.payment_date))
        if p.ex_date is not None:
            pmt.set("exDate", _format_date(p.ex_date))
        pmt.set("quotationType", QUOTATION_PIECE)
        pmt.set("quantity", _format_decimal(p.quantity))
        pmt.set("amountCurrency", p.currency)
        pmt.set("amount", _format_decimal(p.gross_amount))
        if p.amount_per_unit is not None:
            pmt.set("amountPerUnit", _format_decimal(p.amount_per_unit))

        cat = _determine_income_category(p)
        gross_chf = _chf_or_original(p.gross_amount_chf, p.gross_amount)
        wht_chf = _chf_or_original(p.withholding_tax_chf, p.withholding_tax)
        if cat == IncomeCategory.RUBRIK_A:
            pmt.set("grossRevenueA", _format_decimal(gross_chf))
            pmt.set("withHoldingTaxClaim", _format_decimal(wht_chf))
        else:
            pmt.set("grossRevenueB", _format_decimal(gross_chf))
            if wht_chf > 0:
                pmt.set("withHoldingTaxClaim", _format_decimal(wht_chf))
            # DA-1 fields (from Kursliste da1Rate enrichment)
            if p.lump_sum_tax_credit_pct is not None:
                pmt.set("lumpSumTaxCredit", "1")
                pmt.set("lumpSumTaxCreditPercent", _format_decimal(p.lump_sum_tax_credit_pct))
            if p.lump_sum_tax_credit_chf is not None:
                pmt.set("lumpSumTaxCreditAmount", _format_decimal(p.lump_sum_tax_credit_chf))
            if p.non_recoverable_tax_pct is not None:
                pmt.set("nonRecoverableTaxPercent", _format_decimal(p.non_recoverable_tax_pct))
            if p.non_recoverable_tax_chf is not None:
                pmt.set("nonRecoverableTaxAmount", _format_decimal(p.non_recoverable_tax_chf))
        if p.exchange_rate is not None and p.currency != "CHF":
            pmt.set("exchangeRate", _format_rate(p.exchange_rate))

    # Opening balance (mutation=false): always emitted for every security
    year_end_qty = holding.quantity if holding is not None else zero
    net_change = sum(
        ((t.quantity or zero) if t.type == TransactionType.BUY else -abs(t.quantity or zero))
        for t in transactions
    ) if transactions else zero
    opening_qty = year_end_qty - net_change
    ob = etree.SubElement(security_el, f"{{{NS_ECH0196}}}stock")
    ob.set("referenceDate", period_from)
    ob.set("mutation", "0")
    ob.set("quotationType", QUOTATION_PIECE)
    ob.set("quantity", _format_decimal(opening_qty))
    ob.set("balanceCurrency", security.currency)

    # Stock mutations (buy/sell transactions)
    for t in transactions:
        stock = etree.SubElement(security_el, f"{{{NS_ECH0196}}}stock")
        stock.set("referenceDate", _format_date(t.date))
        stock.set("mutation", "1")
        if t.type == TransactionType.BUY:
            stock.set("name", "Buy")
        elif t.type == TransactionType.SELL:
            stock.set("name", "Sell")
        stock.set("quotationType", QUOTATION_PIECE)
        qty = t.quantity or zero
        if t.type == TransactionType.SELL:
            qty = -abs(qty)
        stock.set("quantity", _format_decimal(qty))
        stock.set("balanceCurrency", t.currency)
        if t.unit_price is not None:
            stock.set("unitPrice", _format_decimal(t.unit_price))
