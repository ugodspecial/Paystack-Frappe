frappe.ui.form.on("Paystack Refund Log", {
	refresh(frm) {
		const colors = { Pending: "orange", Processing: "blue", Processed: "green", Failed: "red" };
		const status = frm.doc.status || "Pending";
		frm.dashboard.clear_headline();
		frm.dashboard.set_headline_alert(__("Status: <strong>{0}</strong>", [__(status)]), colors[status] || "gray");

		if (status === "Processed") {
			frm.add_custom_button(
				__("Send Receipt"),
				() =>
					frappe
						.call({
							method: "frappe_paystack.frappe_paystack.doctype.paystack_refund_log.paystack_refund_log.send_refund_receipt",
							args: { refund_log_name: frm.doc.name },
						})
						.then((r) => {
							frappe.show_alert({
								message: r.message ? __("Receipt email queued") : __("No email address for this payer"),
								indicator: r.message ? "green" : "orange",
							});
						}),
				__("Paystack")
			);
		}
	},
});
