"""
15.x: move captures the retry window closed on into Needs Attention.

Kept for sites upgrading from before this patch; the 16.1 status migration
then maps them to Paid with booking status Needs Attention.
"""

import frappe
from frappe.utils import add_days, nowdate

PAYMENT_LOG = "Paystack Payment Log"


def execute() -> None:
    if not all(frappe.db.has_column(PAYMENT_LOG, c) for c in ("payment_entry", "linked_doctype")):
        return
    frappe.db.sql(
        """update `tabPaystack Payment Log` set status = 'Needs Attention'
        where status = 'Processed' and coalesce(payment_entry, '') = '' and docstatus < 2
        and linked_doctype != 'POS Invoice' and creation <= %s""",
        (add_days(nowdate(), -7),),
    )
