"""
Map 15.x Payment Log statuses onto the 16.1 model.

15.x conflated what Paystack did with what ERPNext booked. 16.1 keeps the
gateway status (Paid, ...) separate from ERPNext's booking_status and from the
consumer notification_status:

    Processed        -> Paid, booking Pending
    Completed        -> Paid, booking Booked
    Needs Attention  -> Paid, booking Needs Attention (15.x meant "booking abandoned")
    (Partially) Refunded -> unchanged, booking Booked

The previous value is kept in legacy_status. Captured payments are marked
Notified, so no consumer is notified twice. Idempotent.
"""

import frappe
from frappe.utils import flt

PAYMENT_LOG = "Paystack Payment Log"
MAPPING = {
    "Processed": ("Paid", "Pending"),
    "Completed": ("Paid", "Booked"),
    "Needs Attention": ("Paid", "Needs Attention"),
    "Partially Refunded": ("Partially Refunded", "Booked"),
    "Refunded": ("Refunded", "Booked"),
}


def execute() -> None:
    has_booking = frappe.db.has_column(PAYMENT_LOG, "booking_status") and frappe.get_meta(PAYMENT_LOG).has_field("booking_status")
    rows = frappe.db.sql(
        f"""select name, status from `tab{PAYMENT_LOG}`
        where coalesce(legacy_status, '') = '' and status in %(statuses)s""",
        {"statuses": tuple(MAPPING)},
        as_dict=True,
    )
    for row in rows:
        status, booking = MAPPING[row.status]
        values = {"status": status, "legacy_status": row.status, "notification_status": "Notified"}
        if has_booking:
            values["booking_status"] = booking
        frappe.db.set_value(PAYMENT_LOG, row.name, values, update_modified=False)

    # Uncaptured sessions: Pending ones may still be paid; Failed ones need no notification.
    frappe.db.sql(
        f"""update `tab{PAYMENT_LOG}` set notification_status = 'Pending'
        where status = 'Pending' and coalesce(notification_status, '') = ''"""
    )
    frappe.db.sql(
        f"""update `tab{PAYMENT_LOG}` set notification_status = 'Not Required'
        where status = 'Failed' and coalesce(notification_status, '') = ''"""
    )
    recompute_open_charges()
    frappe.db.commit()


def recompute_open_charges() -> None:
    """
    15.x stored a Pending log's amount in the document's currency and converted
    at checkout; 16.1 stores what Paystack is charged. Convert open ERPNext
    logs for documents in a foreign currency (only possible with ERPNext).
    """
    if "erpnext" not in frappe.get_installed_apps():
        return
    for row in frappe.db.sql(
        f"""select name, linked_doctype, linked_docname, amount, currency, gateway_setting from `tab{PAYMENT_LOG}`
        where status = 'Pending' and coalesce(request_data, '') = '' and coalesce(linked_docname, '') != ''""",
        as_dict=True,
    ):
        doctype, docname = row.linked_doctype, row.linked_docname
        if doctype == "Payment Request":
            doctype, docname = frappe.db.get_value("Payment Request", docname, ["reference_doctype", "reference_name"]) or (None, None)
        if not doctype or not frappe.db.exists(doctype, docname) or not frappe.get_meta(doctype).has_field("conversion_rate"):
            continue
        rate = flt(frappe.db.get_value(doctype, docname, "conversion_rate")) or 1.0
        company = frappe.db.get_value(doctype, docname, "company")
        company_currency = frappe.db.get_value("Company", company, "default_currency") if company else None
        if rate != 1.0 and company_currency and (row.currency or "").upper() != company_currency.upper():
            frappe.db.set_value(PAYMENT_LOG, row.name, {"amount": flt(flt(row.amount) * rate, 2), "currency": company_currency},
                                update_modified=False)
