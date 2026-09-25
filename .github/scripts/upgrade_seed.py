"""
Seed rows shaped like frappe_paystack 15.5.0 data, on a site running upstream 15.5.0.

Rows are written with SQL so no ERPNext masters are needed: the point is to
prove that the 16.1 migration keeps every value and maps every status.
Usage (from sites/): ../env/bin/python upgrade_seed.py <site>
"""

import sys

import frappe
from frappe.utils import now_datetime
from frappe.utils.password import set_encrypted_password

site = sys.argv[1]
frappe.init(site=site, sites_path=".")
frappe.connect()
now = now_datetime()


def insert(doctype, values):
    values = {"creation": now, "modified": now, "owner": "Administrator", "modified_by": "Administrator", "docstatus": 0, **values}
    columns = ", ".join(f"`{column}`" for column in values)
    placeholders = ", ".join(["%s"] * len(values))
    frappe.db.sql(f"insert into `tab{doctype}` ({columns}) values ({placeholders})", tuple(values.values()))


insert("Paystack Gateway Setting", {"name": "Legacy Paystack", "gateway": "Legacy Paystack", "enabled": 0, "test_mode": 1,
                                    "public_key": "pk_test_legacy", "company": "_Legacy Co", "currency": "NGN",
                                    "suspense_account": "Paystack Suspense - LC", "mode_of_payment": "Paystack",
                                    "settlement_bank_account": "Bank - LC", "paystack_fee_account": "Fees - LC",
                                    "checkout_mode": "Inline", "auto_refund_on_credit_note": 1})
set_encrypted_password("Paystack Gateway Setting", "Legacy Paystack", "sk_test_legacy_secret", "secret_key")

for name, status, payment_entry in (
    ("legacy-processed", "Processed", None),
    ("legacy-completed", "Completed", "ACC-PAY-0001"),
    ("legacy-attention", "Needs Attention", None),
    ("legacy-refunded", "Partially Refunded", "ACC-PAY-0002"),
    ("legacy-pending", "Pending", None),
    ("legacy-failed", "Failed", None),
):
    captured = status not in ("Pending", "Failed")
    insert("Paystack Payment Log", {
        "name": name, "company": "_Legacy Co", "linked_doctype": "Sales Invoice", "linked_docname": f"SINV-{name}",
        "amount": 1000, "currency": "NGN", "status": status, "amount_paid": 1000 if captured else 0,
        "currency_paid": "NGN" if captured else None, "payment_reference": name,
        "transaction_id": f"tx-{name}" if captured else None, "payment_entry": payment_entry,
        "total_refunded": 250 if status == "Partially Refunded" else 0, "retry_count": 3 if status == "Processed" else 0,
    })

insert("Paystack Refund Log", {"name": "legacy-refund", "payment_log": "legacy-refunded", "company": "_Legacy Co",
                               "refund_amount": 250, "currency": "NGN", "status": "Completed",
                               "reversal_payment_entry": "ACC-PAY-0003", "transaction_id": "tx-legacy-refunded"})
insert("Paystack Customer Authorization", {"name": "legacy-card", "customer": "_Legacy Customer", "company": "_Legacy Co",
                                           "card_label": "Visa •••• 4081", "active": 1, "reusable": 1, "signature": "SIG_1",
                                           "email": "legacy@example.com"})
insert("Paystack Settlement", {"name": "9001", "settlement_id": "9001", "company": "_Legacy Co", "status": "Processed",
                               "currency": "NGN", "gross_amount": 1000, "total_fees": 15, "net_amount": 985,
                               "journal_entry": "ACC-JV-0001"})
frappe.db.commit()
print("seeded 15.5.0 rows")
