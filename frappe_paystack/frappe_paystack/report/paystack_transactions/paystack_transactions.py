# Copyright (c) 2025, Anthony Emmanuel and contributors
# For license information, please see license.txt

"""Transactions as Paystack reports them for one account (live API query)."""

from typing import Optional

import frappe
from frappe import _

from frappe_paystack.core import money
from frappe_paystack.core.client import client_for
from frappe_paystack.core.constants import GATEWAY_SETTING, PAYMENT_LOG
from frappe_paystack.core.permissions import can_move_money

GATEWAY_DOCTYPE = GATEWAY_SETTING


def execute(filters: Optional[dict] = None) -> tuple:
    filters = filters or {}
    return get_columns(), get_data(filters)


def get_columns() -> list:
    return [
        {"label": _("Transaction ID"), "fieldname": "id", "fieldtype": "Data", "width": 160},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
        {"label": _("Reference"), "fieldname": "reference", "fieldtype": "Data", "width": 180},
        {"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency", "options": "currency", "width": 120},
        {"label": _("Currency"), "fieldname": "currency", "fieldtype": "Data", "width": 80},
        {"label": _("Email"), "fieldname": "email", "fieldtype": "Data", "width": 200},
        {"label": _("Reference DocType"), "fieldname": "reference_doctype", "fieldtype": "Data", "width": 160},
        {"label": _("Reference Name"), "fieldname": "reference_docname", "fieldtype": "Dynamic Link",
         "options": "reference_doctype", "width": 180},
        {"label": _("Payment"), "fieldname": "payment_session", "fieldtype": "Link", "options": PAYMENT_LOG, "width": 140},
        {"label": _("Channel"), "fieldname": "channel", "fieldtype": "Data", "width": 100},
        {"label": _("Paid At"), "fieldname": "paid_at", "fieldtype": "Datetime", "width": 170},
        {"label": _("Created At"), "fieldname": "created_at", "fieldtype": "Datetime", "width": 170},
        {"label": _("Gateway Response"), "fieldname": "gateway_response", "fieldtype": "Data", "width": 200},
        {"label": _("Domain"), "fieldname": "domain", "fieldtype": "Data", "width": 80},
    ]


def get_data(filters: dict) -> list:
    if not can_move_money() and not frappe.has_permission(PAYMENT_LOG, "read"):
        frappe.throw(_("You are not permitted to view Paystack transactions."), frappe.PermissionError)
    gateway = filters.get("gateway")
    if not gateway or not frappe.db.exists(GATEWAY_SETTING, gateway):
        frappe.throw(_("Select a Paystack account."))

    params = {}
    for key, param in (("per_page", "perPage"), ("page", "page"), ("customer", "customer"), ("status", "status"),
                       ("from_date", "from"), ("to_date", "to"), ("amount", "amount")):
        if filters.get(key):
            params[param] = filters[key]

    rows = client_for(frappe.get_doc(GATEWAY_SETTING, gateway)).request("GET", "/transaction", params=params) or []
    data = []
    for tx in rows:
        metadata = tx.get("metadata") if isinstance(tx.get("metadata"), dict) else {}
        currency = money.clean_currency(tx.get("currency"))
        data.append(
            {
                "id": str(tx.get("id") or ""),
                "status": tx.get("status"),
                "reference": tx.get("reference"),
                "amount": money.from_minor(tx.get("amount") or 0, currency),
                "currency": currency,
                "email": (tx.get("customer") or {}).get("email"),
                "reference_doctype": metadata.get("reference_doctype"),
                "reference_docname": metadata.get("reference_docname"),
                "payment_session": metadata.get("session") or metadata.get("reference"),
                "channel": tx.get("channel"),
                "paid_at": (tx.get("paid_at") or tx.get("paidAt") or "").replace("T", " ").replace("Z", "")[:19] or None,
                "created_at": (tx.get("created_at") or tx.get("createdAt") or "").replace("T", " ").replace("Z", "")[:19] or None,
                "gateway_response": tx.get("gateway_response"),
                "domain": tx.get("domain"),
            }
        )
    return data
