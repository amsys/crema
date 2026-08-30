// Copyright (c) 2026, Crema and contributors
// For license information, please see license.txt

/* global crema_diff_table, crema_proposal_record */
// Both defined once in crema.bundle.js (app_include_js, so already loaded here).

frappe.ui.form.on("Crema Proposal", {
	refresh(frm) {
		// The point of this doctype is a human reading the write before it happens, so
		// the write is shown as a table, not as the raw JSON in the Detail section.
		frm.get_field("diff_preview").html(
			crema_diff_table(crema_proposal_record(frm.doc.payload_json, frm.doc.mapping_json))
		);
		if (frm.doc.status === "Pending") {
			frm.add_custom_button(__("Approve"), () =>
				crema_run_proposal_action(frm, "approve_proposals")
			);
			frm.add_custom_button(__("Discard"), () =>
				crema_run_proposal_action(frm, "discard_proposals")
			);
		}
		if (frm.doc.status === "Approved") {
			frm.add_custom_button(__("Undo"), () =>
				crema_run_proposal_action(frm, "undo_proposals")
			);
		}
	},
});

function crema_run_proposal_action(frm, method) {
	frappe.call({
		method: `crema.api.${method}`,
		args: { names: [frm.doc.name] },
		freeze: true,
		callback(r) {
			frm.reload_doc();
			const failed = (r.message || []).find((row) => row.name === frm.doc.name);
			if (failed) {
				frappe.msgprint({ title: __("Failed"), message: failed.error, indicator: "red" });
			}
		},
	});
}
