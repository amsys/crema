// Copyright (c) 2026, Crema and contributors
// For license information, please see license.txt

// crema_show_error and crema_diff_table are defined once in crema.bundle.js
// (app_include_js, so they're already on window by the time this form script runs).
//
// Everything below is presentation. The form shows three things the raw doctype cannot:
// what the task will do NEXT (a status panel), what plan it wrote for itself (a field-map
// table instead of a JSON blob), and what it WOULD do if it ran now (Dry Run — the
// affordance that makes it safe to schedule something that writes).

const CREMA_STATUS_COLORS = {
	Success: "green",
	Replanned: "orange",
	Failed: "red",
};

function crema_pill(label, color) {
	return `<span class="indicator-pill ${color}">${frappe.utils.escape_html(label)}</span>`;
}

function crema_panel_row(label, value) {
	return `<div class="mb-1"><span class="text-muted">${frappe.utils.escape_html(
		label
	)}:</span> ${value}</div>`;
}

// The stored plan, readable. plan_json stays in its own collapsed section as the escape
// hatch; this is what an operator actually needs to see. Every value here is
// model-authored text going into an HTML field, so all of it is escaped.
function crema_plan_html(frm) {
	if (!frm.doc.plan_json) {
		return `<div class="text-muted mt-3">${__(
			"No plan yet — it is written on the first run."
		)}</div>`;
	}
	let plan;
	try {
		plan = JSON.parse(frm.doc.plan_json);
	} catch (e) {
		return `<div class="text-muted mt-3">${__(
			"The stored plan is not readable; it will be rewritten on the next run."
		)}</div>`;
	}
	const map = (plan && plan.map) || {};
	const prompt = ((plan && plan.extract) || {}).prompt || "";
	return `
		<div class="mt-3"><b>${__("Plan")}</b></div>
		${crema_panel_row(__("Writes to"), frappe.utils.escape_html(map.doctype || "—"))}
		${crema_panel_row(__("Matches on"), frappe.utils.escape_html((map.match_fields || []).join(", ")))}
		${crema_diff_table({ set: map.field_map || {} })}
		<div class="text-muted small">${__("Extraction prompt")}</div>
		<pre class="small" style="white-space:pre-wrap">${frappe.utils.escape_html(prompt)}</pre>`;
}

function crema_render_status(frm) {
	const field = frm.get_field("status_html");
	if (!field) return;
	const wrapper = field.$wrapper.empty();
	if (frm.is_new()) return;

	const status = frm.doc.last_status;
	const parts = [crema_pill(status || __("Never run"), CREMA_STATUS_COLORS[status] || "gray")];

	if (frm.doc.trigger === "Document Event") {
		parts.push(
			crema_panel_row(
				__("Runs on"),
				frappe.utils.escape_html(
					`${frm.doc.source_doctype || "—"} · ${frm.doc.event || "—"}`
				)
			)
		);
	} else {
		const next = frm.doc.__onload && frm.doc.__onload.next_run;
		parts.push(
			crema_panel_row(
				__("Next run"),
				frm.doc.enabled && next
					? frappe.datetime.str_to_user(next)
					: `<span class="text-muted">${__("not scheduled — the task is disabled")}</span>`
			)
		);
	}

	if (frm.doc.last_run) {
		parts.push(crema_panel_row(__("Last run"), frappe.datetime.str_to_user(frm.doc.last_run)));
	}
	if (frm.doc.last_result) {
		parts.push(crema_panel_row(__("Result"), frappe.utils.escape_html(frm.doc.last_result)));
	}
	if (frm.doc.last_error) {
		parts.push(
			`<div class="text-danger mt-2" style="white-space:pre-wrap">${frappe.utils.escape_html(
				frm.doc.last_error
			)}</div>`
		);
	}
	// The auto-disable at 5 is otherwise completely silent — the only signal is the
	// Enabled checkbox clearing itself.
	if (frm.doc.consecutive_failures >= 3) {
		parts.push(
			`<div class="text-danger mt-2"><b>${__(
				"{0} consecutive failures. This task disables itself at 5.",
				[frm.doc.consecutive_failures]
			)}</b></div>`
		);
	}

	parts.push(crema_plan_html(frm));
	$(`<div>${parts.join("")}</div>`).appendTo(wrapper);
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
		frm.doc.action === "Update Source Records" ? __("would be updated") : __("would be written");
	const header = [
		`<p>${__("{0} row(s) {1}. Nothing was saved.", [result.row_count, verb])}</p>`,
		result.used_stored_plan
			? `<p class="text-muted small">${__("Using the stored plan.")}</p>`
			: `<p class="text-muted small">${__("A new plan was written and saved.")}</p>`,
		result.note ? `<p class="text-muted small">${frappe.utils.escape_html(result.note)}</p>` : "",
	].join("");

	const rows = (result.rows || []).map((row) => crema_diff_table({ set: row })).join("");
	frappe.msgprint({ title: __("Dry Run"), message: header + rows, wide: true });
}

// A point-and-click builder over the source_filters JSON, following
// frappe/desk/doctype/number_card/number_card.js's render_filters_table: the current
// filters render as a table in the field's own wrapper, and clicking it opens a
// FilterGroup. The Code field stays the source of truth, so a doctype the widget cannot
// render still degrades to editable JSON.
function crema_render_filters(frm) {
	const field = frm.get_field("source_filters");
	if (!field || frm.doc.source_type !== "Document Query" || !frm.doc.source_doctype) return;

	let filters = [];
	try {
		filters = JSON.parse(frm.doc.source_filters || "[]");
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
			title: __("Set Source Filters"),
			fields: [{ fieldtype: "HTML", fieldname: "filter_area" }],
			primary_action_label: __("Set"),
			primary_action() {
				frm.set_value(
					"source_filters",
					JSON.stringify(frm.crema_filter_group.get_filters())
				);
				dialog.hide();
				crema_render_filters(frm);
			},
		});
		frm.crema_filter_group = new frappe.ui.FilterGroup({
			parent: dialog.get_field("filter_area").$wrapper,
			doctype: frm.doc.source_doctype,
			on_change: () => {},
		});
		if (filters.length) frm.crema_filter_group.add_filters_to_filter_group(filters);
		dialog.show();
	});
}

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
		crema_render_filters(frm);
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

	// A changed source needs a differently-shaped filter widget; the server clears the
	// stored plan on the same change, so the panel is re-rendered with it.
	source_type: (frm) => crema_render_filters(frm),
	source_doctype(frm) {
		frm.set_value("source_filters", "[]");
		crema_render_filters(frm);
	},
});
