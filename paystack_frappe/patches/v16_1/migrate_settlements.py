"""15.x Settlement.status described ERPNext booking; 16.1 keeps it as booking_status (ERPNext only)."""

import frappe

SETTLEMENT = "Paystack Settlement"
MAPPING = {"Pending": "Pending", "Processed": "Booked", "Failed": "Failed"}


def execute() -> None:
    if not frappe.db.has_column(SETTLEMENT, "status") or not frappe.db.has_column(SETTLEMENT, "booking_status"):
        return
    for legacy, booking in MAPPING.items():
        frappe.db.sql(
            f"""update `tab{SETTLEMENT}` set booking_status = %s
            where status = %s and coalesce(booking_status, '') = ''""",
            (booking, legacy),
        )
    frappe.db.commit()
