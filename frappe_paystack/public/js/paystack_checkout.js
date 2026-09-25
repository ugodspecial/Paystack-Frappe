/**
 * Paystack checkout page.
 *
 * The transaction is always initialised on the server (start_checkout): the
 * browser never chooses the amount, the currency or the reference. Inline
 * mode resumes that transaction in the Paystack popup; Hosted mode redirects
 * to Paystack, which sends the payer back to this page. Either way the result
 * is verified on the server (verify_checkout) before anything is trusted.
 */
(function () {
	"use strict";

	const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
	const API = "frappe_paystack.api.";
	const POLL_INTERVAL_MS = 6000;
	const POLL_LIMIT = 30;

	const node = document.getElementById("paystack-checkout-data");
	if (!node) {
		return;
	}

	let data;
	try {
		data = JSON.parse(node.textContent);
	} catch (error) {
		console.error("Paystack: could not read the checkout payload", error);
		return;
	}

	const button = document.getElementById("ps-pay");
	const emailInput = document.getElementById("ps-email");
	const feedback = document.getElementById("ps-feedback");
	let busy = false;
	let pollTimer = null;
	let polls = 0;
	const label = button ? button.textContent : "";

	function say(message, tone) {
		if (!feedback) {
			return;
		}
		feedback.hidden = !message;
		feedback.textContent = message || "";
		feedback.className = `ps-alert ps-alert--${tone || "info"}`;
	}

	function setBusy(value, text) {
		busy = value;
		if (button) {
			button.disabled = value;
			button.textContent = value ? text || __("Processing...") : label;
		}
	}

	function call(method, args) {
		return new Promise((resolve, reject) => {
			frappe.call({
				method: API + method,
				args: args,
				type: "POST",
				callback: (r) => resolve(r.message),
				error: (r) => reject(r),
			});
		});
	}

	function payerEmail() {
		if (!data.needs_email) {
			return data.email || "";
		}
		return ((emailInput && emailInput.value) || "").trim();
	}

	function stopPolling() {
		if (pollTimer) {
			clearTimeout(pollTimer);
			pollTimer = null;
		}
	}

	function handleResult(result) {
		if (!result) {
			return false;
		}
		if (result.redirect && ["Paid", "Partially Refunded", "Refunded"].includes(result.status)) {
			stopPolling();
			say(__("Payment received. Redirecting..."), "success");
			window.location.href = result.redirect;
			return true;
		}
		if (["Paid", "Partially Refunded", "Refunded"].includes(result.status)) {
			stopPolling();
			say(__("Payment received. Thank you."), "success");
			window.location.reload();
			return true;
		}
		if (result.status === "Failed") {
			stopPolling();
			setBusy(false);
			say(__("The payment was not successful. You can try again."), "danger");
			return true;
		}
		if (result.status === "Needs Attention") {
			stopPolling();
			say(__("We received your payment but need to review it. The merchant will contact you."), "warning");
			return true;
		}
		return false;
	}

	function verify(transactionReference) {
		return call("verify_checkout", {
			reference: data.reference,
			transaction_reference: transactionReference || undefined,
		}).then(handleResult);
	}

	function poll() {
		stopPolling();
		if (polls >= POLL_LIMIT) {
			setBusy(false);
			say(__("We have not heard back from Paystack yet. If you completed the payment, it will be confirmed shortly."), "info");
			return;
		}
		pollTimer = setTimeout(() => {
			polls += 1;
			verify().then((done) => {
				if (!done) {
					poll();
				}
			}).catch(() => poll());
		}, POLL_INTERVAL_MS);
	}

	function resume(checkout) {
		if (typeof PaystackPop === "undefined") {
			if (checkout.authorization_url) {
				window.location.href = checkout.authorization_url;
				return;
			}
			setBusy(false);
			say(__("Paystack could not be loaded. Check your connection and try again."), "danger");
			return;
		}
		const popup = new PaystackPop();
		popup.resumeTransaction(checkout.access_code, {
			onSuccess(transaction) {
				verify((transaction && (transaction.reference || transaction.trxref)) || checkout.reference);
			},
			onCancel() {
				stopPolling();
				setBusy(false);
				say(__("Payment cancelled. You can try again."), "warning");
			},
			onError(error) {
				stopPolling();
				setBusy(false);
				say((error && error.message) || __("The payment could not be started. Please try again."), "danger");
			},
		});
		// Fallback when the popup gives no callback: ask the server until Paystack answers.
		polls = 0;
		poll();
	}

	function start() {
		if (busy) {
			return;
		}
		const email = payerEmail();
		if (!EMAIL_PATTERN.test(email)) {
			say(__("Enter a valid email address."), "warning");
			if (emailInput) {
				emailInput.focus();
			}
			return;
		}
		say("", "info");
		setBusy(true);
		call("start_checkout", { reference: data.reference, email: email })
			.then((checkout) => {
				if (!checkout) {
					throw new Error("empty");
				}
				if (checkout.mode === "Hosted" && checkout.authorization_url) {
					setBusy(true, __("Redirecting to Paystack..."));
					window.location.href = checkout.authorization_url;
					return;
				}
				resume(checkout);
			})
			.catch(() => {
				setBusy(false);
				say(__("We could not start the payment. Please refresh the page and try again."), "danger");
			});
	}

	if (button) {
		button.addEventListener("click", start);
	}
})();
