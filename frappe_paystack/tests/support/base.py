"""
Base test case and fixtures that need only Frappe + Payments.

The consuming document in core tests is Frappe's own ToDo, given an
on_payment_authorized / on_payment_failed at test time: exactly what any app
does to consume Payments, and proof that no business DocType of our own (or
ERPNext) is needed.
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from typing import Any, Optional
from unittest.mock import patch

import frappe

from frappe_paystack.core.adapters import clear_adapter_cache
from frappe_paystack.core.constants import (
    AUTHORIZATION,
    GATEWAY_SETTING,
    INTEGRATION_REQUEST,
    PAYMENT_LOG,
    RECONCILIATION_LOG,
    REFUND_LOG,
    SETTLEMENT,
)
from frappe_paystack.tests.support.paystack_fake import FakePaystack, webhook

FRAPPE_MAJOR = int(frappe.__version__.split(".")[0])

if FRAPPE_MAJOR >= 16:
    from frappe.tests import IntegrationTestCase as FrappeBaseTestCase
else:
    from frappe.tests.utils import FrappeTestCase as FrappeBaseTestCase

SECRET = "sk_test_core_secret"
PUBLIC = "pk_test_core_public"
ACCOUNT = "Paystack Core Test"
LEARNER = "paystack-learner@example.com"
OTHER_USER = "paystack-other@example.com"
ADMIN_USER = "paystack-admin@example.com"


def installed(app: str) -> bool:
    return app in frappe.get_installed_apps()


def requires(app: str):
    return unittest.skipUnless(installed(app), f"{app} is not installed on this site")


class ConsumerRecorder:
    """Records every consumer callback: status, the user it ran as, and frappe.flags.data."""

    def __init__(self) -> None:
        self.calls: list = []
        self.failures: list = []
        self.redirect: Optional[str] = None
        self.raise_error: Optional[Exception] = None
        self.write_marker = False

    def on_payment_authorized(self, doc: Any, status: str = None):
        data = dict(frappe.flags.data or {})
        self.calls.append({"doc": doc.name, "status": status, "user": frappe.session.user, "data": data})
        if self.write_marker:
            frappe.db.set_value("ToDo", doc.name, "description", f"paid:{data.get('payment_session')}")
        if self.raise_error:
            raise self.raise_error
        return self.redirect

    def on_payment_failed(self, doc: Any, message: str = None):
        self.failures.append({"doc": doc.name, "message": message, "user": frappe.session.user})


class PaystackTestCase(FrappeBaseTestCase):
    """Fresh fake Paystack, a test account and a ToDo consumer per test."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        frappe.set_user("Administrator")
        ensure_user(LEARNER, "Paystack Learner", roles=[])
        ensure_user(OTHER_USER, "Paystack Other", roles=[])
        ensure_user(ADMIN_USER, "Paystack Admin", roles=["System Manager"])
        frappe.db.commit()

    def setUp(self) -> None:
        super().setUp()
        frappe.set_user("Administrator")
        clear_adapter_cache()
        self.fake = FakePaystack()
        self._fake_ctx = self.fake.installed()
        self._fake_ctx.__enter__()
        self.addCleanup(self._fake_ctx.__exit__, None, None, None)

        self.recorder = ConsumerRecorder()
        recorder = self.recorder
        todo_class = frappe.get_doc({"doctype": "ToDo", "description": "x"}).__class__

        def on_payment_authorized(doc, status=None):
            return recorder.on_payment_authorized(doc, status)

        def on_payment_failed(doc, message=None):
            return recorder.on_payment_failed(doc, message)

        for name, fn in (("on_payment_authorized", on_payment_authorized), ("on_payment_failed", on_payment_failed)):
            patcher = patch.object(todo_class, name, fn, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.setting = make_setting(ACCOUNT, SECRET, PUBLIC)
        self.addCleanup(frappe.set_user, "Administrator")

    def tearDown(self) -> None:
        frappe.set_user("Administrator")
        frappe.db.rollback()
        cleanup_payments()
        super().tearDown()

    # ------------------------------------------------------------------ helpers

    def controller(self, gateway: str = "Paystack"):
        from payments.utils import get_payment_gateway_controller

        return get_payment_gateway_controller(gateway)

    def make_todo(self, owner: Optional[str] = None) -> Any:
        doc = frappe.get_doc({"doctype": "ToDo", "description": "Paystack test order", "allocated_to": owner})
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return doc

    def payment_url(self, todo: Any = None, amount: Any = 5000, currency: str = "NGN", user: Optional[str] = None,
                    **extra) -> str:
        todo = todo or self.make_todo()
        kwargs = {
            "amount": amount,
            "currency": currency,
            "title": "Test order",
            "description": "Paying for a test order",
            "reference_doctype": "ToDo",
            "reference_docname": todo.name,
            "payer_email": user if user and user != "Guest" else extra.pop("payer_email", LEARNER),
            "payer_name": "Test Payer",
            "payment_gateway": "Paystack",
            "redirect_to": "/thank-you",
        }
        kwargs.update(extra)
        if user:
            frappe.set_user(user)
        try:
            url = self.controller().get_payment_url(**kwargs)
        finally:
            if user:
                frappe.set_user("Administrator")
        frappe.db.commit()
        return url

    @staticmethod
    def session_of(url: str) -> str:
        return url.rstrip("/").rsplit("/", 1)[-1]

    def session(self, name: str) -> Any:
        frappe.clear_document_cache(PAYMENT_LOG, name)
        return frappe.get_doc(PAYMENT_LOG, name)

    def start(self, session: str, email: Optional[str] = None, user: Optional[str] = None) -> dict:
        from frappe_paystack.core.lifecycle import start_checkout

        with as_user(user):
            return start_checkout(session, email=email)

    # The secret webhooks are signed with (the account the session belongs to).
    webhook_secret = SECRET

    def deliver(self, event: str, data: dict, secret: Optional[str] = None, ip: str = "52.31.139.75") -> dict:
        """Deliver a signed webhook as Paystack would (as Guest)."""
        from frappe_paystack.core.webhook import receive

        body, signature = webhook(event, data, secret or self.webhook_secret)
        with as_user("Guest"):
            return receive(body, signature, ip)

    def pay_and_notify(self, session: str, **kwargs) -> dict:
        reference = self.session(session).payment_reference
        tx = self.fake.pay(reference, **kwargs)
        self.deliver("charge.success", tx)
        return tx


@contextmanager
def as_user(user: Optional[str]):
    if not user:
        yield
        return
    previous = frappe.session.user
    frappe.set_user(user)
    try:
        yield
    finally:
        frappe.set_user(previous)


def ensure_user(email: str, first_name: str, roles: list) -> str:
    if not frappe.db.exists("User", email):
        user = frappe.get_doc({"doctype": "User", "email": email, "first_name": first_name, "send_welcome_email": 0,
                               "user_type": "System User" if roles else "Website User"})
        user.flags.ignore_permissions = True
        user.insert()
    user = frappe.get_doc("User", email)
    for role in roles:
        if role not in [r.role for r in user.roles]:
            user.add_roles(role)
    return email


def make_setting(name: str, secret: str, public: str, **values) -> Any:
    if frappe.db.exists(GATEWAY_SETTING, name):
        doc = frappe.get_doc(GATEWAY_SETTING, name)
    else:
        doc = frappe.new_doc(GATEWAY_SETTING)
        doc.gateway = name
    if frappe.get_meta(GATEWAY_SETTING).has_field("company") and "company" not in values:
        # With ERPNext, Link fields default to the user's default company; a core
        # test account belongs to no company.
        values["company"] = None
    doc.update({"enabled": 1, "test_mode": 1, "secret_key": secret, "public_key": public, "currency": "NGN",
                "checkout_mode": "Inline", "payment_link_validity_hours": 0, "save_card_authorizations": 0,
                "additional_currencies": "", "allowed_webhook_ips": "", "webhook_secret": None, **values})
    doc.flags.ignore_permissions = True
    doc.save()
    frappe.db.commit()
    return doc


def cleanup_payments() -> None:
    """Remove every record the payment tests create (committed by design of the flows)."""
    for doctype in (RECONCILIATION_LOG, REFUND_LOG, SETTLEMENT, AUTHORIZATION):
        frappe.db.delete(doctype)
    frappe.db.delete(PAYMENT_LOG)
    frappe.db.delete(INTEGRATION_REQUEST, {"integration_request_service": "Paystack"})
    frappe.db.delete("ToDo", {"description": ["like", "Paystack test order%"]})
    frappe.db.delete("ToDo", {"description": ["like", "paid:%"]})
    frappe.db.commit()
