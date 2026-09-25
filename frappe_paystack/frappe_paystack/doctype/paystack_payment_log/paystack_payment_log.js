const PAYSTACK_STATUS_COLORS = {
	Pending: "orange",
	Paid: "green",
	Failed: "red",
	Cancelled: "gray",
	Expired: "gray",
	"Needs Attention": "red",
	"Partially Refunded": "yellow",
	Refunded: "purple",
};

const CAPTURED = ["Paid", "Partially Refunded", "Refunded"];

const BOOK_PAYMENT_ENTRY =
	"frappe_paystack.integrations.erpnext.payment_entry.complete_payment";
const BOOKABLE_DOCTYPES = ["Sales Invoice", "Sales Order", "Dunning"];

frappe.ui.form.on("Paystack Payment Log", {
	onload(frm) {
		// ERPNext adapter: the outcome of a manual booking arrives in real time.
		frappe.realtime.on("paystack_payment_completed", (data) => {
			if (!data || data.log !== frm.doc.name) {
				return;
			}
			frappe.show_alert({ message: data.message, indicator: data.booked ? "green" : "red" });
			frm.reload_doc();
		});
	},

	refresh(frm) {
		frm.trigger("decorate");
		frm.trigger("action_buttons");
	},

	decorate(frm) {
		const status = frm.doc.status || "Pending";
		frm.dashboard.clear_headline();
		let message = __("Status: <strong>{0}</strong>", [__(status)]);
		if (CAPTURED.includes(status) && frm.doc.notification_status !== "Notified") {
			message += " &middot; " + __("Application notification: {0}", [__(frm.doc.notification_status)]);
		}
		frm.dashboard.set_headline_alert(message, PAYSTACK_STATUS_COLORS[status] || "gray");
	},

	action_buttons(frm) {
		const group = __("Paystack");

		frm.add_custom_button(
			__("Verify with Paystack"),
			() =>
				frm.call("verify_with_paystack").then((r) => {
					const result = r.message || {};
					frappe.show_alert({
						message: __("Status: {0}", [__(result.status || frm.doc.status)]),
						indicator: CAPTURED.includes(result.status) ? "green" : "blue",
					});
					frm.reload_doc();
				}),
			group
		);

		if (
			CAPTURED.includes(frm.doc.status) &&
			["Pending", "Failed", "Needs Attention"].includes(frm.doc.notification_status)
		) {
			frm.add_custom_button(
				__("Retry Notification"),
				() =>
					frm.call("retry_notification").then(() => frm.reload_doc()),
				group
			);
			frm.add_custom_button(
				__("Mark Notification Resolved"),
				() =>
					frappe.prompt(
						{ fieldtype: "Small Text", fieldname: "note", label: __("How was it resolved?"), reqd: 1 },
						(values) =>
							frm
								.call("mark_notification_resolved", { note: values.note })
								.then(() => frm.reload_doc()),
						__("Mark Notification Resolved")
					),
				group
			);
		}

		if (frm.doc.transaction_id) {
			frm.add_custom_button(
				__("Open in Paystack"),
				() =>
					window.open(
						`https://dashboard.paystack.com/#/transactions/${encodeURIComponent(frm.doc.transaction_id)}/analytics`,
						"_blank",
						"noopener"
					),
				group
			);
		}

		const refundable = flt(frm.doc.amount_paid) - flt(frm.doc.total_refunded);
		if (["Paid", "Partially Refunded"].includes(frm.doc.status) && refundable > 0) {
			frm.add_custom_button(__("Refund"), () => show_refund_dialog(frm, refundable), group);
		}

		// Only present when the ERPNext adapter is active (booking_status is a Custom Field).
		if (
			CAPTURED.includes(frm.doc.status) &&
			["Pending", "Needs Attention"].includes(frm.doc.booking_status) &&
			!frm.doc.payment_entry &&
			BOOKABLE_DOCTYPES.includes(frm.doc.linked_doctype)
		) {
			frm.add_custom_button(
				__("Book Payment Entry"),
				() =>
					frappe.call({ method: BOOK_PAYMENT_ENTRY, args: { payment_log_name: frm.doc.name } }).then(() =>
						frappe.show_alert({ message: __("Verifying with Paystack and booking..."), indicator: "blue" })
					),
				group
			);
		}

		if (frm.doc.status === "Pending") {
			frm.add_custom_button(
				__("Copy Payment Link"),
				() => frappe.utils.copy_to_clipboard(`${window.location.origin}/paystack-checkout/${frm.doc.name}`),
				group
			);
		}
	},
});

function show_refund_dialog(frm, maxRefund) {
	const currency = frm.doc.currency_paid || frm.doc.currency;
	const dialog = new frappe.ui.Dialog({
		title: __("Refund"),
		fields: [
			{
				fieldtype: "Currency",
				fieldname: "amount",
				label: __("Amount"),
				options: currency,
				default: maxRefund,
				reqd: 1,
				description: __("Refundable: {0}", [format_currency(maxRefund, currency)]),
			},
			{ fieldtype: "Small Text", fieldname: "reason", label: __("Reason") },
		],
		primary_action_label: __("Refund"),
		primary_action(values) {
			if (values.amount <= 0 || values.amount > maxRefund) {
				frappe.msgprint(__("Enter an amount between 0 and {0}.", [format_currency(maxRefund, currency)]));
				return;
			}
			frm.call("refund", { amount: values.amount, reason: values.reason }).then((r) => {
				dialog.hide();
				frappe.show_alert({ message: __("Refund {0} requested", [r.message]), indicator: "blue" });
				frm.reload_doc();
			});
		},
	});
	dialog.show();
}
