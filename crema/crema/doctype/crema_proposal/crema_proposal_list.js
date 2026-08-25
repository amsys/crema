// Copyright (c) 2026, Crema and contributors
// For license information, please see license.txt

// hide_name_column, same reason as Crema Log's own list view (see crema_log_list.js):
// this doctype names rows by hash too, and a column of opaque hashes tells a reader
// nothing.
frappe.listview_settings["Crema Proposal"] = {
	hide_name_column: true,

	get_indicator(doc) {
		const colors = { Pending: "orange", Approved: "green", Discarded: "gray" };
		return [__(doc.status), colors[doc.status] || "gray", `status,=,${doc.status}`];
	},

	refresh(listview) {
		listview.page.add_actions_menu_item(__("Approve"), () =>
			crema_run_bulk_proposal_action(listview, "approve_proposals")
		);
		listview.page.add_actions_menu_item(__("Discard"), () =>
			crema_run_bulk_proposal_action(listview, "discard_proposals")
		);
		listview.page.add_actions_menu_item(__("Undo"), () =>
			crema_run_bulk_proposal_action(listview, "undo_proposals")
		);
	},
};

function crema_run_bulk_proposal_action(listview, method) {
	const names = listview.get_checked_items(true);
	if (!names.length) {
		frappe.msgprint(__("Select one or more rows first."));
		return;
	}
	frappe.call({
		method: `crema.api.${method}`,
		args: { names },
		freeze: true,
		callback(r) {
			listview.refresh();
			const failed = r.message || [];
			if (failed.length) {
				frappe.msgprint({
					title: __("Some rows failed"),
					message: failed.map((row) => `${row.name}: ${row.error}`).join("<br>"),
					indicator: "red",
				});
			}
		},
	});
}
