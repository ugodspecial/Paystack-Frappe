"""
LMS end to end, without ERPNext: buy a course or a batch through LMS's own
get_payment_link, pay with Paystack, and check the learner (never Guest or
the administrator) is enrolled exactly once.
"""

import frappe
from frappe.utils import add_days, nowdate

from frappe_paystack.core.constants import PAID, PAYMENT_LOG
from frappe_paystack.tests.support.base import ADMIN_USER, PaystackTestCase, as_user, ensure_user, requires

STUDENT = "paystack-lms-student@example.com"
SOURCE = "Paystack Test Source"
ADDRESS = {
    # "How did you hear about us": required on LMS Payment.
    "source": SOURCE,
    "billing_name": "Paystack Student",
    "address_line1": "1 Marina Road",
    "city": "Lagos",
    "state": "Lagos",
    "country": "Nigeria",
    "phone": "+2348000000000",
}


@requires("lms")
class TestLMSPayments(PaystackTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_user(STUDENT, "Paystack Student", roles=[])
        user = frappe.get_doc("User", STUDENT)
        if "LMS Student" not in [row.role for row in user.roles]:
            user.add_roles("LMS Student")
        if not frappe.db.exists("LMS Source", SOURCE):
            frappe.get_doc({"doctype": "LMS Source", "source": SOURCE}).insert(ignore_permissions=True)
        frappe.db.commit()

    def setUp(self):
        super().setUp()
        frappe.db.set_single_value("LMS Settings", "payment_gateway", "Paystack")
        frappe.db.commit()
        self.course = self.make_course()
        self.addCleanup(self.cleanup_lms)

    def cleanup_lms(self):
        frappe.set_user("Administrator")
        for doctype in ("LMS Enrollment", "LMS Batch Enrollment", "LMS Payment"):
            frappe.db.delete(doctype, {"member": STUDENT})
        frappe.db.delete("Address", {"email_id": STUDENT})
        frappe.db.commit()

    # ------------------------------------------------------------------ fixtures

    def make_course(self, price=5000):
        doc = frappe.get_doc(
            {
                "doctype": "LMS Course",
                "title": f"Paystack Course {frappe.generate_hash(length=6)}",
                "short_introduction": "Paid course",
                "description": "Paid course paid with Paystack",
                "published": 1,
                "paid_course": 1,
                "course_price": price,
                "currency": "NGN",
                "instructors": [{"instructor": "Administrator"}],
            }
        )
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return doc.name

    def make_batch(self, price=3000):
        doc = frappe.get_doc(
            {
                "doctype": "LMS Batch",
                "title": f"Paystack Batch {frappe.generate_hash(length=6)}",
                "description": "Paid batch",
                "batch_details": "Paid batch paid with Paystack",
                "start_date": add_days(nowdate(), 7),
                "end_date": add_days(nowdate(), 37),
                "start_time": "09:00:00",
                "end_time": "11:00:00",
                "timezone": "Africa/Lagos",
                "published": 1,
                "paid_batch": 1,
                "amount": price,
                "currency": "NGN",
                "instructors": [{"instructor": "Administrator"}],
            }
        )
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return doc.name

    def buy(self, doctype, docname):
        from lms.lms.payments import get_payment_link

        with as_user(STUDENT):
            url = get_payment_link(doctype, docname, ADDRESS, 0, None, "Nigeria")
        frappe.db.commit()
        self.assertIn("/paystack-checkout/", url, url)
        return self.session_of(url)

    def pay(self, session_name):
        self.start(session_name, user=STUDENT)
        self.pay_and_notify(session_name)  # delivered as Guest, like Paystack

    def lms_payment(self, session_name):
        data = frappe.parse_json(self.session(session_name).request_data)
        return frappe.get_doc("LMS Payment", data["payment"])

    # ------------------------------------------------------------------ tests

    def test_course_purchase_enrols_the_learner(self):
        session_name = self.buy("LMS Course", self.course)
        session = self.session(session_name)
        self.assertEqual((session.linked_doctype, session.linked_docname), ("LMS Course", self.course))
        self.assertEqual(session.run_as_user, STUDENT)
        self.assertEqual(session.amount, 5000)

        self.pay(session_name)
        payment = self.lms_payment(session_name)
        self.assertEqual(self.session(session_name).status, PAID)
        self.assertEqual(self.session(session_name).notification_status, "Notified")
        self.assertEqual(payment.payment_received, 1)
        self.assertEqual(payment.payment_id, session_name)
        self.assertEqual(frappe.db.count("LMS Enrollment", {"course": self.course, "member": STUDENT}), 1)
        for other in ("Guest", "Administrator"):
            self.assertFalse(frappe.db.exists("LMS Enrollment", {"course": self.course, "member": other}))

    def test_duplicate_webhook_enrols_once(self):
        session_name = self.buy("LMS Course", self.course)
        self.start(session_name, user=STUDENT)
        tx = self.fake.pay(session_name)
        self.deliver("charge.success", tx)
        self.deliver("charge.success", tx)
        self.assertEqual(frappe.db.count("LMS Enrollment", {"course": self.course, "member": STUDENT}), 1)

    def test_batch_purchase_enrols_the_learner(self):
        batch = self.make_batch()
        session_name = self.buy("LMS Batch", batch)
        self.pay(session_name)
        self.assertEqual(self.lms_payment(session_name).payment_received, 1)
        self.assertEqual(frappe.db.count("LMS Batch Enrollment", {"batch": batch, "member": STUDENT}), 1)

    def test_admin_verifying_from_the_desk_enrols_the_learner_not_the_admin(self):
        session_name = self.buy("LMS Course", self.course)
        self.start(session_name, user=STUDENT)
        self.fake.pay(session_name)  # the webhook never arrives
        with as_user(ADMIN_USER):
            frappe.get_doc(PAYMENT_LOG, session_name).verify_with_paystack()
        self.assertTrue(frappe.db.exists("LMS Enrollment", {"course": self.course, "member": STUDENT}))
        self.assertFalse(frappe.db.exists("LMS Enrollment", {"course": self.course, "member": ADMIN_USER}))

    def test_learner_enrolled_by_an_admin_before_payment_completes(self):
        """LMS course enrolment is idempotent: the payment is recorded, no duplicate enrolment."""
        session_name = self.buy("LMS Course", self.course)
        frappe.get_doc({"doctype": "LMS Enrollment", "course": self.course, "member": STUDENT}).insert(ignore_permissions=True)
        frappe.db.commit()
        self.pay(session_name)
        self.assertEqual(self.session(session_name).notification_status, "Notified")
        self.assertEqual(self.lms_payment(session_name).payment_received, 1)
        self.assertEqual(frappe.db.count("LMS Enrollment", {"course": self.course, "member": STUDENT}), 1)

    def test_batch_learner_enrolled_by_an_admin_is_flagged_not_lost(self):
        """
        LMS batch enrolment refuses a duplicate ("Member already enrolled").
        The payment stays captured, LMS's partial writes are rolled back, and
        the notification is recorded for an administrator to resolve.
        """
        batch = self.make_batch()
        session_name = self.buy("LMS Batch", batch)
        frappe.get_doc({"doctype": "LMS Batch Enrollment", "batch": batch, "member": STUDENT}).insert(ignore_permissions=True)
        frappe.db.commit()
        self.pay(session_name)
        session = self.session(session_name)
        self.assertEqual(session.status, PAID)
        self.assertEqual(session.notification_status, "Failed")
        self.assertIn("already enrolled", (session.notification_error or "").lower())
        self.assertEqual(frappe.db.count("LMS Batch Enrollment", {"batch": batch, "member": STUDENT}), 1)
        with as_user(ADMIN_USER):
            frappe.get_doc(PAYMENT_LOG, session_name).mark_notification_resolved("Learner was enrolled by hand")
        self.assertEqual(self.session(session_name).notification_status, "Not Required")

    def test_no_second_charge_once_enrolled(self):
        from lms.lms.payments import get_payment_link

        session_name = self.buy("LMS Course", self.course)
        self.pay(session_name)
        with as_user(STUDENT):
            redirect = get_payment_link("LMS Course", self.course, ADDRESS, 0, None, "Nigeria")
        self.assertNotIn("/paystack-checkout/", redirect)
