"""Refunds without any accounting system: full, partial, over-refund, webhook and polled status."""

import frappe

from frappe_paystack.core.constants import PARTIALLY_REFUNDED, REFUND_LOG, REFUNDED
from frappe_paystack.core.refunds import refund_payment
from frappe_paystack.tests.support.base import ADMIN_USER, LEARNER, PaystackTestCase, as_user


class TestRefunds(PaystackTestCase):
    def paid_session(self, amount=5000):
        session_name = self.session_of(self.payment_url(amount=amount, user=LEARNER))
        self.start(session_name)
        self.pay_and_notify(session_name)
        return session_name

    def test_partial_then_full_refund_by_webhook(self):
        session_name = self.paid_session()
        first = refund_payment(session_name, amount=2000, reason="Damaged")
        self.assertEqual(first.status, "Pending")
        self.assertTrue(first.refund_reference)
        call = self.fake.calls[-1]
        self.assertEqual(call["json"]["amount"], 200000)
        self.assertEqual(call["json"]["transaction"], self.session(session_name).transaction_id)

        # Pending refunds hold the balance: only 3000 remains refundable.
        self.assertRaises(frappe.ValidationError, refund_payment, session_name, 3500)

        refund = self.fake.set_refund_status(first.refund_reference, "processed")
        self.deliver("refund.processed", {"id": refund["id"], "status": "processed", "amount": 200000, "currency": "NGN",
                                          "transaction_reference": session_name})
        session = self.session(session_name)
        self.assertEqual(frappe.db.get_value(REFUND_LOG, first.name, "status"), "Processed")
        self.assertEqual(session.total_refunded, 2000)
        self.assertEqual(session.status, PARTIALLY_REFUNDED)

        second = refund_payment(session_name)  # the remaining balance
        self.assertEqual(second.refund_amount, 3000)
        self.fake.set_refund_status(second.refund_reference, "processed")
        # Paystack's refund webhook may omit the refund id and send only the transaction reference.
        self.deliver("refund.processed", {"status": "processed", "amount": 300000, "currency": "NGN",
                                          "transaction_reference": session_name, "refund_reference": "PROC-1"})
        session = self.session(session_name)
        self.assertEqual(session.total_refunded, 5000)
        self.assertEqual(session.status, REFUNDED)
        self.assertRaises(frappe.ValidationError, refund_payment, session_name, 1)

    def test_failed_refund_releases_the_balance(self):
        session_name = self.paid_session()
        refund = refund_payment(session_name, amount=5000)
        self.deliver("refund.failed", {"id": int(refund.refund_reference), "status": "failed", "amount": 500000,
                                       "transaction_reference": session_name})
        self.assertEqual(frappe.db.get_value(REFUND_LOG, refund.name, "status"), "Failed")
        again = refund_payment(session_name, amount=5000)
        self.assertEqual(again.status, "Pending")

    def test_refund_status_is_polled_when_webhooks_are_missed(self):
        from frappe_paystack.core.sweeps import sync_refunds

        session_name = self.paid_session()
        refund = refund_payment(session_name, amount=1000)
        self.fake.set_refund_status(refund.refund_reference, "processed")
        sync_refunds()
        self.assertEqual(frappe.db.get_value(REFUND_LOG, refund.name, "status"), "Processed")
        self.assertEqual(self.session(session_name).total_refunded, 1000)

    def test_final_status_is_not_walked_back(self):
        session_name = self.paid_session()
        refund = refund_payment(session_name, amount=1000)
        from frappe_paystack.core.refunds import apply_refund_data

        apply_refund_data(refund.name, {"id": refund.refund_reference, "status": "processed"})
        apply_refund_data(refund.name, {"id": refund.refund_reference, "status": "pending"})
        self.assertEqual(frappe.db.get_value(REFUND_LOG, refund.name, "status"), "Processed")

    def test_paystack_refusal_marks_the_refund_failed(self):
        session_name = self.paid_session()
        self.fake.fail_next = "connection reset"
        refund = refund_payment(session_name, amount=1000)
        self.assertEqual(refund.status, "Failed")
        self.assertTrue(refund.errors)

    def test_uncaptured_payment_cannot_be_refunded(self):
        session_name = self.session_of(self.payment_url())
        self.assertRaises(frappe.ValidationError, refund_payment, session_name, 100)

    def test_refund_endpoint_permissions(self):
        from frappe_paystack.api import initiate_refund_from_log

        session_name = self.paid_session()
        with as_user(LEARNER):
            self.assertRaises(frappe.PermissionError, initiate_refund_from_log, session_name, 100)
        with as_user(ADMIN_USER):
            name = initiate_refund_from_log(session_name, 100, "Goodwill")
        self.assertEqual(frappe.db.get_value(REFUND_LOG, name, "refund_amount"), 100)

    def test_dashboard_refund_is_recorded(self):
        """A refund started on the Paystack dashboard arrives only as a webhook."""
        session_name = self.paid_session()
        self.deliver("refund.processed", {"id": 777001, "status": "processed", "amount": 50000, "currency": "NGN",
                                          "transaction_reference": session_name})
        refund = frappe.get_all(REFUND_LOG, filters={"payment_log": session_name}, fields=["refund_amount", "status"])
        self.assertEqual(refund[0].refund_amount, 500)
        self.assertEqual(refund[0].status, "Processed")
        self.assertEqual(self.session(session_name).total_refunded, 500)

    def test_controller_refund_methods(self):
        session_name = self.paid_session()
        refund = self.controller().refund_payment(session_name, amount=250)
        self.assertEqual(refund.refund_amount, 250)
        self.assertTrue(self.controller().fetch_refund(refund.refund_reference))
        self.assertEqual(len(self.controller().fetch_refunds(session_name)), 1)
