const WEBHOOK_PATH = "/api/method/paystack_frappe.api.paystack_webhook";

/**
 * The webhook URL to show, as the administrator's browser can see it.
 *
 * The server builds the URL from the address the request arrived on, which a
 * proxy may have stripped the port from (nginx forwards `Host $host`), so a
 * site reached at erp.localhost:8080 could be reported as erp.localhost - port
 * 80, where nothing is listening. Here the page knows its own origin, so for a
 * URL on this very host the browser's scheme, host and port are used.
 *
 * A URL on a *different* host is kept untouched: that is a pinned `host_name`,
 * the public address of the site, which is exactly what Paystack must call and
 * is not the desk address.
 */
function webhook_url(reported) {
	const here = window.location;
	if (!reported) {
		return `${here.origin}${WEBHOOK_PATH}`;
	}

	try {
		const url = new URL(reported, here.origin);
		if (url.hostname !== here.hostname) {
			return url.href;
		}
		return `${here.origin}${url.pathname}${url.search}`;
	} catch (error) {
		return `${here.origin}${WEBHOOK_PATH}`;
	}
}

/** True for an address Paystack's servers cannot reach from the internet. */
function is_local_address(url) {
	let hostname = "";
	try {
		hostname = new URL(url).hostname.toLowerCase();
	} catch (error) {
		return false;
	}

	return (
		hostname === "localhost" ||
		hostname.endsWith(".localhost") ||
		hostname === "127.0.0.1" ||
		hostname === "::1" ||
		hostname === "[::1]" ||
		/^10\./.test(hostname) ||
		/^192\.168\./.test(hostname) ||
		/^172\.(1[6-9]|2\d|3[01])\./.test(hostname)
	);
}

frappe.ui.form.on("Paystack Gateway Setting", {
	refresh(frm) {
		frm.trigger("render_webhook_help");

		if (frm.is_new()) {
			return;
		}

		frm.add_custom_button(__("Test Connection"), () => {
			frm.call("test_connection").then((r) => {
				if (r.message && r.message.ok) {
					frappe.show_alert({ message: r.message.message, indicator: "green" });
				}
			});
		});
	},

	render_webhook_help(frm) {
		const onload = frm.doc.__onload || {};
		const url = webhook_url(onload.webhook_url);
		const gateways = (onload.payment_gateways || []).map((g) => frappe.utils.escape_html(g));

		const html = `
			<p>${__("Set this URL as the Webhook URL of this account in the Paystack dashboard (Settings, API Keys & Webhooks):")}</p>
			<p><code>${frappe.utils.escape_html(url)}</code></p>
			${
				is_local_address(url)
					? `<p class="text-muted small">${__(
							"This address is on your own machine, so Paystack cannot call it. While developing, expose the site through a tunnel (ngrok, cloudflared) and set that address as the Webhook URL instead; payments still settle without it, through the verification sweep."
						)}</p>`
					: ""
			}
			${
				gateways.length
					? `<p class="text-muted small">${__("Payment Gateways using this account")}: ${gateways.join(", ")}</p>`
					: ""
			}`;
		frm.get_field("webhook_help").$wrapper.html(html);
	},
});
