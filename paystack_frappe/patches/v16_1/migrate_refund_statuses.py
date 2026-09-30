"""15.x Refund Log "Completed" (reversal booked) becomes "Processed"; the reversal stays linked."""

import frappe

REFUND_LOG = "Paystack Refund Log"


def execute() -> None:
    frappe.db.sql(f"update `tab{REFUND_LOG}` set status = 'Processed' where status = 'Completed'")
    frappe.db.commit()
