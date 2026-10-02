"""
Payment links, callback URLs and redirects must survive localhost and proxies.

A site named `erp.localhost` is reached at `http://erp.localhost:8000`; nginx
forwards `Host` without the port. A link built from the bare site name sends
the payer to `http://erp.localhost`, which refuses the connection - the blank
"erp.localhost refused to connect" page instead of the checkout.

The host names below (`erp.localhost`, `learn.localhost`, `pay.example.test`)
are *fixtures*, not configuration: `browsing()` fabricates the incoming HTTP
request, so each test says "a browser at this address asked for a link" and
checks what came back. Nothing in the app stores or recognises them -
`site_hosts()` reads the site it is running on - and
`test_any_site_on_any_port_keeps_its_port` proves it, using the name of
whatever site the suite is run against.
"""

from contextlib import contextmanager

import frappe
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from paystack_frappe.core.constants import PAID
from paystack_frappe.core.session import checkout_url
from paystack_frappe.core.urls import (
    ORIGIN_FLAG,
    browser_path,
    normalise_origin,
    remember_origin,
    site_url,
)
from paystack_frappe.tests.support.base import LEARNER, PaystackTestCase

MISSING = object()


@contextmanager
def browsing(url: str, headers: dict = None, host_name: str = None):
    """
    Serve the block as if a browser at `url` had made the request.

    `host_name` is what site_config would pin; by default the site pins nothing,
    which is how `bench new-site` leaves it.
    """
    environ = EnvironBuilder(base_url=url, headers=list((headers or {}).items())).get_environ()
    previous_request = getattr(frappe.local, "request", None)
    previous_ip = getattr(frappe.local, "request_ip", None)
    previous_host = frappe.local.conf.get("host_name", MISSING)
    previous_alias = frappe.local.conf.get("hostname", MISSING)

    frappe.local.request = Request(environ)
    # Frappe fills this on a real request; the rate limiter on the guest
    # endpoints refuses to run without it.
    frappe.local.request_ip = "127.0.0.1"
    frappe.local.conf.pop("hostname", None)
    if host_name:
        frappe.local.conf["host_name"] = host_name
    else:
        frappe.local.conf.pop("host_name", None)
    try:
        yield
    finally:
        frappe.local.request = previous_request
        frappe.local.request_ip = previous_ip
        for key, value in (("host_name", previous_host), ("hostname", previous_alias)):
            if value is MISSING:
                frappe.local.conf.pop(key, None)
            else:
                frappe.local.conf[key] = value
        frappe.local.flags.pop(ORIGIN_FLAG, None)


class TestCheckoutUrls(PaystackTestCase):
    def tearDown(self) -> None:
        frappe.local.flags.pop(ORIGIN_FLAG, None)
        super().tearDown()

    def test_link_keeps_the_port_the_browser_is_on(self):
        with browsing("http://erp.localhost:8000/app/sales-invoice/SI-0001"):
            link = site_url("/paystack-checkout/X")
            self.assertEqual(link, "http://erp.localhost:8000/paystack-checkout/X")

    def test_forwarded_headers_are_followed(self):
        with browsing(
            "http://frontend/app",
            {"X-Forwarded-Host": "erp.localhost", "X-Forwarded-Proto": "https", "X-Forwarded-Port": "8443"},
        ):
            link = site_url("/paystack-checkout/X")
            self.assertEqual(link, "https://erp.localhost:8443/paystack-checkout/X")

    def test_a_live_site_keeps_https_and_its_default_port(self):
        """A domain behind TLS: no port in the link, and never a downgrade to http."""
        forwarded = {"X-Forwarded-Host": "pay.example.test", "X-Forwarded-Proto": "https"}
        with browsing("http://frontend/app", forwarded):
            self.assertEqual(
                site_url("/paystack-checkout/X"), "https://pay.example.test/paystack-checkout/X"
            )

    def test_the_browser_scheme_wins_when_a_proxy_forgets_to_forward_it(self):
        """TLS terminated upstream without X-Forwarded-Proto: the page knows it is https."""
        with browsing("http://pay.example.test/api/method/paystack_frappe.api.start_checkout"):
            remember_origin("https://pay.example.test")
            self.assertEqual(site_url("/x"), "https://pay.example.test/x")

    def test_reported_origin_restores_a_port_the_proxy_dropped(self):
        # nginx forwards `Host $host`, so the port never reaches Frappe.
        with browsing("http://erp.localhost/api/method/paystack_frappe.api.start_checkout"):
            self.assertEqual(site_url("/x"), "http://erp.localhost/x")
            remember_origin("http://erp.localhost:8080")
            self.assertEqual(site_url("/x"), "http://erp.localhost:8080/x")

    def test_any_site_on_any_port_keeps_its_port(self):
        """
        No host name is hard-coded anywhere: the link follows the site the
        request is for. Run on a fresh `shop.example.org` or `mysite.local`
        behind :9017 and the link is that site on :9017.
        """
        site = frappe.local.site  # whatever site this suite is run against
        for port in ("8080", "9017", "3000"):
            with browsing(f"http://{site}/billing", {"Origin": f"http://{site}:{port}"}):
                self.assertEqual(site_url("/x"), f"http://{site}:{port}/x")

    def test_the_origin_header_restores_a_port_the_proxy_dropped(self):
        """
        ERPNext and LMS call get_payment_url() themselves, with no `origin`
        argument: the payer's port only survives in the browser's own headers.
        """
        with browsing(
            "http://learn.localhost/api/method/lms.lms.payments.get_payment_link",
            {"Origin": "http://learn.localhost:8080"},
        ):
            self.assertEqual(site_url("/x"), "http://learn.localhost:8080/x")

    def test_the_referer_restores_the_port_on_a_plain_page_load(self):
        with browsing(
            "http://learn.localhost/billing",
            {"Referer": "http://learn.localhost:8080/courses/python/checkout"},
        ):
            self.assertEqual(site_url("/x"), "http://learn.localhost:8080/x")

    def test_a_reported_origin_never_overrides_a_port_that_did_arrive(self):
        with browsing("http://erp.localhost:8000/app", {"Origin": "http://erp.localhost:9999"}):
            self.assertEqual(site_url("/x"), "http://erp.localhost:8000/x")

    def test_a_reported_origin_on_another_host_is_ignored(self):
        with browsing("http://erp.localhost/app", {"Origin": "https://phishing.example.com:8080"}):
            self.assertEqual(site_url("/x"), "http://erp.localhost/x")
        with browsing(
            "http://erp.localhost/app", {"Referer": "http://user:pass@erp.localhost:8080/x"}
        ):
            self.assertEqual(site_url("/x"), "http://erp.localhost/x")

    def test_the_origin_header_keeps_https_when_the_proxy_forgets_it(self):
        with browsing("http://pay.example.test/app", {"Origin": "https://pay.example.test"}):
            self.assertEqual(site_url("/x"), "https://pay.example.test/x")

    def test_an_https_request_is_never_downgraded_by_the_origin_header(self):
        with browsing(
            "http://frontend/app",
            {
                "X-Forwarded-Host": "pay.example.test",
                "X-Forwarded-Proto": "https",
                "Origin": "http://pay.example.test",
            },
        ):
            self.assertEqual(site_url("/x"), "https://pay.example.test/x")

    def test_a_pinned_host_name_missing_the_port_is_corrected_by_the_header(self):
        with browsing(
            "http://learn.localhost/billing",
            {"Origin": "http://learn.localhost:8080"},
            host_name="http://learn.localhost",
        ):
            self.assertEqual(site_url("/x"), "http://learn.localhost:8080/x")

    def test_a_consumer_payment_url_keeps_the_browser_port(self):
        """What ERPNext's Payment Request and LMS get back from get_payment_url."""
        with browsing(
            "http://learn.localhost/api/method/run_doc_method",
            {"Origin": "http://learn.localhost:8080"},
        ):
            url = self.payment_url()
            self.assertTrue(url.startswith("http://learn.localhost:8080/paystack-checkout/"), url)

    def test_an_origin_on_another_host_is_refused(self):
        with browsing("http://erp.localhost:8000/app"):
            self.assertIsNone(normalise_origin("https://phishing.example.com"))
            self.assertIsNone(normalise_origin("http://user:pass@erp.localhost:8000"))
            self.assertIsNone(normalise_origin("javascript:alert(1)"))
            remember_origin("https://phishing.example.com")
            # The forged origin is ignored: the link still points at this site.
            self.assertEqual(site_url("/x"), "http://erp.localhost:8000/x")

    def test_an_origin_of_this_site_is_accepted(self):
        with browsing("http://erp.localhost:8000/app"):
            self.assertEqual(normalise_origin("http://erp.localhost:8000"), "http://erp.localhost:8000")
            self.assertEqual(normalise_origin("erp.localhost:9000"), "http://erp.localhost:9000")

    def test_a_pinned_host_name_stays_canonical_for_another_host(self):
        """An administrator who published the site keeps the published address."""
        with browsing("http://192.168.1.10:8000/app", host_name="https://pay.example.test"):
            remember_origin("http://192.168.1.10:8000")
            self.assertTrue(site_url("/x").startswith("https://pay.example.test"))

    def test_a_pinned_host_name_missing_the_port_is_corrected(self):
        """Same host, so the browser knows the port the configuration left out."""
        with browsing("http://erp.localhost/app", host_name="http://erp.localhost"):
            remember_origin("http://erp.localhost:8000")
            self.assertEqual(site_url("/x"), "http://erp.localhost:8000/x")

    def test_checkout_link_of_a_session_follows_the_browser(self):
        with browsing("http://erp.localhost:8000/app"):
            session_name = self.session_of(self.payment_url())
            self.assertEqual(
                checkout_url(session_name), f"http://erp.localhost:8000/paystack-checkout/{session_name}"
            )

    def test_callback_url_uses_the_origin_the_payer_reported(self):
        from paystack_frappe.api import start_checkout

        session_name = self.session_of(self.payment_url())
        with browsing("http://erp.localhost/api/method/paystack_frappe.api.start_checkout"):
            start_checkout(session_name, email=LEARNER, origin="http://erp.localhost:8000")
        call = [c for c in self.fake.calls if c["path"] == "/transaction/initialize"][-1]
        self.assertEqual(
            call["json"]["callback_url"], f"http://erp.localhost:8000/paystack-checkout/{session_name}"
        )

    def test_redirects_stay_on_the_payers_origin(self):
        """Payments' own gateways redirect to "payment-success?..."; so do we."""
        session_name = self.session_of(self.payment_url())
        self.start(session_name)
        self.pay_and_notify(session_name)
        from paystack_frappe.core.lifecycle import finish

        result = finish(session_name, "paid")
        self.assertEqual(self.session(session_name).status, PAID)
        self.assertTrue(result["redirect"].startswith("/payment-success?"))

    def test_a_consumer_redirect_on_this_site_becomes_a_path(self):
        with browsing("http://erp.localhost:8000/paystack-checkout/X"):
            self.assertEqual(browser_path("http://erp.localhost/thank-you?a=1"), "/thank-you?a=1")
            self.assertEqual(browser_path("/thank-you"), "/thank-you")
            foreign = "https://learn.example.com/course"
            self.assertEqual(browser_path(foreign), foreign)
