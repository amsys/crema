// Copyright (c) 2026, Crema and contributors
// For license information, please see license.txt

// Without this the list shows last_status as plain text, so a failing task looks exactly
// like a healthy one. Mirrors crema_provider_list.js.
//
// task_name/last_status/enabled deliberately have no in_list_view: name == task_name
// (autoname is field:task_name) so the subject column already shows it, and the indicator
// below already says Disabled/Success/Replanned/Failed. Showing them again as columns is
// the same value twice.

// A Select column is hardcoded to a status pill with a coloured dot in frappe's list view
// (list_view.js, the df.fieldtype === "Select" branch). Trigger and AI Profile are
// categories, not states, so a dot next to "Schedule" reads as a status that doesn't
// exist. A formatter bypasses that branch entirely; this returns the same filterable
// anchor frappe gives a Data column, so click-to-filter still works.
const plain = (value, df) =>
	`<a class="filterable ellipsis" data-filter="${df.fieldname},=,${frappe.utils.escape_html(
		value
	)}">${frappe.utils.escape_html(__(value))}</a>`;

frappe.listview_settings["Crema Automation Task"] = {
	// title_field is set, so frappe appends its own trailing ID column unless this is on —
	// and that column would repeat the subject, since name == task_name.
	hide_name_column: true,
	add_fields: ["enabled", "last_status"],
	formatters: {
		trigger: plain,
		interface: plain,
	},
	get_indicator(doc) {
		if (!doc.enabled) {
			return [__("Disabled"), "gray", "enabled,=,0"];
		}
		const colors = { Success: "green", Replanned: "orange", Failed: "red" };
		if (!doc.last_status) {
			return [__("Never run"), "blue", "last_status,=,"];
		}
		return [
			__(doc.last_status),
			colors[doc.last_status] || "gray",
			`last_status,=,${doc.last_status}`,
		];
	},
};
