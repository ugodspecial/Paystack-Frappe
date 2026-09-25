"""
Education ("School"): a Student Applicant paying its application fee through
the generic Payments contract, anonymously (as from a public Web Form).

Education requires ERPNext, so this runs only in the ERPNext environment; the
payment path itself uses nothing but the core.
"""

import frappe

from frappe_paystack.core.constants import PAID
from frappe_paystack.tests.support.base import PaystackTestCase, as_user, requires


@requires("education")
class TestStudentApplicant(PaystackTestCase):
    def make_applicant(self):
        applicant = frappe.new_doc("Student Applicant")
        applicant.update({"first_name": "Paystack", "last_name": "Applicant", "student_email_id": "applicant@example.com"})
        # The applicant's own admission rules (register, fee term, ...) are not what is tested here.
        applicant.flags.ignore_validate = True
        applicant.flags.ignore_mandatory = True
        applicant.flags.ignore_links = True
        applicant.flags.ignore_permissions = True
        applicant.insert()
        frappe.db.commit()
        self.addCleanup(frappe.delete_doc, "Student Applicant", applicant.name, force=True)
        return applicant

    def test_application_fee_marks_the_applicant_paid(self):
        applicant = self.make_applicant()
        with as_user("Guest"):
            url = self.controller().get_payment_url(
                amount=10000,
                currency="NGN",
                title="Application fee",
                description="Application fee",
                reference_doctype="Student Applicant",
                reference_docname=applicant.name,
                payer_email="Guest",
                payer_name="Paystack Applicant",
                order_id=applicant.name,
            )
        frappe.db.commit()
        session_name = self.session_of(url)
        self.start(session_name, email="applicant@example.com", user="Guest")
        self.pay_and_notify(session_name)
        self.assertEqual(self.session(session_name).status, PAID)
        self.assertEqual(self.session(session_name).notification_status, "Notified")
        self.assertEqual(frappe.db.get_value("Student Applicant", applicant.name, "paid"), 1)
