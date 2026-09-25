"""
Create a Paystack account and a real payment on a freshly installed site, the
way a consuming app would (through Frappe Payments). Prints JSON with the
payment (session) name and the account's secret for the webhook check.

Usage (from sites/): ../env/bin/python smoke_site.py <site>
"""

import json
import sys

import frappe

site = sys.argv[1]
frappe.init(site=site, sites_path=".")
frappe.connect()
frappe.set_user("Administrator")

SECRET = "sk_test_smoke_secret"

if not frappe.db.exists("Paystack Gateway Setting", "Smoke Paystack"):
    frappe.get_doc(
        {
            "doctype": "Paystack Gateway Setting",
            "gateway": "Smoke Paystack",
            "enabled": 1,
            "test_mode": 1,
            "public_key": "pk_test_smoke_public",
            "secret_key": SECRET,
            "currency": "NGN",
        }
    ).insert()

todo = frappe.get_doc({"doctype": "ToDo", "description": "Smoke test order"}).insert()

from payments.utils import get_payment_gateway_controller  # noqa: E402

url = get_payment_gateway_controller("Paystack").get_payment_url(
    amount=5000,
    currency="NGN",
    title="Smoke test order",
    description="Paying for a smoke test order",
    reference_doctype="ToDo",
    reference_docname=todo.name,
    payer_email="payer@example.com",
    payer_name="Smoke Payer",
    redirect_to="/",
)
frappe.db.commit()
print(json.dumps({"session": url.rstrip("/").rsplit("/", 1)[-1], "secret": SECRET}))
