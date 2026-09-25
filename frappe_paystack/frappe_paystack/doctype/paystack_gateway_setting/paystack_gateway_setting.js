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
		const url =
			onload.webhook_url ||
			`${window.location.origin}/api/method/frappe_paystack.api.paystack_webhook`;
		const gateways = (onload.payment_gateways || []).map((g) => frappe.utils.escape_html(g));

		const html = `
			<p>${__("Set this URL as the Webhook URL of this account in the Paystack dashboard (Settings, API Keys & Webhooks):")}</p>
			<p><code>${frappe.utils.escape_html(url)}</code></p>
			${
				gateways.length
					? `<p class="text-muted small">${__("Payment Gateways using this account")}: ${gateways.join(", ")}</p>`
					: ""
			}`;
		frm.get_field("webhook_help").$wrapper.html(html);
	},
});
