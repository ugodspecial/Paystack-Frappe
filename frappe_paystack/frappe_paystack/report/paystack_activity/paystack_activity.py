"""
One chronological feed of everything Paystack has been doing.

Merges the captures, the refunds, the payouts and the API calls into one
stream, newest first, and grades each row by severity.
"""

from typing import Any, Optional

import frappe
from frappe import _
from frappe.utils import flt

from frappe_paystack.core.constants import CAPTURED_STATUSES, PAYSTACK_SERVICE

PAYMENT_LOG = "Paystack Payment Log"
REFUND_LOG = "Paystack Refund Log"
SETTLEMENT = "Paystack Settlement"
INTEGRATION_REQUEST = "Integration Request"

# Doctypes an Integration Request can name that carry an account of their own.
ACCOUNT_SOURCES = (PAYMENT_LOG, SETTLEMENT)

# Maximum rows the feed returns.
ACTIVITY_LIMIT = 500

ERROR = "Error"
WARNING = "Warning"
INFO = "Info"

# An API call with no error carries the literal string null.
NO_ERROR = "null"


def get_columns() -> list:
    return [
        {
            "label": _("Time"),
            "fieldname": "timestamp",
            "fieldtype": "Datetime",
            "width": 165,
        },
        {
            "label": _("Severity"),
            "fieldname": "severity",
            "fieldtype": "Data",
            "width": 90,
        },
        {"label": _("Source"), "fieldname": "source", "fieldtype": "Data", "width": 110},
        {
            "label": _("Record"),
            "fieldname": "record",
            "fieldtype": "Dynamic Link",
            "options": "source_doctype",
            "width": 210,
        },
        {
            "label": _("Source Doctype"),
            "fieldname": "source_doctype",
            "fieldtype": "Data",
            "width": 175,
        },
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
        {
            "label": _("Paystack Account"),
            "fieldname": "account",
            "fieldtype": "Link",
            "options": "Paystack Gateway Setting",
            "width": 150,
        },
        {
            "label": _("Amount"),
            "fieldname": "amount",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 130,
        },
        {
            "label": _("Currency"),
            "fieldname": "currency",
            "fieldtype": "Data",
            "width": 80,
        },
        {"label": _("Detail"), "fieldname": "detail", "fieldtype": "Data", "width": 420},
    ]


def one_line(text: Optional[str]) -> str:
    """Collapse a Small Text into something a grid cell can show."""
    return " ".join((text or "").split())


def creation_condition(filters: dict) -> Optional[list]:
    """
    Return the creation window the date range asks for, if any.

    to_date is closed at 23:59:59 to cover the whole day it names.
    """
    from_date = filters.get("from_date")
    to_date = filters.get("to_date")
    end = f"{to_date} 23:59:59" if to_date else None

    if from_date and end:
        return ["between", [from_date, end]]
    if from_date:
        return [">=", from_date]
    if end:
        return ["<=", end]
    return None


def base_filters(filters: dict, account: bool = True) -> dict:
    """Return the window (and account) conditions every source shares."""
    conditions = {}

    creation = creation_condition(filters)
    if creation:
        conditions["creation"] = creation

    if account and filters.get("gateway_setting"):
        conditions["gateway_setting"] = filters["gateway_setting"]
    return conditions


def optional_fields(doctype: str, *fields: str) -> list:
    """Adapter-owned fields (e.g. ERPNext booking) that exist on this site."""
    meta = frappe.get_meta(doctype)
    return [field for field in fields if meta.has_field(field)]


def payment_severity(log: Any) -> str:
    """
    Grade a payment.

    Failed and Needs Attention are errors. A capture whose application was not
    told (or, with ERPNext, not booked) warns.
    """
    if log.status in ("Failed", "Needs Attention"):
        return ERROR
    if log.status in CAPTURED_STATUSES and (
        log.notification_status in ("Failed", "Needs Attention") or log.get("booking_status") in ("Pending", "Needs Attention")
    ):
        return WARNING
    return INFO


def payment_rows(filters: dict) -> list:
    """Return the captures in range."""
    rows = []
    for log in frappe.get_all(
        PAYMENT_LOG,
        filters=base_filters(filters),
        fields=[
            "name",
            "creation",
            "gateway_setting",
            "status",
            "amount",
            "currency",
            "amount_paid",
            "currency_paid",
            "notification_status",
            "notification_error",
            "linked_doctype",
            "linked_docname",
            "errors",
        ]
        + optional_fields(PAYMENT_LOG, "booking_status"),
    ):
        rows.append(
            {
                "timestamp": log.creation,
                "severity": payment_severity(log),
                "source": _("Payment"),
                "source_doctype": PAYMENT_LOG,
                "record": log.name,
                "status": log.status,
                "account": log.gateway_setting,
                "amount": flt(log.amount_paid or log.amount),
                "currency": log.currency_paid or log.currency,
                "detail": one_line(log.errors)
                or one_line(log.notification_error)
                or f"{log.linked_doctype or ''} {log.linked_docname or ''}".strip(),
            }
        )
    return rows


def refund_severity(log: Any) -> str:
    """Grade a refund; Pending/Processing means the payer is still waiting on Paystack."""
    if log.status == "Failed":
        return ERROR
    if log.status in ("Pending", "Processing"):
        return WARNING
    return INFO


def refund_rows(filters: dict) -> list:
    """Return the refunds in range."""
    rows = []
    for log in frappe.get_all(
        REFUND_LOG,
        filters=base_filters(filters, account=False),
        fields=[
            "name",
            "creation",
            "status",
            "refund_amount",
            "currency",
            "payment_log",
            "refund_reason",
            "errors",
        ],
    ):
        rows.append(
            {
                "timestamp": log.creation,
                "severity": refund_severity(log),
                "source": _("Refund"),
                "source_doctype": REFUND_LOG,
                "record": log.name,
                "status": log.status,
                "account": None,
                "amount": flt(log.refund_amount),
                "currency": log.currency,
                "detail": one_line(log.errors) or one_line(log.refund_reason) or log.payment_log,
            }
        )
    return rows


def settlement_severity(payout: Any) -> str:
    """
    Grade a payout.

    An errored payout is an error; with ERPNext, one not booked yet warns.
    """
    if payout.errors or payout.get("booking_status") == "Failed":
        return ERROR
    if payout.get("booking_status") == "Pending":
        return WARNING
    return INFO


def settlement_rows(filters: dict) -> list:
    """Return the payouts in range."""
    rows = []
    for payout in frappe.get_all(
        SETTLEMENT,
        filters=base_filters(filters),
        fields=["name", "creation", "gateway_setting", "paystack_status", "net_amount", "currency", "errors"]
        + optional_fields(SETTLEMENT, "journal_entry", "booking_status"),
    ):
        rows.append(
            {
                "timestamp": payout.creation,
                "severity": settlement_severity(payout),
                "source": _("Payout"),
                "source_doctype": SETTLEMENT,
                "record": payout.name,
                "status": payout.get("booking_status") or payout.paystack_status,
                "account": payout.gateway_setting,
                "amount": flt(payout.net_amount),
                "currency": payout.currency,
                "detail": one_line(payout.errors) or payout.get("journal_entry") or "",
            }
        )
    return rows


def request_accounts(requests: list) -> dict:
    """Return the Paystack account behind each referenced record, keyed by (doctype, name)."""
    accounts = {}
    for doctype in ACCOUNT_SOURCES:
        names = {r.reference_docname for r in requests if r.reference_doctype == doctype and r.reference_docname}
        if names:
            for row in frappe.get_all(doctype, filters={"name": ["in", list(names)]}, fields=["name", "gateway_setting"]):
                accounts[(doctype, row.name)] = row.gateway_setting
    return accounts


def request_error(request: Any) -> str:
    """Return what an API call recorded going wrong, if anything did."""
    error = one_line(request.error)
    return "" if error == NO_ERROR else error


def request_rows(filters: dict) -> list:
    """
    Return the Paystack API calls in range, including unattributable ones.

    A call with no resolvable account is kept under any account filter.
    """
    conditions = base_filters(filters, account=False)
    conditions["integration_request_service"] = PAYSTACK_SERVICE

    requests = frappe.get_all(
        INTEGRATION_REQUEST,
        filters=conditions,
        fields=[
            "name",
            "creation",
            "status",
            "url",
            "error",
            "reference_doctype",
            "reference_docname",
        ],
    )

    accounts = request_accounts(requests)
    account = filters.get("gateway_setting")

    rows = []
    for request in requests:
        owner = accounts.get((request.reference_doctype, request.reference_docname))
        if account and owner and owner != account:
            continue

        rows.append(
            {
                "timestamp": request.creation,
                "severity": ERROR if request.status == "Failed" else INFO,
                "source": _("API Call"),
                "source_doctype": INTEGRATION_REQUEST,
                "record": request.name,
                "status": request.status,
                "account": owner,
                "amount": None,
                "currency": None,
                "detail": request_error(request) or request.url,
            }
        )

    return rows


SOURCES = {
    "Payments": payment_rows,
    "Refunds": refund_rows,
    "Payouts": settlement_rows,
    "API Calls": request_rows,
}


def get_data(filters: dict) -> list:
    """Merge the sources the filters ask for into one stream, newest first."""
    source = filters.get("source")
    severity = filters.get("severity")

    rows = []
    for label, fetch in SOURCES.items():
        if source and source != label:
            continue
        rows.extend(fetch(filters))

    if severity:
        rows = [row for row in rows if row["severity"] == severity]

    rows.sort(key=lambda row: row["timestamp"], reverse=True)
    return rows[:ACTIVITY_LIMIT]


def execute(filters: Optional[dict] = None) -> tuple:
    filters = filters or {}
    if not frappe.has_permission(PAYMENT_LOG, "read"):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    return get_columns(), get_data(filters)
