"""
Verify a 15.5.0 site after the switch to the renamed app and `bench migrate`
(see upgrade_seed.py): every seeded row keeps its values, and the site itself
now runs paystack_frappe - module, patch history, scheduler rows - with no
frappe_paystack rows left behind.
Usage (from sites/): ../env/bin/python upgrade_verify.py <site>
"""

import sys

import frappe

site = sys.argv[1]
frappe.init(site=site, sites_path=".")
frappe.connect()
failures = []


def expect(label, actual, expected):
    if actual != expected:
        failures.append(f"{label}: expected {expected!r}, got {actual!r}")


# --- the rename itself: the site runs the renamed app, nothing names the old one
expect("installed apps", "paystack_frappe" in frappe.get_installed_apps(), True)
expect("former app installed", "frappe_paystack" in frappe.get_installed_apps(), False)
expect("module renamed", bool(frappe.db.exists("Module Def", "Paystack Frappe")), True)
expect("former module gone", bool(frappe.db.exists("Module Def", "Frappe Paystack")), False)
module = frappe.db.get_value("Module Def", "Paystack Frappe", ["module_name", "app_name"], as_dict=True)
expect("module_name", module.module_name, "Paystack Frappe")
expect("module app_name", module.app_name, "paystack_frappe")
for doctype in ("Paystack Payment Log", "Paystack Gateway Setting", "Paystack Settlement"):
    expect(f"{doctype} module", frappe.db.get_value("DocType", doctype, "module"), "Paystack Frappe")
expect("workspace module", frappe.db.get_value("Workspace", "Paystack Dashboard", "module"), "Paystack Frappe")
expect("report module", frappe.db.get_value("Report", "Paystack Activity", "module"), "Paystack Frappe")
expect("card module", frappe.db.get_value("Number Card", "Paystack Failed Payments", "module"), "Paystack Frappe")

expect(
    "patch logs of the former app",
    frappe.get_all("Patch Log", filters={"patch": ["like", "frappe_paystack.%"]}, pluck="patch"),
    [],
)
for patch in (
    "paystack_frappe.patches.v17_0.rename_app",
    "paystack_frappe.patches.v16_1.migrate_payment_statuses",
    "paystack_frappe.patches.v16_1.register_payment_gateways",
):
    expect(f"patch log {patch}", bool(frappe.db.get_value("Patch Log", {"patch": patch, "skipped": 0}, "name")), True)
expect(
    "scheduled jobs of the former app",
    frappe.get_all("Scheduled Job Type", filters={"method": ["like", "frappe_paystack.%"]}, pluck="method"),
    [],
)
for method in (
    "paystack_frappe.core.sweeps.run_daily_reconciliation",
    "paystack_frappe.integrations.erpnext.settlement.retry_unposted_settlements",
):
    expect(f"scheduled job {method}", bool(frappe.db.exists("Scheduled Job Type", {"method": method})), True)
expect(
    "renamed job keeps its state",
    frappe.db.get_value("Scheduled Job Type", {"method": "frappe_paystack.utils.settlement.retry_unposted_settlements"}, "name"),
    None,
)
expect("scheduler rows for this app", len(frappe.get_all("Scheduled Job Type", filters={"method": ["like", "paystack_frappe.%"]}, pluck="name")) >= 4, True)

# --- the seeded 15.5.0 data, kept through the 16.1 migration and the rename

log = "Paystack Payment Log"
expected = {
    "legacy-processed": ("Paid", "Pending", "Processed", "Notified"),
    "legacy-completed": ("Paid", "Booked", "Completed", "Notified"),
    "legacy-attention": ("Paid", "Needs Attention", "Needs Attention", "Notified"),
    "legacy-refunded": ("Partially Refunded", "Booked", "Partially Refunded", "Notified"),
    "legacy-pending": ("Pending", None, None, "Pending"),
    "legacy-failed": ("Failed", None, None, "Not Required"),
}
for name, (status, booking, legacy, notification) in expected.items():
    row = frappe.db.get_value(log, name, ["status", "booking_status", "legacy_status", "notification_status", "company",
                                          "gateway_setting"], as_dict=True)
    expect(f"{name}.status", row.status, status)
    expect(f"{name}.booking_status", row.booking_status or None, booking)
    expect(f"{name}.legacy_status", row.legacy_status or None, legacy)
    expect(f"{name}.notification_status", row.notification_status, notification)
    # ERPNext-only data moved to Custom Fields with the same name: nothing lost.
    expect(f"{name}.company", row.company, "_Legacy Co")
    expect(f"{name}.gateway_setting", row.gateway_setting, "Legacy Paystack")

expect("payment_entry kept", frappe.db.get_value(log, "legacy-completed", "payment_entry"), "ACC-PAY-0001")
expect("retry_count kept", frappe.db.get_value(log, "legacy-processed", "retry_count"), 3)
meta = frappe.get_meta(log)
for field in ("company", "payment_request", "payment_entry", "booking_status", "retry_count"):
    expect(f"custom field {field}", bool(meta.has_field(field)), True)

setting = frappe.get_doc("Paystack Gateway Setting", "Legacy Paystack")
expect("setting.company", setting.get("company"), "_Legacy Co")
expect("setting.suspense_account", setting.get("suspense_account"), "Paystack Suspense - LC")
expect("setting.auto_refund_on_credit_note", setting.get("auto_refund_on_credit_note"), 1)
expect("setting.secret_key", setting.get_password("secret_key"), "sk_test_legacy_secret")

refund = frappe.db.get_value("Paystack Refund Log", "legacy-refund", ["status", "reversal_payment_entry", "company"], as_dict=True)
expect("refund.status", refund.status, "Processed")
expect("refund.reversal_payment_entry", refund.reversal_payment_entry, "ACC-PAY-0003")
expect("refund.company", refund.company, "_Legacy Co")

card = frappe.db.get_value("Paystack Customer Authorization", "legacy-card", ["party_type", "party", "gateway_setting"], as_dict=True)
expect("card.party_type", card.party_type, "Customer")
expect("card.party", card.party, "_Legacy Customer")
expect("card.gateway_setting", card.gateway_setting, "Legacy Paystack")

payout = frappe.db.get_value("Paystack Settlement", "9001", ["booking_status", "journal_entry", "company", "gateway_setting"], as_dict=True)
expect("settlement.booking_status", payout.booking_status, "Booked")
expect("settlement.journal_entry", payout.journal_entry, "ACC-JV-0001")
expect("settlement.company", payout.company, "_Legacy Co")
expect("settlement.gateway_setting", payout.gateway_setting, "Legacy Paystack")

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("upgrade from 15.5.0 verified")
