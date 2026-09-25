"""Paystack Gateway Setting: keys, test mode, currencies, webhook secret, Payments contract."""

import frappe

from frappe_paystack.core.constants import GATEWAY_SETTING
from frappe_paystack.tests.support.base import PaystackTestCase, make_setting


class TestSettings(PaystackTestCase):
    def test_live_key_refused_in_test_mode(self):
        doc = frappe.get_doc(GATEWAY_SETTING, self.setting.name)
        doc.secret_key = "sk_live_xxx"
        self.assertRaises(frappe.ValidationError, doc.save)

    def test_test_key_refused_outside_test_mode(self):
        doc = frappe.get_doc(GATEWAY_SETTING, self.setting.name)
        doc.test_mode = 0
        doc.public_key = "pk_test_still"
        self.assertRaises(frappe.ValidationError, doc.save)

    def test_unsupported_account_currency_refused(self):
        doc = frappe.get_doc(GATEWAY_SETTING, self.setting.name)
        doc.currency = "INR"
        self.assertRaises(frappe.ValidationError, doc.save)

    def test_additional_currencies_are_cleaned(self):
        doc = frappe.get_doc(GATEWAY_SETTING, self.setting.name)
        doc.additional_currencies = "usd,\nNGN, ghs"
        doc.save()
        self.assertEqual(doc.additional_currencies, "USD, GHS")
        self.assertEqual(doc.get_allowed_currencies(), ["NGN", "USD", "GHS"])

    def test_webhook_secret_defaults_to_secret_key(self):
        self.assertEqual(self.setting.get_webhook_secret(), "sk_test_core_secret")
        doc = make_setting(self.setting.name, "sk_test_core_secret", "pk_test_core_public", webhook_secret="whsec_override")
        self.assertEqual(doc.get_webhook_secret(), "whsec_override")

    def test_controller_contract(self):
        controller = self.controller()
        controller.validate_transaction_currency("NGN")
        controller.validate_transaction_currency("XOF")
        self.assertRaises(frappe.ValidationError, controller.validate_transaction_currency, "INR")
        self.assertRaises(frappe.ValidationError, controller.validate_minimum_transaction_amount, "NGN", 10)
        controller.validate_minimum_transaction_amount("NGN", 50)
        self.assertEqual(set(controller.get_supported_currencies()), {"NGN", "GHS", "ZAR", "KES", "USD", "XOF"})
        self.assertTrue(controller.on_payment_request_submission(frappe._dict(currency="NGN")))
        self.assertFalse(controller.on_payment_request_submission(frappe._dict(currency="INR")))

    def test_test_connection_uses_the_secret_key(self):
        result = self.setting.test_connection()
        self.assertTrue(result["ok"])
        self.assertIn("/api/method/frappe_paystack.api.paystack_webhook", result["webhook_url"])
        self.assertEqual(self.fake.calls[-1]["path"], "/transaction")

    def test_bad_key_reports_paystack_refusal(self):
        doc = make_setting(self.setting.name, "sk_bad_key_test", "pk_test_core_public")
        self.assertRaises(frappe.ValidationError, doc.test_connection)

    def test_disabling_moves_the_default_gateway(self):
        second = make_setting("Paystack Backup", "sk_test_backup", "pk_test_backup")
        self.addCleanup(frappe.delete_doc, GATEWAY_SETTING, second.name, force=True)
        self.setting.enabled = 0
        self.setting.save()
        self.assertEqual(frappe.db.get_value("Payment Gateway", "Paystack", "gateway_controller"), second.name)
        make_setting(self.setting.name, "sk_test_core_secret", "pk_test_core_public")
        frappe.db.set_value("Payment Gateway", "Paystack", "gateway_controller", self.setting.name)
        frappe.db.commit()
