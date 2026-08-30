// Copyright (c) 2026, Crema and contributors
// For license information, please see license.txt

/* global crema_confirm_records, crema_diff_table, crema_proposal_record */
// All three defined once in crema.bundle.js (app_include_js, so already loaded here).

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
			crema_confirm_bulk_approve(listview)
		);
		listview.page.add_actions_menu_item(__("Discard"), () =>
			crema_run_bulk_proposal_action(listview, "discard_proposals")
		);
		listview.page.add_actions_menu_item(__("Undo"), () =>
			crema_run_bulk_proposal_action(listview, "undo_proposals")
		);
	},
};

// Approve is the only one of the three actions that writes, so it is the only one that
// asks first — and it shows each parked write, not just a count of rows. Discard writes
// nothing.
function crema_confirm_bulk_approve(listview) {
	const names = listview.get_checked_items(true);
	if (!names.length) {
		frappe.msgprint(__("Select one or more rows first."));
		return;
	}
	frappe.db
		.get_list("Crema Proposal", {
			filters: { name: ["in", names] },
			fields: ["name", "target_doctype", "payload_json", "mapping_json"],
			// get_list defaults to 20 rows; the user can check more than that.
			limit: names.length,
		})
		.then((rows) => {
			crema_confirm_records({
				title: __("Approve {0} proposals?", [names.length]),
				rows: null,
				extra_heading: __("Records to write"),
				extra_html: rows
					.map(
						(r, i) =>
							`<b>${i + 1}. ${frappe.utils.escape_html(
								r.target_doctype || ""
							)}</b>${crema_diff_table(
								crema_proposal_record(r.payload_json, r.mapping_json)
							)}`
					)
					.join(""),
				primary_label: __("Approve {0}", [names.length]),
				onConfirm: () => crema_run_bulk_proposal_action(listview, "approve_proposals"),
			});
		});
}

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
			// base_list.refresh() no-ops on identical args within 3 seconds
			// (no_change) — and the menu-open get_counts a moment ago count. The
			// rows just changed on the server, so beat the throttle, or the list
			// keeps showing the old status until the user reloads by hand.
			listview.last_args = null;
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
