// Copyright (c) 2026, Crema and contributors
// For license information, please see license.txt

// hide_name_column is the only reason this file exists. Frappe's list_view.js appends an
// "ID" column of its own whenever title_field is set and differs from name (see
// setup_columns), and Crema Log names rows by hash — so the list carried a column of
// opaque hashes that means nothing to a reader. This is the supported switch for it.
//
// status has no in_list_view either: the indicator below already renders it, and a Select
// column would print the same word again one cell to the right.
frappe.listview_settings["Crema Log"] = {
	hide_name_column: true,

	get_indicator(doc) {
		const colors = {
			Success: "green",
			Cached: "blue",
			Blocked: "red",
			Error: "orange",
		};
		return [__(doc.status), colors[doc.status] || "gray", `status,=,${doc.status}`];
	},
};
