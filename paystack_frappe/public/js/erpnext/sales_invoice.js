frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		paystack_frappe.actions.setupForm(frm, {
			getOutstanding: (form) => flt(form.doc.outstanding_amount),
			isPayable: (form) => !form.doc.is_return,
		});
	},
});
