"""
Assert whether the ERPNext adapter is active on a site.
Usage (from sites/): ../env/bin/python check_adapter.py <site> active|inactive
"""

import sys

import frappe

site, state = sys.argv[1], sys.argv[2]
frappe.init(site=site, sites_path=".")
frappe.connect()

FIELDS = (
    ("Paystack Gateway Setting", "company"),
    ("Paystack Gateway Setting", "suspense_account"),
    ("Paystack Payment Log", "payment_entry"),
    ("Paystack Payment Log", "booking_status"),
    ("Paystack Refund Log", "reversal_payment_entry"),
    ("Paystack Settlement", "journal_entry"),
)
present = {f"{doctype}.{field}": frappe.get_meta(doctype).has_field(field) for doctype, field in FIELDS}
failures = []
if state == "active":
    failures += [f"{name} missing" for name, ok in present.items() if not ok]
    if not frappe.db.exists("Mode of Payment", "Paystack"):
        failures.append("Mode of Payment 'Paystack' missing")
    if not frappe.db.exists("Custom DocPerm", {"parent": "Paystack Payment Log", "role": "Accounts Manager"}):
        failures.append("Accounts Manager permission missing")
else:
    failures += [f"{name} still present" for name, ok in present.items() if ok]
    leftovers = frappe.get_all("Custom Field", filters={"dt": ["like", "Paystack%"]}, pluck="name")
    if leftovers:
        failures.append(f"custom fields left behind: {leftovers}")

if failures:
    sys.exit("ERPNext adapter is not " + state + ": " + "; ".join(failures))
print(f"ERPNext adapter is {state}")
