"""
Any Frappe app, through Frappe Payments' own "Accept Payment" Web Forms: an
anonymous visitor submits a Web Form, Payments asks the Paystack controller for
a checkout URL, and the document is told when the payment completes.
"""

import frappe

from frappe_paystack.core.constants import PAID
from frappe_paystack.tests.support.base import PaystackTestCase, as_user


class TestPaymentsWebForm(PaystackTestCase):
    def make_web_form(self):
        name = "paystack-test-form"
        if frappe.db.exists("Web Form", {"route": name}):
            frappe.delete_doc("Web Form", frappe.db.get_value("Web Form", {"route": name}), force=True)
        form = frappe.get_doc(
            {
                "doctype": "Web Form",
                "title": "Paystack Test Form",
                "route": name,
                "doc_type": "ToDo",
                "module": "Frappe Paystack",
                "is_standard": 0,
                "published": 1,
                "login_required": 0,
                "accept_payment": 1,
                "payment_gateway": "Paystack",
                "amount": 5000,
                "currency": "NGN",
                "success_url": "/thanks",
                "web_form_fields": [{"fieldname": "description", "fieldtype": "Text", "label": "Description"}],
            }
        )
        form.flags.ignore_permissions = True
        form.insert()
        frappe.db.commit()
        self.addCleanup(frappe.delete_doc, "Web Form", form.name, force=True)
        return frappe.get_doc("Web Form", form.name)

    def test_anonymous_web_form_payment(self):
        # Payments attaches this method to Web Form with extend_doctype_class, which
        # Frappe provides from version-16; calling it directly runs the same code
        # on every version.
        from payments.overrides.payment_webform import PaymentWebForm

        form = self.make_web_form()
        todo = self.make_todo()
        with as_user("Guest"):
            url = PaymentWebForm.get_payment_gateway_url(form, todo)
        frappe.db.commit()
        session_name = self.session_of(url)
        session = self.session(session_name)
        self.assertEqual(session.run_as_user, "Guest")
        self.assertEqual(session.order_id, todo.name)
        self.assertFalse(session.payer_email)  # "Guest" is not an email

        self.start(session_name, email="visitor@example.com", user="Guest")
        self.pay_and_notify(session_name)
        self.assertEqual(self.session(session_name).status, PAID)
        call = self.recorder.calls[-1]
        self.assertEqual(call["doc"], todo.name)
        self.assertEqual(call["user"], "Guest")
        self.assertEqual(call["data"]["order_id"], todo.name)
