"""The app installs and registers with Payments on a site with only Frappe and Payments."""

import frappe

from frappe_paystack.core.constants import DEFAULT_GATEWAY_NAME, GATEWAY_SETTING, PAYSTACK_MANAGER
from frappe_paystack.tests.support.base import ACCOUNT, PaystackTestCase, installed, make_setting


class TestInstall(PaystackTestCase):
    def test_required_apps_are_only_payments(self):
        from frappe_paystack import hooks

        self.assertEqual([app.split("/")[-1] for app in hooks.required_apps], ["payments"])

    def test_installed_alongside_payments(self):
        apps = frappe.get_installed_apps()
        self.assertIn("payments", apps)
        self.assertIn("frappe_paystack", apps)

    def test_role_and_workspace_widgets_exist(self):
        from frappe_paystack.install import NUMBER_CARDS

        self.assertTrue(frappe.db.exists("Role", PAYSTACK_MANAGER))
        for card in NUMBER_CARDS:
            self.assertTrue(frappe.db.exists("Number Card", card["name"]), card["name"])

    def test_number_cards_count_without_error(self):
        from frappe.desk.doctype.number_card.number_card import get_result
        from frappe_paystack.install import NUMBER_CARDS

        for card in NUMBER_CARDS:
            doc = frappe.get_doc("Number Card", card["name"])
            get_result(doc, frappe.as_json(frappe.parse_json(doc.filters_json)))

    def test_enabled_setting_registers_payment_gateway(self):
        gateway = frappe.get_doc("Payment Gateway", DEFAULT_GATEWAY_NAME)
        self.assertEqual(gateway.gateway_settings, GATEWAY_SETTING)
        self.assertEqual(gateway.gateway_controller, ACCOUNT)
        self.assertEqual(self.controller().name, ACCOUNT)

    def test_separate_payment_gateway(self):
        second = make_setting("Paystack Second", "sk_test_second", "pk_test_second", separate_payment_gateway=1)
        self.addCleanup(frappe.delete_doc, GATEWAY_SETTING, second.name, force=True)
        self.assertEqual(frappe.db.get_value("Payment Gateway", "Paystack-Paystack Second", "gateway_controller"), second.name)
        # The default gateway keeps pointing at the first enabled account.
        self.assertEqual(frappe.db.get_value("Payment Gateway", DEFAULT_GATEWAY_NAME, "gateway_controller"), ACCOUNT)
        frappe.db.delete("Payment Gateway", "Paystack-Paystack Second")
        frappe.db.commit()

    def test_payment_gateway_enabled_is_broadcast(self):
        from unittest.mock import patch

        with patch("frappe_paystack.core.accounts.call_hook_method") as hook:
            self.setting.save()
        hook.assert_any_call("payment_gateway_enabled", gateway=DEFAULT_GATEWAY_NAME)

    def test_web_pages_and_jinja_render(self):
        """Hooks that run on every page (website context, jinja methods) must not need ERPNext."""
        from frappe.utils.jinja import get_jenv
        from frappe.website.serve import get_response
        from werkzeug.test import EnvironBuilder

        get_jenv()
        session = self.session_of(self.payment_url())
        saved_request = getattr(frappe.local, "request", None)
        try:
            for path in ("/", f"/paystack-checkout/{session}", "/paystack-checkout/does-not-exist"):
                frappe.local.request = EnvironBuilder(path=path).get_request()
                frappe.set_user("Guest")
                response = get_response(path)
                frappe.set_user("Administrator")
                self.assertIn(response.status_code, (200, 404), path)
                if session in path:
                    self.assertIn(session, response.get_data(as_text=True))
        finally:
            frappe.local.request = saved_request

    def test_erpnext_adapter_inactive_without_erpnext(self):
        if installed("erpnext"):
            self.skipTest("ERPNext is installed")
        from frappe_paystack.integrations import erpnext

        self.assertFalse(erpnext.is_available())
        self.assertFalse(frappe.get_meta(GATEWAY_SETTING).has_field("company"))
        self.assertFalse(frappe.get_meta("Paystack Payment Log").has_field("payment_entry"))
        # Scheduler entry points of the adapter are no-ops.
        from frappe_paystack.integrations.erpnext import payment_entry, settlement, subscriptions

        payment_entry.retry_stuck_bookings()
        settlement.retry_unposted_settlements()
        subscriptions.collect_subscription_payments()

    def test_scheduled_jobs_run_without_error(self):
        from frappe_paystack.core import sweeps

        sweeps.every_ten_minutes()
        sweeps.sync_refunds()
        sweeps.run_daily_reconciliation()
        sweeps.poll_settlements()
