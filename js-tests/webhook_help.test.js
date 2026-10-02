// The webhook URL the Gateway Setting form tells the administrator to register.
//
// It must be the address the site is really reached at, port included: a proxy
// that forwards `Host` without the port makes the server report
// http://erp.localhost/..., and a webhook registered at port 80 is never
// delivered. The desk page knows its own origin, so it corrects it.

import { beforeEach, describe, expect, it } from "vitest";
import { install_web_globals, make_gateway_setting_frm } from "./web_stubs.js";

const PATH = "/api/method/paystack_frappe.api.paystack_webhook";

/** The URL inside the <code> block of the rendered help. */
function shown_url(html) {
	return html.match(/<code>([^<]*)<\/code>/)[1];
}

describe("the webhook URL shown on a Paystack Gateway Setting", () => {
	beforeEach(() => {
		install_web_globals();
	});

	it("keeps the port of the desk the administrator is on", () => {
		const { rendered } = make_gateway_setting_frm(
			// What the server reports when nginx forwarded `Host $host`.
			{ webhook_url: `http://erp.localhost${PATH}` },
			"http://erp.localhost:8080"
		);

		expect(shown_url(rendered.html)).toBe(`http://erp.localhost:8080${PATH}`);
	});

	it("works for any site, on any port", () => {
		const { rendered } = make_gateway_setting_frm(
			{ webhook_url: `http://shop.example.org${PATH}` },
			"http://shop.example.org:9017"
		);

		expect(shown_url(rendered.html)).toBe(`http://shop.example.org:9017${PATH}`);
	});

	it("leaves a live domain alone, with no port invented", () => {
		const { rendered } = make_gateway_setting_frm(
			{ webhook_url: `https://pay.example.com${PATH}` },
			"https://pay.example.com"
		);

		expect(shown_url(rendered.html)).toBe(`https://pay.example.com${PATH}`);
	});

	it("keeps a pinned public address, which is not the desk address", () => {
		const { rendered } = make_gateway_setting_frm(
			{ webhook_url: `https://pay.example.com${PATH}` },
			"http://192.168.1.10:8000"
		);

		expect(shown_url(rendered.html)).toBe(`https://pay.example.com${PATH}`);
	});

	it("falls back to this page's own origin when the server reported none", () => {
		const { rendered } = make_gateway_setting_frm({}, "http://erp.localhost:8080");

		expect(shown_url(rendered.html)).toBe(`http://erp.localhost:8080${PATH}`);
	});

	it("says that Paystack cannot call an address on this machine", () => {
		const { rendered } = make_gateway_setting_frm(
			{ webhook_url: `http://erp.localhost${PATH}` },
			"http://erp.localhost:8080"
		);

		expect(rendered.html).toContain("Paystack cannot call it");
	});

	it("says nothing of the sort for a public address", () => {
		const { rendered } = make_gateway_setting_frm(
			{ webhook_url: `https://pay.example.com${PATH}` },
			"https://pay.example.com"
		);

		expect(rendered.html).not.toContain("Paystack cannot call it");
	});

	it("still lists the Payment Gateways using the account", () => {
		const { rendered } = make_gateway_setting_frm(
			{ webhook_url: `http://erp.localhost${PATH}`, payment_gateways: ["Paystack", "Paystack-NGN"] },
			"http://erp.localhost:8080"
		);

		expect(rendered.html).toContain("Paystack-NGN");
	});
});
