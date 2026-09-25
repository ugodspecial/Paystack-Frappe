import fs from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const SOURCE = fs.readFileSync(path.resolve(process.cwd(), "frappe_paystack/public/js/paystack_checkout.js"), "utf8");

let calls;
let replies;
let popup;
let redirect;

function mount(payload, withEmail = false) {
	document.body.innerHTML = `
		<div id="ps-feedback" hidden></div>
		${withEmail ? '<input id="ps-email">' : ""}
		<button id="ps-pay">Pay</button>
		<script type="application/json" id="paystack-checkout-data">${JSON.stringify(payload)}</script>`;
	new Function(SOURCE)(); // eslint-disable-line no-new-func
}

function click() {
	document.getElementById("ps-pay").click();
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
	vi.useRealTimers();
	calls = [];
	replies = {};
	popup = null;
	redirect = "";
	globalThis.__ = (text, args) => (args || []).reduce((out, value, index) => out.replace(`{${index}}`, value), text);
	globalThis.frappe = {
		call: (options) => {
			calls.push({ method: options.method, args: options.args, type: options.type });
			const reply = replies[options.method];
			if (reply instanceof Error) {
				options.error(reply);
			} else {
				options.callback({ message: typeof reply === "function" ? reply(options.args) : reply });
			}
		},
	};
	globalThis.PaystackPop = function PaystackPop() {
		return {
			resumeTransaction: (accessCode, callbacks) => {
				popup = { accessCode, callbacks };
			},
		};
	};
	Object.defineProperty(window, "location", {
		configurable: true,
		writable: true,
		value: {
			set href(url) {
				redirect = url;
			},
			get href() {
				return redirect;
			},
			reload: vi.fn(),
		},
	});
});

afterEach(() => {
	delete globalThis.PaystackPop;
});

describe("checkout page", () => {
	it("asks for a valid email before starting", async () => {
		mount({ reference: "S1", needs_email: true, email: "" }, true);
		document.getElementById("ps-email").value = "not-an-email";
		click();
		await flush();
		expect(calls).toHaveLength(0);
		expect(document.getElementById("ps-feedback").hidden).toBe(false);
	});

	it("starts on the server and resumes the transaction inline", async () => {
		replies["frappe_paystack.api.start_checkout"] = { mode: "Inline", access_code: "ac_S1", reference: "S1" };
		replies["frappe_paystack.api.verify_checkout"] = { status: "Paid", redirect: "/payment-success?x=1" };
		mount({ reference: "S1", needs_email: false, email: "payer@example.com" });
		click();
		await flush();

		expect(calls[0]).toEqual({
			method: "frappe_paystack.api.start_checkout",
			args: { reference: "S1", email: "payer@example.com" },
			type: "POST",
		});
		// The browser never sends an amount, currency or reference of its own.
		expect(Object.keys(calls[0].args)).toEqual(["reference", "email"]);
		expect(popup.accessCode).toBe("ac_S1");

		popup.callbacks.onSuccess({ reference: "S1" });
		await flush();
		expect(calls[1].method).toBe("frappe_paystack.api.verify_checkout");
		expect(calls[1].args.transaction_reference).toBe("S1");
		expect(redirect).toBe("/payment-success?x=1");
	});

	it("redirects to Paystack in hosted mode", async () => {
		replies["frappe_paystack.api.start_checkout"] = {
			mode: "Hosted",
			authorization_url: "https://checkout.paystack.com/ac_S2",
		};
		mount({ reference: "S2", needs_email: false, email: "payer@example.com" });
		click();
		await flush();
		expect(redirect).toBe("https://checkout.paystack.com/ac_S2");
		expect(popup).toBeNull();
	});

	it("re-enables the button when the payer cancels", async () => {
		replies["frappe_paystack.api.start_checkout"] = { mode: "Inline", access_code: "ac_S3", reference: "S3" };
		mount({ reference: "S3", needs_email: false, email: "payer@example.com" });
		click();
		await flush();
		popup.callbacks.onCancel();
		expect(document.getElementById("ps-pay").disabled).toBe(false);
		expect(document.getElementById("ps-feedback").textContent).toContain("cancelled");
	});

	it("reports a failed verification without redirecting", async () => {
		replies["frappe_paystack.api.start_checkout"] = { mode: "Inline", access_code: "ac_S4", reference: "S4" };
		replies["frappe_paystack.api.verify_checkout"] = { status: "Failed", redirect: null };
		mount({ reference: "S4", needs_email: false, email: "payer@example.com" });
		click();
		await flush();
		popup.callbacks.onSuccess({ reference: "S4" });
		await flush();
		expect(redirect).toBe("");
		expect(document.getElementById("ps-feedback").textContent).toContain("not successful");
	});

	it("falls back to the hosted page when the Paystack script is unavailable", async () => {
		delete globalThis.PaystackPop;
		replies["frappe_paystack.api.start_checkout"] = {
			mode: "Inline",
			access_code: "ac_S5",
			authorization_url: "https://checkout.paystack.com/ac_S5",
		};
		mount({ reference: "S5", needs_email: false, email: "payer@example.com" });
		click();
		await flush();
		expect(redirect).toBe("https://checkout.paystack.com/ac_S5");
	});
});
