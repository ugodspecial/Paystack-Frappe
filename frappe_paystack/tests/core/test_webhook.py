"""Webhooks: signature, IP allowlist, success, failure, verification, duplicates, retries, accounts."""

import frappe

from frappe_paystack.core.constants import FAILED, INTEGRATION_REQUEST, NEEDS_ATTENTION, PAID, PAYMENT_LOG, PENDING
from frappe_paystack.tests.support.base import LEARNER, SECRET, PaystackTestCase, make_setting
from frappe_paystack.tests.support.paystack_fake import sign, webhook


class TestWebhookSecurity(PaystackTestCase):
    def test_invalid_signature_is_rejected_and_logged(self):
        body, _signature = webhook("charge.success", {"id": 1, "reference": "x"}, SECRET)
        from frappe_paystack.core.webhook import receive

        before = frappe.db.count(INTEGRATION_REQUEST, {"error": "Invalid Paystack signature"})
        self.assertRaises(frappe.PermissionError, receive, body, "0" * 128, "52.31.139.75")
        self.assertRaises(frappe.PermissionError, receive, body, None, "52.31.139.75")
        self.assertEqual(frappe.db.count(INTEGRATION_REQUEST, {"error": "Invalid Paystack signature"}), before + 2)

    def test_signature_from_the_webhook_secret_override(self):
        make_setting(self.setting.name, SECRET, "pk_test_core_public", webhook_secret="whsec_only")
        session_name = self.session_of(self.payment_url())
        self.start(session_name)
        tx = self.fake.pay(session_name)
        self.assertRaises(frappe.PermissionError, self.deliver, "charge.success", tx, SECRET)
        self.deliver("charge.success", tx, "whsec_only")
        self.assertEqual(self.session(session_name).status, PAID)

    def test_ip_allowlist(self):
        make_setting(self.setting.name, SECRET, "pk_test_core_public", allowed_webhook_ips="52.31.139.75\n10.0.0.0/8")
        self.deliver("transfer.success", {"id": 10}, ip="10.1.2.3")
        self.assertRaises(frappe.PermissionError, self.deliver, "transfer.success", {"id": 11}, ip="41.58.1.1")

    def test_http_endpoint(self):
        """The whitelisted endpoint reads the raw body and header as Paystack sends them."""
        from werkzeug.test import EnvironBuilder

        from frappe_paystack.api import paystack_webhook

        session_name = self.session_of(self.payment_url())
        self.start(session_name)
        body, signature = webhook("charge.success", self.fake.pay(session_name), SECRET)
        builder = EnvironBuilder(method="POST", data=body, headers={"x-paystack-signature": signature,
                                                                    "Content-Type": "application/json"})
        saved_request = getattr(frappe.local, "request", None)
        frappe.local.request = builder.get_request()
        frappe.local.request_ip = "52.31.139.75"
        frappe.set_user("Guest")
        try:
            paystack_webhook()
        finally:
            frappe.set_user("Administrator")
            frappe.local.request = saved_request
        self.assertEqual(frappe.local.response.get("http_status_code"), 200)
        self.assertEqual(self.session(session_name).status, PAID)


class TestWebhookProcessing(PaystackTestCase):
    def test_success_marks_paid_and_notifies_once(self):
        session_name = self.session_of(self.payment_url(user=LEARNER))
        self.start(session_name, user=LEARNER)
        tx = self.pay_and_notify(session_name)
        session = self.session(session_name)
        self.assertEqual(session.status, PAID)
        self.assertEqual(session.amount_paid, 5000)
        self.assertEqual(session.currency_paid, "NGN")
        self.assertEqual(session.transaction_id, str(tx["id"]))
        self.assertEqual(session.paystack_fee, 1.5)
        self.assertEqual(session.completed_via, "Webhook")
        self.assertEqual(session.notification_status, "Notified")
        self.assertEqual(len(self.recorder.calls), 1)
        self.assertEqual(self.recorder.calls[0]["status"], "Completed")

    def test_duplicate_delivery_is_acknowledged_and_ignored(self):
        session_name = self.session_of(self.payment_url())
        self.start(session_name)
        tx = self.fake.pay(session_name)
        first = self.deliver("charge.success", tx)
        second = self.deliver("charge.success", tx)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(len(self.recorder.calls), 1)

    def test_failed_processing_is_retried_on_redelivery(self):
        session_name = self.session_of(self.payment_url())
        self.start(session_name)
        tx = self.fake.pay(session_name)
        from unittest.mock import patch

        with patch("frappe_paystack.core.webhook.process_charge", side_effect=RuntimeError("worker died")):
            first = self.deliver("charge.success", tx)
        self.assertEqual(frappe.db.get_value(INTEGRATION_REQUEST, first["integration_request"], "status"), "Failed")
        self.assertEqual(self.session(session_name).status, PENDING)
        second = self.deliver("charge.success", tx)
        self.assertEqual(second["integration_request"], first["integration_request"])
        self.assertEqual(self.session(session_name).status, PAID)

    def test_verification_prefers_paystack_over_the_payload(self):
        """A payload claiming success for a transaction Paystack reports as failed is not trusted."""
        session_name = self.session_of(self.payment_url())
        self.start(session_name)
        tx = self.fake.pay(session_name)
        self.fake.fail(session_name)
        self.deliver("charge.success", tx)
        self.assertEqual(self.session(session_name).status, FAILED)
        self.assertEqual(len(self.recorder.calls), 0)
        self.assertEqual(len(self.recorder.failures), 1)

    def test_amount_mismatch_needs_attention_without_notifying(self):
        session_name = self.session_of(self.payment_url(amount=5000))
        self.start(session_name)
        self.pay_and_notify(session_name, amount=100000)
        session = self.session(session_name)
        self.assertEqual(session.status, NEEDS_ATTENTION)
        self.assertIn("expects", session.errors)
        self.assertEqual(self.recorder.calls, [])

    def test_currency_mismatch_needs_attention(self):
        session_name = self.session_of(self.payment_url(amount=5000))
        self.start(session_name)
        self.pay_and_notify(session_name, currency="GHS")
        self.assertEqual(self.session(session_name).status, NEEDS_ATTENTION)
        self.assertEqual(self.recorder.calls, [])

    def test_event_signed_by_another_account_is_ignored(self):
        other = make_setting("Paystack Other Account", "sk_test_other", "pk_test_other")
        self.addCleanup(frappe.delete_doc, "Paystack Gateway Setting", other.name, force=True)
        session_name = self.session_of(self.payment_url())
        self.start(session_name)
        tx = self.fake.pay(session_name)
        self.deliver("charge.success", tx, secret="sk_test_other")
        self.assertEqual(self.session(session_name).status, PENDING)
        self.assertEqual(self.recorder.calls, [])

    def test_unknown_reference_is_recorded(self):
        result = self.deliver("charge.success", {"id": 99, "reference": "not-ours", "status": "success", "amount": 100,
                                                 "currency": "NGN"})
        output = frappe.db.get_value(INTEGRATION_REQUEST, result["integration_request"], "output")
        self.assertIn("no payment for reference not-ours", output)

    def test_upstream_metadata_reference_still_resolves(self):
        """Transactions started by 15.x carry the Payment Log in metadata.reference."""
        session_name = self.session_of(self.payment_url())
        self.start(session_name)
        tx = self.fake.pay(session_name)
        tx["metadata"] = {"reference": session_name}
        self.deliver("charge.success", tx)
        self.assertEqual(self.session(session_name).status, PAID)

    def test_duplicate_capture_is_flagged(self):
        session_name = self.session_of(self.payment_url())
        self.start(session_name)
        self.pay_and_notify(session_name)
        # The payer paid again in a second tab (a second attempt reference).
        second = f"{session_name}-2"
        frappe.db.set_value(PAYMENT_LOG, session_name, "attempt_count", 2)
        frappe.db.commit()
        self.fake.initialize({"reference": second, "amount": 500000, "currency": "NGN", "email": LEARNER,
                              "metadata": {"session": session_name}})
        self.deliver("charge.success", self.fake.pay(second))
        session = self.session(session_name)
        self.assertEqual(session.status, NEEDS_ATTENTION)
        self.assertIn("second transaction", session.errors)
        self.assertEqual(len(self.recorder.calls), 1)

    def test_signature_helper_matches_paystack(self):
        from frappe_paystack.core.accounts import verify_signature

        self.assertTrue(verify_signature(b"{}", sign(b"{}", "s"), "s"))
        self.assertFalse(verify_signature(b"{}", sign(b"{}", "s"), "t"))
