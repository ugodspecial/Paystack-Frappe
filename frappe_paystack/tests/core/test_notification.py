"""
Consumer notification (decision D5): the callback runs as the user who started
the payment, whichever path completes it, exactly once, isolated from failures.
"""

import frappe

from frappe_paystack.core.constants import PAID, PAYMENT_LOG
from frappe_paystack.tests.support.base import ADMIN_USER, LEARNER, OTHER_USER, PaystackTestCase, as_user


class TestRunAsInitiator(PaystackTestCase):
    def test_webhook_completion_runs_as_the_initiator(self):
        session_name = self.session_of(self.payment_url(user=LEARNER))
        self.start(session_name, user=LEARNER)
        self.pay_and_notify(session_name)  # delivered as Guest
        call = self.recorder.calls[0]
        self.assertEqual(call["user"], LEARNER)
        self.assertEqual(self.session(session_name).notified_as, LEARNER)

    def test_admin_verifying_from_the_desk_does_not_become_the_payer(self):
        """An administrator clicking "Verify with Paystack" must not be enrolled/credited."""
        session_name = self.session_of(self.payment_url(user=LEARNER))
        self.start(session_name, user=LEARNER)
        self.fake.pay(session_name)
        with as_user(ADMIN_USER):
            result = frappe.get_doc(PAYMENT_LOG, session_name).verify_with_paystack()
        self.assertEqual(result["status"], PAID)
        self.assertEqual(self.recorder.calls[0]["user"], LEARNER)
        session = self.session(session_name)
        self.assertEqual(session.completed_by, ADMIN_USER)
        self.assertEqual(session.completed_via, "Manual")

    def test_sweep_completion_runs_as_the_initiator(self):
        from frappe_paystack.core.sweeps import verify_open_sessions

        session_name = self.session_of(self.payment_url(user=LEARNER))
        self.start(session_name, user=LEARNER)
        self.fake.pay(session_name)
        frappe.db.sql("update `tabPaystack Payment Log` set modified = %s where name = %s",
                      (frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-30), session_name))
        frappe.db.commit()
        verify_open_sessions()
        self.assertEqual(self.session(session_name).status, PAID)
        self.assertEqual(self.recorder.calls[0]["user"], LEARNER)

    def test_browser_callback_runs_as_the_payer_and_returns_the_redirect(self):
        from frappe_paystack.core.lifecycle import verify

        self.recorder.redirect = "/courses/my-course"
        session_name = self.session_of(self.payment_url(user=LEARNER))
        self.start(session_name, user=LEARNER)
        self.fake.pay(session_name)
        with as_user(LEARNER):
            result = verify(session_name, reference=session_name)
        self.assertEqual(result["redirect"], "/courses/my-course")
        self.assertEqual(self.recorder.calls[0]["user"], LEARNER)

    def test_default_redirect_follows_the_payments_convention(self):
        from frappe_paystack.core.lifecycle import verify

        todo = self.make_todo()
        session_name = self.session_of(self.payment_url(todo, user=LEARNER))
        self.start(session_name)
        self.fake.pay(session_name)
        redirect = verify(session_name)["redirect"]
        self.assertIn("/payment-success?", redirect)
        self.assertIn(f"docname={todo.name}", redirect)
        self.assertIn("doctype=ToDo", redirect)

    def test_impersonation_is_recorded(self):
        frappe.set_user(LEARNER)
        frappe.session.data["impersonated_by"] = ADMIN_USER
        try:
            url = self.controller().get_payment_url(amount=5000, currency="NGN", reference_doctype="ToDo",
                                                    reference_docname=self.make_todo().name, payer_email=LEARNER)
        finally:
            frappe.session.data.pop("impersonated_by", None)
            frappe.set_user("Administrator")
        session = self.session(self.session_of(url))
        self.assertEqual(session.run_as_user, LEARNER)
        self.assertEqual(session.impersonated_by, ADMIN_USER)

    def test_on_behalf_of_requires_a_system_manager(self):
        from frappe_paystack.core.api import create_session

        todo = self.make_todo()
        with as_user(ADMIN_USER):
            session = create_session("ToDo", todo.name, 5000, "NGN", payer_email=LEARNER, payer_user=LEARNER)
        self.assertEqual(session.initiated_by, ADMIN_USER)
        self.assertEqual(session.run_as_user, LEARNER)
        with as_user(OTHER_USER):
            self.assertRaises(frappe.PermissionError, create_session, "ToDo", todo.name, 5000, "NGN", payer_user=LEARNER)
        with as_user(ADMIN_USER):
            self.assertRaises(frappe.PermissionError, create_session, "ToDo", todo.name, 6000, "NGN",
                              payer_user="Administrator")

    def test_payer_email_never_decides_who_the_callback_runs_as(self):
        """An anonymous payer typing an administrator's email gains nothing."""
        session_name = self.session_of(self.payment_url(user="Guest", payer_email=""))
        self.start(session_name, email="admin@example.com", user="Guest")
        self.pay_and_notify(session_name)
        self.assertEqual(self.recorder.calls[0]["user"], "Guest")


class TestNotificationContract(PaystackTestCase):
    def test_flags_data_carries_the_consumers_kwargs(self):
        session_name = self.session_of(self.payment_url(user=LEARNER, payment="LMS-PAY-9"))
        self.start(session_name)
        self.pay_and_notify(session_name)
        data = self.recorder.calls[0]["data"]
        self.assertEqual(data["payment"], "LMS-PAY-9")
        self.assertEqual(data["order_id"], session_name)
        self.assertEqual(data["payment_gateway"], "Paystack")
        self.assertEqual(data["reference_doctype"], "ToDo")
        self.assertEqual(data["payment_session"], session_name)
        self.assertEqual(data["amount_paid"], 5000)
        self.assertTrue(data["paystack_transaction_id"])

    def test_consumer_supplied_order_id_is_kept(self):
        session_name = self.session_of(self.payment_url(order_id="PR-0001"))
        self.start(session_name)
        self.pay_and_notify(session_name)
        self.assertEqual(self.recorder.calls[0]["data"]["order_id"], "PR-0001")

    def test_consumer_failure_is_rolled_back_recorded_and_retried(self):
        todo = self.make_todo()
        self.recorder.write_marker = True
        self.recorder.raise_error = frappe.ValidationError("Member already enrolled in this batch")
        session_name = self.session_of(self.payment_url(todo, user=LEARNER))
        self.start(session_name)
        self.pay_and_notify(session_name)
        session = self.session(session_name)
        # The payment stays captured; the consumer's partial writes are undone.
        self.assertEqual(session.status, PAID)
        self.assertEqual(session.notification_status, "Failed")
        self.assertIn("already enrolled", session.notification_error)
        self.assertEqual(frappe.db.get_value("ToDo", todo.name, "description"), "Paystack test order")

        self.recorder.raise_error = None
        session.retry_notification()
        session = self.session(session_name)
        self.assertEqual(session.notification_status, "Notified")
        self.assertEqual(frappe.db.get_value("ToDo", todo.name, "description"), f"paid:{session_name}")
        self.assertEqual(self.recorder.calls[-1]["user"], LEARNER)

    def test_persistent_failure_escalates_and_can_be_resolved(self):
        from frappe_paystack.core.constants import NOTIFY_MAX_ATTEMPTS
        from frappe_paystack.core.notify import notify_success

        self.recorder.raise_error = frappe.ValidationError("still failing")
        session_name = self.session_of(self.payment_url())
        self.start(session_name)
        self.pay_and_notify(session_name)
        for _attempt in range(NOTIFY_MAX_ATTEMPTS):
            notify_success(session_name, force=True)
        session = self.session(session_name)
        self.assertEqual(session.notification_status, "Needs Attention")
        session.mark_notification_resolved("Enrolled by hand")
        self.assertEqual(self.session(session_name).notification_status, "Not Required")
        notify_success(session_name, force=True)
        self.assertEqual(self.session(session_name).notification_status, "Not Required")

    def test_retry_sweep_respects_back_off(self):
        from frappe_paystack.core.sweeps import retry_notifications

        self.recorder.raise_error = frappe.ValidationError("temporary")
        session_name = self.session_of(self.payment_url())
        self.start(session_name)
        self.pay_and_notify(session_name)
        calls = len(self.recorder.calls)
        self.recorder.raise_error = None
        retry_notifications()  # still inside the back-off window
        self.assertEqual(len(self.recorder.calls), calls)
        frappe.db.set_value(PAYMENT_LOG, session_name, {"last_notification_attempt": "2020-01-01 00:00:00",
                                                        "modified": "2020-01-01 00:00:00"}, update_modified=False)
        frappe.db.commit()
        retry_notifications()
        self.assertEqual(self.session(session_name).notification_status, "Notified")

    def test_session_without_reference_needs_no_notification(self):
        from frappe_paystack.core.api import create_session
        from frappe_paystack.core.session import create_session as core_create

        session = core_create(self.controller(), {"amount": 5000, "currency": "NGN", "payer_email": LEARNER})
        self.start(session.name)
        self.pay_and_notify(session.name)
        self.assertEqual(self.session(session.name).notification_status, "Not Required")
        self.assertTrue(callable(create_session))
