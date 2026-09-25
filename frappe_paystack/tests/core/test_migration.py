"""The 16.1 patches map 15.x rows correctly and are idempotent (core side; ERPNext data is covered by CI's upgrade job)."""

import frappe
from frappe.utils import now_datetime

from frappe_paystack.core.constants import PAYMENT_LOG, REFUND_LOG
from frappe_paystack.tests.support.base import ACCOUNT, PaystackTestCase


def insert_legacy_log(name, status, captured=True):
    now = now_datetime()
    frappe.db.sql(
        """insert into `tabPaystack Payment Log`
        (name, creation, modified, owner, modified_by, docstatus, status, amount, currency, amount_paid, currency_paid,
         transaction_id, linked_doctype, linked_docname)
        values (%s, %s, %s, 'Administrator', 'Administrator', 0, %s, 1000, 'NGN', %s, %s, %s, 'ToDo', 'legacy')""",
        (name, now, now, status, 1000 if captured else 0, "NGN" if captured else None, f"tx-{name}" if captured else None),
    )


class TestMigration(PaystackTestCase):
    def test_payment_status_mapping(self):
        from frappe_paystack.patches.v16_1 import backfill_gateway_setting, migrate_payment_statuses

        for name, status, captured in (("mig-processed", "Processed", True), ("mig-completed", "Completed", True),
                                       ("mig-attention", "Needs Attention", True), ("mig-pending", "Pending", False),
                                       ("mig-failed", "Failed", False)):
            insert_legacy_log(name, status, captured)
        frappe.db.commit()

        migrate_payment_statuses.execute()
        migrate_payment_statuses.execute()  # idempotent
        backfill_gateway_setting.execute()

        rows = {r.name: r for r in frappe.get_all(PAYMENT_LOG, filters={"name": ["like", "mig-%"]},
                                                   fields=["name", "status", "legacy_status", "notification_status",
                                                           "gateway_setting"])}
        self.assertEqual((rows["mig-processed"].status, rows["mig-processed"].legacy_status), ("Paid", "Processed"))
        self.assertEqual((rows["mig-completed"].status, rows["mig-completed"].legacy_status), ("Paid", "Completed"))
        self.assertEqual(rows["mig-attention"].status, "Paid")
        self.assertEqual(rows["mig-processed"].notification_status, "Notified")
        self.assertEqual(rows["mig-pending"].status, "Pending")
        self.assertEqual(rows["mig-pending"].notification_status, "Pending")
        self.assertEqual(rows["mig-failed"].notification_status, "Not Required")
        for row in rows.values():
            self.assertEqual(row.gateway_setting, ACCOUNT)
        # A migrated capture is not notified again.
        from frappe_paystack.core.sweeps import retry_notifications

        retry_notifications()
        self.assertEqual(self.recorder.calls, [])

    def test_refund_status_mapping(self):
        from frappe_paystack.patches.v16_1 import migrate_refund_statuses

        insert_legacy_log("mig-refunded", "Partially Refunded")
        now = now_datetime()
        frappe.db.sql(
            """insert into `tabPaystack Refund Log` (name, creation, modified, owner, modified_by, docstatus, payment_log,
            refund_amount, currency, status) values ('mig-refund', %s, %s, 'Administrator', 'Administrator', 0,
            'mig-refunded', 100, 'NGN', 'Completed')""",
            (now, now),
        )
        frappe.db.commit()
        migrate_refund_statuses.execute()
        self.assertEqual(frappe.db.get_value(REFUND_LOG, "mig-refund", "status"), "Processed")

    def test_other_patches_are_safe_without_erpnext(self):
        from frappe_paystack.patches.v15_0 import (
            abandon_expired_settlements,
            backfill_payment_request,
            create_payment_gateway,
            route_payments_per_company,
            seed_payment_log_retries,
        )
        from frappe_paystack.patches.v16_1 import (
            activate_erpnext_adapter,
            migrate_saved_cards,
            migrate_settlements,
            register_payment_gateways,
        )

        for patch in (activate_erpnext_adapter, create_payment_gateway, backfill_payment_request, seed_payment_log_retries,
                      abandon_expired_settlements, route_payments_per_company, migrate_saved_cards, migrate_settlements,
                      register_payment_gateways):
            patch.execute()
        self.assertEqual(frappe.db.get_value("Payment Gateway", "Paystack", "gateway_controller"), ACCOUNT)
