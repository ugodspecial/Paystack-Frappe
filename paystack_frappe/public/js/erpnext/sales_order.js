frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		paystack_frappe.actions.setupForm(frm, {
			getOutstanding: (form) =>
				Math.max(0, flt(form.doc.grand_total) - flt(form.doc.advance_paid)),
			isPayable: (form) => !["Completed", "Closed"].includes(form.doc.status),
		});
	},
});
