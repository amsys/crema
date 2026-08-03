// Copyright (c) 2026, Crema and contributors
// For license information, please see license.txt

/* global crema_show_error, crema_diff_table */
// crema_show_error and crema_diff_table are defined once in crema.bundle.js
// (app_include_js, so they're already on window by the time this form script runs).
//
// The run status and the stored plan are ordinary doctype fields — the "Last Run" section
// and the virtual plan_* fields declared in the JSON, filled server-side by properties on
// CremaAutomationTask. This file only adds what a field cannot express: the header
// indicator, the failure warning, the source-filter picker, and Dry Run (the affordance
// that makes it safe to schedule something that writes).

const CREMA_STATUS_COLORS = {
	Success: "green",
	Replanned: "orange",
	Failed: "red",
};

function crema_render_status(frm) {
	if (frm.is_new()) return;

	frm.page.set_indicator(
		__(frm.doc.last_status || "Never run"),
		CREMA_STATUS_COLORS[frm.doc.last_status] || "gray"
	);

	// The auto-disable at 5 is otherwise completely silent — the only signal is the
	// Enabled checkbox clearing itself. Set unconditionally: an empty message clears the
	// banner, so a task that recovers doesn't keep a stale warning on the next refresh.
	frm.dashboard.set_headline_alert(
		frm.doc.consecutive_failures >= 3
			? __("{0} runs failed in a row. This task turns itself off at 5.", [
					frm.doc.consecutive_failures,
			  ])
			: "",
		"red"
	);
}

function crema_dry_run(frm) {
	frappe.call({
		method: "crema.api.dry_run_automation",
		args: { task: frm.doc.name },
		freeze: true,
		freeze_message: __("Reading the source and asking the model…"),
		callback(r) {
			crema_show_dry_run(frm, r.message || {});
			frm.reload_doc(); // a fresh plan is saved by the dry run
		},
		error: crema_show_error,
	});
}

function crema_show_dry_run(frm, result) {
	if (result.action === "Report Only") {
		frappe.msgprint({
			title: __("Dry Run"),
			message: `<pre style="white-space:pre-wrap">${frappe.utils.escape_html(
				result.report || result.note || ""
			)}</pre>`,
			wide: true,
		});
		return;
	}

	if (!result.row_count) {
		frappe.msgprint({
			title: __("Dry Run"),
			message: frappe.utils.escape_html(result.note || __("The source produced no rows.")),
		});
		return;
	}

	const verb =
		frm.doc.action === "Update Source Records"
			? __("would be updated")
			: __("would be written");
	const header = [
		`<p>${__("{0} row(s) {1}. Nothing was saved.", [result.row_count, verb])}</p>`,
		result.used_stored_plan
			? `<p class="text-muted small">${__("Using the stored plan.")}</p>`
			: `<p class="text-muted small">${__("A new plan was written and saved.")}</p>`,
		result.note
			? `<p class="text-muted small">${frappe.utils.escape_html(result.note)}</p>`
			: "",
	].join("");

	const rows = (result.rows || []).map((row) => crema_diff_table({ set: row })).join("");
	frappe.msgprint({ title: __("Dry Run"), message: header + rows, wide: true });
}

// A point-and-click builder over one source row's source_filters JSON, following
// frappe/desk/doctype/number_card/number_card.js's render_filters_table: the current
// filters render as a table in the field's own wrapper, and clicking it opens a
// FilterGroup. The Code field stays the source of truth, so a doctype the widget cannot
// render still degrades to editable JSON. form_render is frappe's own "a grid row was
// expanded" hook — before that the row's field wrapper does not exist.
function crema_render_row_filters(frm, row) {
	const grid_row = frm.fields_dict.sources.grid.grid_rows_by_docname[row.name];
	const field = grid_row && grid_row.grid_form && grid_row.grid_form.fields_dict.source_filters;
	if (!field || row.source_type !== "Document Query" || !row.source_doctype) return;

	let filters = [];
	try {
		filters = JSON.parse(row.source_filters || "[]");
	} catch (e) {
		return; // unparseable — leave the raw Code field visible so it can be fixed
	}
	if (!Array.isArray(filters)) return;

	const wrapper = $(field.wrapper).empty();
	const table = $(`<table class="table table-bordered" style="cursor:pointer; margin:0">
			<thead><tr>
				<th style="width:35%">${__("Filter")}</th>
				<th style="width:20%">${__("Condition")}</th>
				<th>${__("Value")}</th>
			</tr></thead>
			<tbody></tbody>
		</table>`).appendTo(wrapper);

	if (filters.length) {
		filters.forEach((f) => {
			// FilterGroup writes 4-element rows (doctype first); a hand-written filter may
			// be 3.
			const [fieldname, operator, value] = f.length > 3 ? f.slice(1) : f;
			table.find("tbody").append(
				$(`<tr>
						<td>${frappe.utils.escape_html(String(fieldname))}</td>
						<td>${frappe.utils.escape_html(String(operator || ""))}</td>
						<td>${frappe.utils.escape_html(String(value))}</td>
					</tr>`)
			);
		});
	} else {
		table
			.find("tbody")
			.append(
				`<tr><td colspan="3" class="text-muted text-center">${__(
					"Click to set filters"
				)}</td></tr>`
			);
	}

	table.on("click", () => {
		if (!frm.has_perm("write")) return;
		const dialog = new frappe.ui.Dialog({
			title: __("Which Records"),
			fields: [{ fieldtype: "HTML", fieldname: "filter_area" }],
			primary_action_label: __("Set"),
			primary_action() {
				frappe.model.set_value(
					row.doctype,
					row.name,
					"source_filters",
					JSON.stringify(frm.crema_filter_group.get_filters())
				);
				dialog.hide();
				crema_render_row_filters(frm, row);
			},
		});
		frm.crema_filter_group = new frappe.ui.FilterGroup({
			parent: dialog.get_field("filter_area").$wrapper,
			doctype: row.source_doctype,
			on_change: () => {},
		});
		if (filters.length) frm.crema_filter_group.add_filters_to_filter_group(filters);
		dialog.show();
	});
}

// The two read-only grid columns. Mirrors CremaAutomationSource._label/_note so the grid
// updates as the row is edited instead of only after the save; the server stamps the same
// strings in validate() and is the source of truth. Deliberately untranslated on both
// sides, so the two never disagree over a saved value.
function crema_stamp_row(frm, row) {
	const query = row.source_type === "Document Query";

	let count = 0;
	try {
		count = Object.keys(JSON.parse(row.source_filters || "[]")).length;
	} catch (e) {
		count = 0;
	}
	const parts = [count === 0 ? "no filters" : count === 1 ? "1 filter" : `${count} filters`];
	parts.push(`${row.source_limit || 50}/run`);
	if (row.incremental) parts.push("only changed");

	frappe.model.set_value(
		row.doctype,
		row.name,
		"source_label",
		query
			? row.source_doctype || ""
			: (row.source_url || "").replace(/^[a-z0-9+.-]+:\/\//i, "")
	);
	frappe.model.set_value(row.doctype, row.name, "source_note", query ? parts.join(" · ") : "");
}

frappe.ui.form.on("Crema Automation Source", {
	form_render: (frm, cdt, cdn) => crema_render_row_filters(frm, locals[cdt][cdn]),

	source_type: (frm, cdt, cdn) => crema_stamp_row(frm, locals[cdt][cdn]),
	source_url: (frm, cdt, cdn) => crema_stamp_row(frm, locals[cdt][cdn]),
	source_limit: (frm, cdt, cdn) => crema_stamp_row(frm, locals[cdt][cdn]),
	incremental: (frm, cdt, cdn) => crema_stamp_row(frm, locals[cdt][cdn]),
	source_filters(frm, cdt, cdn) {
		crema_stamp_row(frm, locals[cdt][cdn]);
		crema_render_row_filters(frm, locals[cdt][cdn]);
	},

	// A different record type needs differently-shaped filters, so the old ones cannot
	// carry over.
	source_doctype(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		frappe.model.set_value(row.doctype, row.name, "source_filters", "[]");
		crema_stamp_row(frm, row);
		crema_render_row_filters(frm, row);
	},
});

frappe.ui.form.on("Crema Automation Task", {
	onload(frm) {
		frappe.call({
			method: "crema.api.get_interfaces",
			callback(r) {
				frm.set_df_property("interface", "options", (r.message || []).join("\n"));
			},
			// Without this the Interface Select is just empty on failure, with nothing to
			// tell the user why — see crema.bundle.js's crema_show_error.
			error: crema_show_error,
		});
	},

	refresh(frm) {
		crema_render_status(frm);
		if (frm.is_new()) return;

		frm.add_custom_button(__("Dry Run"), () => crema_dry_run(frm));
		frm.add_custom_button(__("Run Now"), () => {
			frappe.call({
				method: "crema.api.run_automation_now",
				args: { task: frm.doc.name },
				freeze: true,
				callback(r) {
					frappe.show_alert({
						message: __("Queued as {0}", [r.message]),
						indicator: "green",
					});
				},
				error: crema_show_error,
			});
		});
	},
});
