"""Payment initialisation, checkout, references, currency and amount rules, expiry, anonymous checkout."""

import json

import frappe
from frappe.utils import add_to_date, now_datetime

from frappe_paystack.core import money
from frappe_paystack.core.constants import CANCELLED, EXPIRED, FAILED, PAID, PAYMENT_LOG, PENDING
from frappe_paystack.core.lifecycle import NotPayable
from frappe_paystack.tests.support.base import LEARNER, PaystackTestCase, make_setting


class TestPaymentInitialisation(PaystackTestCase):
    def test_get_payment_url_creates_a_pending_session(self):
        todo = self.make_todo()
        url = self.payment_url(todo, amount=7500, user=LEARNER, payment="LMS-PAY-1")
        self.assertIn("/paystack-checkout/", url)
        session = self.session(self.session_of(url))
        self.assertEqual(session.status, PENDING)
        self.assertEqual((session.linked_doctype, session.linked_docname), ("ToDo", todo.name))
        self.assertEqual(session.amount, 7500)
        self.assertEqual(session.currency, "NGN")
        self.assertEqual(session.gateway_setting, self.setting.name)
        self.assertEqual(session.payment_gateway, "Paystack")
        self.assertEqual(session.initiated_by, LEARNER)
        self.assertEqual(session.run_as_user, LEARNER)
        data = json.loads(session.request_data)
        # Extras are handed back verbatim; order_id defaults to the session name.
        self.assertEqual(data["payment"], "LMS-PAY-1")
        self.assertEqual(data["order_id"], session.name)

    def test_integration_request_is_owned_by_the_caller(self):
        """Payments consumers (LMS) fall back to the latest Integration Request owned by the user."""
        todo = self.make_todo()
        session = self.session(self.session_of(self.payment_url(todo, user=LEARNER, payment="P1")))
        request = frappe.get_doc("Integration Request", session.integration_request)
        self.assertEqual(request.owner, LEARNER)
        self.assertEqual((request.reference_doctype, request.reference_docname), ("ToDo", todo.name))
        self.assertEqual(json.loads(request.data)["payment"], "P1")

    def test_same_intent_reuses_the_open_session(self):
        todo = self.make_todo()
        first = self.payment_url(todo, amount=5000, user=LEARNER)
        second = self.payment_url(todo, amount=5000, user=LEARNER)
        third = self.payment_url(todo, amount=6000, user=LEARNER)
        self.assertEqual(first, second)
        self.assertNotEqual(first, third)

    def test_unsupported_currency_is_refused_not_rewritten(self):
        self.assertRaises(money.CurrencyNotSupported, self.payment_url, amount=5000, currency="INR")
        self.assertFalse(frappe.db.exists(PAYMENT_LOG, {"currency": "NGN", "amount": 5000}))

    def test_currency_must_be_enabled_on_the_account(self):
        self.assertRaises(money.CurrencyNotSupported, self.payment_url, amount=10, currency="USD")
        make_setting(self.setting.name, "sk_test_core_secret", "pk_test_core_public", additional_currencies="USD")
        session = self.session(self.session_of(self.payment_url(amount=10, currency="usd")))
        self.assertEqual(session.currency, "USD")

    def test_amount_rules(self):
        for bad in (0, -5, 49.99, "abc", 100.001):
            self.assertRaises(frappe.ValidationError, self.payment_url, amount=bad)
        make_setting(self.setting.name, "sk_test_core_secret", "pk_test_core_public", additional_currencies="XOF, GHS, KES, ZAR")
        self.assertRaises(money.InvalidAmount, self.payment_url, amount=100.5, currency="XOF")
        self.payment_url(amount=100, currency="XOF")
        self.payment_url(amount=0.10, currency="GHS")
        self.assertRaises(money.InvalidAmount, self.payment_url, amount=2.99, currency="KES")

    def test_minor_units(self):
        self.assertEqual(money.to_minor("5000", "NGN"), 500000)
        self.assertEqual(money.to_minor(0.1, "GHS"), 10)
        self.assertEqual(money.to_minor(19.99, "USD"), 1999)
        self.assertEqual(money.to_minor(1000, "XOF"), 100000)
        self.assertEqual(money.from_minor(500000), 5000.0)


class TestCheckout(PaystackTestCase):
    def test_inline_checkout_is_initialised_on_the_server(self):
        session_name = self.session_of(self.payment_url(amount=5000))
        checkout = self.start(session_name)
        self.assertEqual(checkout["mode"], "Inline")
        self.assertEqual(checkout["reference"], session_name)
        self.assertTrue(checkout["access_code"])
        self.assertEqual(checkout["public_key"], "pk_test_core_public")
        call = self.fake.calls[-1]
        self.assertEqual(call["path"], "/transaction/initialize")
        # Amount, currency and reference come from the session, never from the browser.
        self.assertEqual(call["json"]["amount"], 500000)
        self.assertEqual(call["json"]["currency"], "NGN")
        self.assertEqual(call["json"]["metadata"]["session"], session_name)
        self.assertIn(f"/paystack-checkout/{session_name}", call["json"]["callback_url"])

    def test_hosted_checkout_returns_the_paystack_page(self):
        make_setting(self.setting.name, "sk_test_core_secret", "pk_test_core_public", checkout_mode="Hosted")
        checkout = self.start(self.session_of(self.payment_url()))
        self.assertEqual(checkout["mode"], "Hosted")
        self.assertTrue(checkout["authorization_url"].startswith("https://checkout.paystack.com/"))

    def test_second_open_of_the_same_checkout_reuses_the_transaction(self):
        session_name = self.session_of(self.payment_url())
        first = self.start(session_name)
        second = self.start(session_name)
        self.assertEqual(first["access_code"], second["access_code"])
        self.assertEqual(len([c for c in self.fake.calls if c["path"] == "/transaction/initialize"]), 1)

    def test_retry_after_failure_uses_a_new_reference(self):
        session_name = self.session_of(self.payment_url())
        self.start(session_name)
        self.fake.fail(session_name)
        self.deliver("charge.success", self.fake.transaction(session_name))  # status failed in the payload
        from frappe_paystack.core.lifecycle import verify

        self.assertEqual(verify(session_name)["status"], FAILED)
        retry = self.start(session_name)
        self.assertEqual(retry["reference"], f"{session_name}-2")
        self.assertEqual(self.session(session_name).status, PENDING)
        self.pay_and_notify(session_name)
        self.assertEqual(self.session(session_name).status, PAID)

    def test_abandoned_checkout_is_cancelled_and_retryable(self):
        from frappe_paystack.core.lifecycle import verify

        session_name = self.session_of(self.payment_url())
        self.start(session_name)
        self.assertEqual(verify(session_name)["status"], CANCELLED)
        self.assertEqual(self.start(session_name)["reference"], f"{session_name}-2")

    def test_anonymous_checkout_asks_for_an_email(self):
        session_name = self.session_of(self.payment_url(user="Guest", payer_email=""))
        session = self.session(session_name)
        self.assertEqual(session.run_as_user, "Guest")
        self.assertFalse(session.payer_email)
        self.assertRaises(frappe.ValidationError, self.start, session_name, None, "Guest")
        checkout = self.start(session_name, email="guest-payer@example.com", user="Guest")
        self.assertTrue(checkout["access_code"])
        self.assertEqual(self.session(session_name).payer_email, "guest-payer@example.com")
        self.pay_and_notify(session_name)
        self.assertEqual(self.recorder.calls[-1]["user"], "Guest")

    def test_expired_link_refuses_checkout_but_late_capture_settles(self):
        session_name = self.session_of(self.payment_url())
        self.start(session_name)
        frappe.db.set_value(PAYMENT_LOG, session_name, "expires_at", add_to_date(now_datetime(), hours=-1))
        frappe.db.commit()
        self.assertRaises(NotPayable, self.start, session_name)
        from frappe_paystack.core.sweeps import verify_open_sessions

        verify_open_sessions()
        self.assertEqual(self.session(session_name).status, EXPIRED)
        # The payer had already paid on Paystack: the capture still settles.
        self.pay_and_notify(session_name)
        self.assertEqual(self.session(session_name).status, PAID)
        self.assertEqual(len(self.recorder.calls), 1)

    def test_validity_window_is_stamped(self):
        make_setting(self.setting.name, "sk_test_core_secret", "pk_test_core_public", payment_link_validity_hours=2)
        session = self.session(self.session_of(self.payment_url()))
        self.assertTrue(session.expires_at)

    def test_completed_payment_cannot_be_paid_again(self):
        session_name = self.session_of(self.payment_url())
        self.start(session_name)
        self.pay_and_notify(session_name)
        self.assertRaises(NotPayable, self.start, session_name)
        from frappe_paystack.core.lifecycle import OUTCOME_ALREADY_PAID, verify

        self.assertEqual(verify(session_name)["outcome"], OUTCOME_ALREADY_PAID)

    def test_checkout_page_and_status_endpoint(self):
        from frappe_paystack.api import get_payment_status
        from frappe_paystack.core.portal import page_context as render_context

        session_name = self.session_of(self.payment_url(amount=5000))
        frappe.set_user("Guest")
        status = get_payment_status(session_name)
        context = render_context(session_name)
        frappe.set_user("Administrator")
        self.assertTrue(status["is_payable"])
        self.assertEqual(status["formatted_amount"], money.format_amount(5000, "NGN"))
        for secret in ("secret_key", "access_code", "request_data", "run_as_user"):
            self.assertNotIn(secret, status)
        self.assertEqual(context.doc["reference"], session_name)

    def test_hosted_callback_verifies_and_redirects(self):
        from frappe_paystack.core.portal import page_context as render_context

        session_name = self.session_of(self.payment_url(user=LEARNER))
        self.start(session_name)
        self.fake.pay(session_name)
        frappe.set_user(LEARNER)
        try:
            render_context(session_name, trxref=session_name)
            self.fail("expected a redirect")
        except frappe.Redirect:
            location = frappe.local.flags.redirect_location
        finally:
            frappe.set_user("Administrator")
        self.assertIn("/payment-success?", location)
        self.assertIn("redirect_to=%2Fthank-you", location)
        self.assertEqual(self.session(session_name).status, PAID)
        self.assertEqual(self.recorder.calls[-1]["user"], LEARNER)
