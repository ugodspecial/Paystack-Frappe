"""Saved cards for any payer, settlement facts and fee linkage, gateway-level reconciliation."""

import frappe

from frappe_paystack.core.constants import AUTHORIZATION, PAID, PAYMENT_LOG, SETTLEMENT
from frappe_paystack.tests.support.base import LEARNER, PaystackTestCase, make_setting


class TestSavedCards(PaystackTestCase):
    def test_cards_are_stored_only_when_the_account_opts_in(self):
        session_name = self.session_of(self.payment_url(user=LEARNER))
        self.start(session_name)
        self.pay_and_notify(session_name)
        self.assertFalse(frappe.db.exists(AUTHORIZATION, {"user": LEARNER}))

    def test_card_is_stored_for_the_logged_in_payer_and_charged_again(self):
        from frappe_paystack.core.api import charge_saved_authorization
        from frappe_paystack.core.authorizations import usable_authorizations

        make_setting(self.setting.name, "sk_test_core_secret", "pk_test_core_public", save_card_authorizations=1)
        session_name = self.session_of(self.payment_url(user=LEARNER))
        self.start(session_name)
        self.pay_and_notify(session_name)
        cards = usable_authorizations(user=LEARNER)
        self.assertEqual(len(cards), 1)
        card = frappe.get_doc(AUTHORIZATION, cards[0].name)
        self.assertEqual(card.card_label, "Visa •••• 4081")
        self.assertEqual(card.get_password("authorization_code"), f"AUTH_{session_name}")
        self.assertEqual(self.session(session_name).customer_authorization, card.name)

        todo = self.make_todo()
        charged = charge_saved_authorization(card.name, "ToDo", todo.name, 2500, "NGN", payer_email=LEARNER,
                                             payer_user=None)
        self.assertEqual(charged.status, PAID)
        charge = [c for c in self.fake.calls if c["path"] == "/transaction/charge_authorization"][-1]
        self.assertEqual(charge["json"]["authorization_code"], f"AUTH_{session_name}")
        self.assertEqual(charge["json"]["amount"], 250000)
        self.assertEqual(self.recorder.calls[-1]["doc"], todo.name)

    def test_declined_saved_card(self):
        from frappe_paystack.core.api import charge_saved_authorization

        make_setting(self.setting.name, "sk_test_core_secret", "pk_test_core_public", save_card_authorizations=1)
        session_name = self.session_of(self.payment_url(user=LEARNER))
        self.start(session_name)
        self.pay_and_notify(session_name)
        card = frappe.get_all(AUTHORIZATION, pluck="name")[0]
        self.fake.charge_status = "failed"
        self.assertRaises(frappe.ValidationError, charge_saved_authorization, card, "ToDo", self.make_todo().name, 2500, "NGN")


class TestSettlements(PaystackTestCase):
    def test_settlement_webhook_records_the_payout_and_links_fees(self):
        session_name = self.session_of(self.payment_url(amount=5000))
        self.start(session_name)
        tx = self.pay_and_notify(session_name, fees=7500)
        payout = self.fake.add_settlement("88001", [tx], fees=7500)
        self.deliver("settlement.success", payout)
        settlement = frappe.get_doc(SETTLEMENT, "88001")
        self.assertEqual(settlement.gross_amount, 5000)
        self.assertEqual(settlement.total_fees, 75)
        self.assertEqual(settlement.net_amount, 4925)
        self.assertEqual(settlement.transactions_linked, 1)
        session = self.session(session_name)
        self.assertEqual(session.settlement, "88001")
        self.assertEqual(session.paystack_fee, 75)

    def test_settlements_are_polled(self):
        from frappe_paystack.core.sweeps import poll_settlements

        session_name = self.session_of(self.payment_url())
        self.start(session_name)
        tx = self.pay_and_notify(session_name)
        self.fake.add_settlement("88002", [tx], fees=150)
        self.assertIn("88002", poll_settlements())
        self.assertEqual(self.session(session_name).settlement, "88002")


class TestReconciliation(PaystackTestCase):
    def test_reconciled_and_mismatched(self):
        from frappe_paystack.core.reconciliation import ReconciliationEngine

        paid = self.session_of(self.payment_url(amount=5000))
        self.start(paid)
        self.pay_and_notify(paid)
        pending = self.session_of(self.payment_url(amount=6000))
        self.start(pending)
        self.fake.pay(pending)  # captured on Paystack, never reported here

        engine = ReconciliationEngine(self.setting.name)
        self.assertEqual(engine.reconcile_payment(paid)["status"], "Reconciled")
        self.assertEqual(engine.reconcile_payment(pending)["status"], "Mismatch")

    def test_reconciliation_api(self):
        from frappe_paystack.utils.reconciliation_api import get_reconciliation_stats, mark_manual_override, run_reconciliation

        paid = self.session_of(self.payment_url(amount=5000))
        self.start(paid)
        self.pay_and_notify(paid)
        result = run_reconciliation()
        self.assertEqual(result["reconciled"], 1)
        self.assertEqual(get_reconciliation_stats()["Reconciled"], 1)
        self.assertEqual(mark_manual_override(paid, "Checked by hand")["status"], "Manual Override")
        self.assertTrue(frappe.db.exists(PAYMENT_LOG, paid))
