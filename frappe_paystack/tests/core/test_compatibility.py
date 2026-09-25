"""15.x dotted paths and helpers keep working (and never pull ERPNext in eagerly)."""

import importlib

import frappe

from frappe_paystack.tests.support.base import PaystackTestCase, installed

LEGACY_MODULES = (
    "frappe_paystack.utils",
    "frappe_paystack.utils.utils",
    "frappe_paystack.utils.payment_request",
    "frappe_paystack.utils.pos_payment",
    "frappe_paystack.utils.portal",
    "frappe_paystack.utils.printing",
    "frappe_paystack.utils.qr",
    "frappe_paystack.utils.reconciliation",
    "frappe_paystack.utils.reconciliation_api",
    "frappe_paystack.utils.scheduled_jobs",
    "frappe_paystack.utils.settlement",
    "frappe_paystack.utils.subscription",
    "frappe_paystack.utils.sweep",
    "frappe_paystack.events",
    "frappe_paystack.setup",
    "frappe_paystack.migration",
)

WHITELISTED = (
    "frappe_paystack.api.paystack_webhook",
    "frappe_paystack.api.validate_payment_link",
    "frappe_paystack.api.start_hosted_checkout",
    "frappe_paystack.api.create_payment_link",
    "frappe_paystack.api.initiate_refund_from_log",
    "frappe_paystack.api.payment_link_qr",
    "frappe_paystack.api.saved_cards",
    "frappe_paystack.api.charge_saved_card",
    "frappe_paystack.api.is_enabled_for_company",
    "frappe_paystack.utils.pos_payment.send_pos_payment_link",
    "frappe_paystack.utils.pos_payment.pos_payment_status",
    "frappe_paystack.utils.portal.download_payment_receipt",
    "frappe_paystack.utils.reconciliation_api.run_reconciliation",
    "frappe_paystack.frappe_paystack.doctype.paystack_refund_log.paystack_refund_log.send_refund_receipt",
)


class TestCompatibility(PaystackTestCase):
    def test_legacy_modules_import(self):
        for module in LEGACY_MODULES:
            importlib.import_module(module)

    def test_legacy_endpoints_are_still_whitelisted(self):
        for path in WHITELISTED:
            self.assertIn(frappe.get_attr(path), frappe.whitelisted, path)

    def test_utils_facade(self):
        from frappe_paystack import utils

        self.assertEqual(utils.to_minor_units(12.5, "NGN"), 1250)
        self.assertEqual(utils.normalize_currency(" inr "), "INR")  # no silent NGN fallback any more
        self.assertTrue(utils.verify_signature(b"x", utils.hmac_sha512(b"x", "k"), "k"))

    def test_erpnext_endpoints_explain_themselves_without_erpnext(self):
        if installed("erpnext"):
            self.skipTest("ERPNext is installed")
        from frappe_paystack.api import create_payment_link, is_enabled_for_company

        self.assertFalse(is_enabled_for_company("Any Company"))
        self.assertRaises(frappe.ValidationError, create_payment_link, "Sales Invoice", "SINV-0001")

    def test_legacy_payment_link_helpers(self):
        from frappe_paystack.api import start_hosted_checkout, validate_payment_link

        session_name = self.session_of(self.payment_url())
        self.assertTrue(validate_payment_link(session_name)["is_payable"])
        self.assertTrue(start_hosted_checkout(session_name).startswith("https://checkout.paystack.com/"))
