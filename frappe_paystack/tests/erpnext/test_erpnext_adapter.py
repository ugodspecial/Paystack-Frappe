"""
ERPNext features through the adapter: Payment Requests (and Webshop), direct
invoice links with Payment Entry booking, reversal entries for refunds,
credit-note refunds, settlement Journal Entries, company routing, saved cards
per Customer, the portal, and activation/deactivation keeping data.
"""

import frappe
from frappe.utils import flt

from frappe_paystack.core.constants import PAID, PAYMENT_LOG, REFUND_LOG, SETTLEMENT
from frappe_paystack.tests.support.base import PaystackTestCase, requires

ERP_ACCOUNT = "Paystack ERPNext Test"
ERP_SECRET = "sk_test_erpnext_secret"
ERP_PUBLIC = "pk_test_erpnext_public"


@requires("erpnext")
class ERPNextTestCase(PaystackTestCase):
    webhook_secret = ERP_SECRET

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from frappe_paystack.tests.support import erpnext_fixtures as fixtures

        fixtures.before_tests()
        cls.company = fixtures.company()
        cls.customer = fixtures.ensure_customer()
        fixtures.ensure_item()
        frappe.db.commit()

    def setUp(self):
        super().setUp()
        from frappe_paystack.tests.support.erpnext_fixtures import erpnext_setting

        self.erp = erpnext_setting(ERP_ACCOUNT, ERP_SECRET, ERP_PUBLIC, self.company)

    def invoice(self, rate=5000, submit=True, company=None):
        from frappe_paystack.tests.support.erpnext_fixtures import make_sales_invoice

        return make_sales_invoice(company or self.company, self.customer, rate=rate, submit=submit)

    def direct_session(self, invoice):
        from frappe_paystack.core.api import create_session

        session = create_session("Sales Invoice", invoice.name, invoice.grand_total, "NGN",
                                 payer_email="paystack-customer@example.com")
        frappe.db.commit()
        return session.name

    def pay(self, session_name):
        self.start(session_name)
        return self.pay_and_notify(session_name)


class TestActivation(ERPNextTestCase):
    def test_adapter_is_active(self):
        from frappe_paystack.integrations import erpnext

        self.assertTrue(erpnext.is_available())
        for doctype, field in ((PAYMENT_LOG, "payment_entry"), (PAYMENT_LOG, "booking_status"),
                               ("Paystack Gateway Setting", "suspense_account"), (REFUND_LOG, "reversal_payment_entry"),
                               (SETTLEMENT, "journal_entry")):
            self.assertTrue(frappe.get_meta(doctype).has_field(field), f"{doctype}.{field}")
        self.assertTrue(frappe.db.exists("Mode of Payment", "Paystack"))
        self.assertTrue(frappe.db.exists("Print Format", "Paystack Invoice with Payment Link"))
        self.assertTrue(frappe.db.exists("Custom DocPerm", {"parent": PAYMENT_LOG, "role": "Accounts Manager"}))
        self.assertTrue(frappe.db.exists("Payment Gateway Account", {"payment_gateway": "Paystack", "company": self.company}))

    def test_account_rules(self):
        doc = frappe.get_doc("Paystack Gateway Setting", ERP_ACCOUNT)
        doc.currency = "USD"
        self.assertRaises(frappe.ValidationError, doc.save)

    def test_deactivate_keeps_data_and_activate_restores_it(self):
        from frappe_paystack.integrations import erpnext

        session_name = self.direct_session(self.invoice())
        frappe.db.set_value(PAYMENT_LOG, session_name, "company", self.company)
        frappe.db.commit()
        try:
            erpnext.deactivate()
            self.assertFalse(frappe.get_meta(PAYMENT_LOG).has_field("company"))
            stored = frappe.db.sql("select company from `tabPaystack Payment Log` where name = %s", session_name)[0][0]
            self.assertEqual(stored, self.company)
        finally:
            erpnext.activate()
        self.assertEqual(frappe.db.get_value(PAYMENT_LOG, session_name, "company"), self.company)


class TestPayments(ERPNextTestCase):
    def test_direct_invoice_payment_books_a_payment_entry(self):
        invoice = self.invoice()
        session_name = self.direct_session(invoice)
        session = self.session(session_name)
        self.assertEqual(session.gateway_setting, ERP_ACCOUNT)  # routed by company
        self.assertEqual(session.company, self.company)
        self.pay(session_name)
        session = self.session(session_name)
        self.assertEqual(session.status, PAID)
        self.assertEqual(session.booking_status, "Booked")
        pe = frappe.get_doc("Payment Entry", session.payment_entry)
        self.assertEqual(pe.docstatus, 1)
        self.assertEqual(pe.paid_to, self.erp.suspense_account)
        self.assertEqual(flt(pe.paid_amount), 5000)
        self.assertEqual(flt(frappe.db.get_value("Sales Invoice", invoice.name, "outstanding_amount")), 0)

    def test_payment_link_through_a_payment_request(self):
        from frappe_paystack.api import create_payment_link

        invoice = self.invoice()
        url = create_payment_link("Sales Invoice", invoice.name)
        session_name = self.session_of(url)
        session = self.session(session_name)
        self.assertEqual(session.linked_doctype, "Payment Request")
        self.assertEqual(session.payment_request, session.linked_docname)
        self.pay(session_name)
        session = self.session(session_name)
        request = frappe.get_doc("Payment Request", session.payment_request)
        self.assertEqual(request.status, "Paid")
        self.assertEqual(session.booking_status, "Booked")
        self.assertEqual(session.notified_as, "Administrator")
        self.assertEqual(frappe.db.count("Payment Entry Reference", {"payment_request": request.name, "docstatus": 1}) or
                         frappe.db.count("Payment Entry", {"reference_no": request.name, "docstatus": 1}), 1)

    @requires("webshop")
    def test_webshop_override_does_not_book_twice(self):
        """Webshop's on_payment_authorized calls set_as_paid() itself; the adapter must not repeat it."""
        from frappe_paystack.api import create_payment_link

        frappe.db.set_single_value("Webshop Settings", "enabled", 1)
        frappe.db.commit()
        self.addCleanup(frappe.db.set_single_value, "Webshop Settings", "enabled", 0)
        invoice = self.invoice()
        session_name = self.session_of(create_payment_link("Sales Invoice", invoice.name))
        self.pay(session_name)
        request = self.session(session_name).payment_request
        entries = set(frappe.get_all("Payment Entry Reference", filters={"payment_request": request, "docstatus": 1},
                                     pluck="parent"))
        entries |= set(frappe.get_all("Payment Entry", filters={"reference_no": request, "docstatus": 1}, pluck="name"))
        self.assertEqual(len(entries), 1)
        self.assertTrue(self.session(session_name).consumer_redirect)

    def test_company_routing(self):
        from frappe_paystack.tests.support.base import make_setting

        second = "Paystack Routing Co"
        if not frappe.db.exists("Company", second):
            frappe.get_doc({"doctype": "Company", "company_name": second, "abbr": "PRC", "default_currency": "NGN",
                            "country": "Nigeria", "create_chart_of_accounts_based_on": "Standard Template",
                            "chart_of_accounts": "Standard"}).insert(ignore_permissions=True)
            frappe.db.commit()
        other = make_setting("Paystack Routing Account", "sk_test_routing", "pk_test_routing", company=second)
        self.addCleanup(frappe.db.set_value, "Paystack Gateway Setting", other.name, "enabled", 0)
        invoice = self.invoice(company=second)
        session = self.session(self.direct_session(invoice))
        self.assertEqual(session.gateway_setting, other.name)
        self.assertEqual(session.company, second)


class TestRefundsAndSettlements(ERPNextTestCase):
    def paid_invoice_session(self):
        invoice = self.invoice()
        session_name = self.direct_session(invoice)
        tx = self.pay(session_name)
        return invoice, session_name, tx

    def test_processed_refund_books_a_reversal_entry(self):
        from frappe_paystack.core.refunds import refund_payment

        _invoice, session_name, _tx = self.paid_invoice_session()
        refund = refund_payment(session_name, amount=2000, reason="Partial refund")
        self.fake.set_refund_status(refund.refund_reference, "processed")
        self.deliver("refund.processed", {"id": int(refund.refund_reference), "status": "processed", "amount": 200000,
                                          "currency": "NGN", "transaction_reference": session_name})
        refund = frappe.get_doc(REFUND_LOG, refund.name)
        self.assertEqual(refund.status, "Processed")
        self.assertEqual(refund.company, self.company)
        reversal = frappe.get_doc("Payment Entry", refund.reversal_payment_entry)
        self.assertEqual(reversal.docstatus, 1)
        self.assertEqual(reversal.payment_type, "Pay")
        self.assertEqual(flt(reversal.paid_amount), 2000)

    def test_credit_note_refunds_through_paystack(self):
        from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return

        frappe.db.set_value("Paystack Gateway Setting", ERP_ACCOUNT, "auto_refund_on_credit_note", 1)
        frappe.db.commit()
        invoice, session_name, _tx = self.paid_invoice_session()
        credit_note = make_sales_return(invoice.name)
        credit_note.insert(ignore_permissions=True)
        credit_note.submit()
        frappe.db.commit()
        refund = frappe.get_all(REFUND_LOG, filters={"payment_log": session_name}, fields=["refund_amount", "linked_docname"])
        self.assertEqual(len(refund), 1)
        self.assertEqual(flt(refund[0].refund_amount), 5000)
        self.assertEqual(refund[0].linked_docname, credit_note.name)

    def test_settlement_posts_a_journal_entry(self):
        _invoice, session_name, tx = self.paid_invoice_session()
        payout = self.fake.add_settlement("77001", [tx], fees=7500)
        self.deliver("settlement.success", payout)
        settlement = frappe.get_doc(SETTLEMENT, "77001")
        self.assertEqual(settlement.company, self.company)
        self.assertEqual(settlement.booking_status, "Booked", settlement.errors)
        entry = frappe.get_doc("Journal Entry", settlement.journal_entry)
        rows = {row.account: (flt(row.debit_in_account_currency), flt(row.credit_in_account_currency)) for row in entry.accounts}
        self.assertEqual(rows[self.erp.settlement_bank_account], (4925, 0))
        self.assertEqual(rows[self.erp.paystack_fee_account], (75, 0))
        self.assertEqual(rows[self.erp.suspense_account], (0, 5000))
        self.assertEqual(self.session(session_name).settlement, "77001")


class TestCustomers(ERPNextTestCase):
    def test_saved_card_per_customer(self):
        from frappe_paystack.api import charge_saved_card, saved_cards

        frappe.db.set_value("Paystack Gateway Setting", ERP_ACCOUNT, "save_card_authorizations", 1)
        frappe.db.commit()
        session_name = self.direct_session(self.invoice())
        self.pay(session_name)
        cards = saved_cards(self.customer)
        self.assertEqual(len(cards), 1)
        charged = charge_saved_card("Sales Invoice", self.invoice(rate=3000).name, cards[0].name)
        self.assertEqual(frappe.db.get_value(PAYMENT_LOG, charged, "status"), PAID)

    def test_portal_payment_state(self):
        from frappe_paystack.integrations.erpnext.portal import payment_state

        invoice = self.invoice()
        self.assertEqual(payment_state("Sales Invoice", invoice.name), "open")
        self.pay(self.direct_session(invoice))
        self.assertEqual(payment_state("Sales Invoice", invoice.name), "settled")


class TestWithoutERPNextDocuments(ERPNextTestCase):
    def test_generic_consumers_still_work_on_an_erpnext_site(self):
        """A non-ERPNext reference (here ToDo) is untouched by the adapter's booking."""
        session_name = self.session_of(self.payment_url())
        self.start(session_name)
        tx = self.fake.pay(session_name)
        self.deliver("charge.success", tx, secret="sk_test_core_secret")
        session = self.session(session_name)
        self.assertEqual(session.status, PAID)
        self.assertEqual(session.booking_status, "Not Required")
        self.assertEqual(len(self.recorder.calls), 1)

