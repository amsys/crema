// Copyright (c) 2026, Crema and contributors
// For license information, please see license.txt

// The filters live here, not in crema_usage.json. query_report.js falls back to the
// report record's own filters only when frappe.query_reports[...].filters is absent, so
// these replace them rather than adding to them — do not restate them in the JSON, or
// the two will drift and the JSON copy will never be the one you see.
//
// Two things a JSON filter cannot express are the reason for the move: a computed
// default (From Date is the start of this month, matching how spending limits are
// counted) and a formatter.
frappe.query_reports["Crema Usage"] = {
	filters: [
		{
			fieldname: "from_date",
			fieldtype: "Date",
			label: __("From Date"),
			default: frappe.datetime.month_start(),
		},
		{
			fieldname: "to_date",
			fieldtype: "Date",
			label: __("To Date"),
			default: frappe.datetime.month_end(),
		},
		{
			fieldname: "interface",
			fieldtype: "Data",
			label: __("Use Case"),
			// Matches either the label you see in the table ("Default") or the internal
			// key ("simple") — see _where in crema_usage.py.
		},
		{
			fieldname: "provider",
			fieldtype: "Link",
			label: __("AI Service"),
			options: "Crema Provider",
		},
		{
			fieldname: "status",
			fieldtype: "Select",
			label: __("Status"),
			options: "\nSuccess\nCached\nBlocked\nError",
		},
		{
			fieldname: "group_by",
			fieldtype: "Select",
			label: __("Group By"),
			default: "Use Case",
			options: "Use Case\nAI Service\nModel\nUser\nDay",
		},
	],

	// Blocked and Errors are the two numbers worth noticing; zero should stay quiet.
	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (["blocked", "errors"].includes(column.fieldname) && value) {
			return `<span class="text-danger">${formatted}</span>`;
		}
		return formatted;
	},
};
