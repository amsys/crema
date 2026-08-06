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
	if (result.action === "No Changes") {
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
		frm.doc.action === "Update the Records It Read"
			? __("would be updated")
			: __("would be written");
	// A File Query dry run has no plan at all — each file was read straight into records
	// by extract() — so "used_stored_plan" is absent rather than false, and the line
	// below is skipped entirely instead of misreporting "a new plan was written".
	let plan_line = "";
	if ("used_stored_plan" in result) {
		plan_line = result.used_stored_plan
			? `<p class="text-muted small">${__("Using the stored plan.")}</p>`
			: `<p class="text-muted small">${__("A new plan was written and saved.")}</p>`;
	}
	const header = [
		`<p>${__("{0} row(s) {1}. Nothing was saved.", [result.row_count, verb])}</p>`,
		plan_line,
		result.note
			? `<p class="text-muted small">${frappe.utils.escape_html(result.note)}</p>`
			: "",
	].join("");

	// A File Query row already comes back shaped {set, child_set} from extract(), so it
	// renders as-is (child rows included); a plan-based row is still a flat extracted
	// object and needs wrapping the way it always has.
	const rows = (result.rows || [])
		.map((row) => crema_diff_table(row?.set !== undefined ? row : { set: row }))
		.join("");
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
	const field = grid_row?.grid_form?.fields_dict.source_filters;
	const is_query = row.source_type === "Document Query" || row.source_type === "File Query";
	if (!field || !is_query || !row.source_doctype) return;

	let filters = [];
	try {
		filters = JSON.parse(row.source_filters || "[]");
	} catch (e) {
		return; // unparseable — leave the raw Code field visible so it can be fixed
	}
	if (!Array.isArray(filters)) return;
	// Stored filters may be the 3-element [fieldname, operator, value] form — what
	// _apply_email_trigger, the seeded example task and hand-written JSON all write.
	// FilterGroup.add_filters_to_filter_group only reads the 4-element
	// [doctype, fieldname, operator, value] one, and mis-reads a 3-element row as a doctype
	// named after the field: it rejects it, opens empty, and Set then writes [] over the
	// user's filters. Normalise here, so the table and the dialog read the same thing.
	filters = filters.map((f) => (f.length > 3 ? f : [row.source_doctype, ...f]));

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
			const [, fieldname, operator, value] = f;
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
					JSON.stringify(filter_group.get_filters())
				);
				dialog.hide();
				crema_render_row_filters(frm, row);
			},
		});
		// Local, not on frm: two expanded rows share one form, and a single slot would have
		// the older dialog's Set write the newer row's filters.
		let filter_group;
		// FieldSelect.build_options reads frappe.get_meta(row.source_doctype) directly
		// (frappe/public/js/frappe/ui/filters/field_select.js) with no fallback — on a
		// doctype this session has never loaded, that throws instead of returning
		// undefined. Nothing upstream of this click guarantees the meta is loaded.
		frappe.model.with_doctype(row.source_doctype, () => {
			filter_group = new frappe.ui.FilterGroup({
				parent: dialog.get_field("filter_area").$wrapper,
				doctype: row.source_doctype,
				on_change: () => {},
			});
			if (filters.length) {
				filter_group.add_filters_to_filter_group(filters);
			} else {
				// The two lines the list view's own filter popover runs when it opens on an
				// unfiltered list (filter_list.js set_popover_events): seed one blank row, so
				// the field-name autocomplete — frappe.ui.FieldSelect, the same control the
				// list view types into — is on screen instead of behind an "Add a Filter" the
				// user has to find first. get_filters() drops a row with no value, so leaving
				// it untouched and pressing Set still stores nothing.
				filter_group.toggle_empty_filters(false);
				filter_group.add_filter(row.source_doctype, "name");
			}
			dialog.show();
		});
	});
}

// The read-only grid columns. Mirrors CremaAutomationSource._label so the grid updates as
// the row is edited instead of only after the save; the server stamps the same string in
// validate() and is the source of truth. Deliberately untranslated on both sides, so the
// two never disagree over a saved value.
//
// The query-only cells are cleared on a URL row for the same reason the server clears
// them: a grid column's depends_on mutates the shared docfield, so it cannot hide one
// row's cell without hiding every row's.
function crema_stamp_row(frm, row) {
	const query = row.source_type === "Document Query";
	const file_query = row.source_type === "File Query";
	const set = (fieldname, value) =>
		frappe.model.set_value(row.doctype, row.name, fieldname, value);

	// A File Query always reads the File doctype itself — pinned here (mirroring
	// CremaAutomationSource.validate) so Which Records' depends_on and the filter
	// dialog below both have a doctype to work with before the row is even saved.
	if (file_query && row.source_doctype !== "File") set("source_doctype", "File");

	let count = 0;
	try {
		count = Object.keys(JSON.parse(row.source_filters || "[]")).length;
	} catch (e) {
		count = 0;
	}

	let heading;
	if (file_query) {
		heading = "Files";
	} else if (query) {
		heading = row.source_doctype || "";
	} else {
		heading = (row.source_url || "").replace(/^[a-z0-9+.-]+:\/\//i, "");
	}

	const plural = count > 1 ? "s" : "";
	set(
		"source_label",
		(query || file_query) && count ? `${heading} · ${count} filter${plural}` : heading
	);
	if (file_query) {
		set("read_attachments", 0); // the File Query IS the file read; nothing to attach
	} else if (!query) {
		set("source_limit", 0);
		set("incremental", 0);
		set("read_attachments", 0);
	}
}

frappe.ui.form.on("Crema Automation Source", {
	form_render: (frm, cdt, cdn) => crema_render_row_filters(frm, locals[cdt][cdn]),

	// "Stop if a Source Fails" only means something once there are two sources, and its
	// depends_on is not re-evaluated when a grid row is added. grid.js fires <fieldname>_add
	// / _remove against the CHILD doctype (script_manager.trigger(fieldname + "_add",
	// d.doctype, d.name)), not the parent — so this has to live here, not on
	// "Crema Automation Task", or it never fires at all and the field stays hidden until
	// the next save.
	sources_add: (frm) => frm.layout.refresh_dependency(),
	sources_remove: (frm) => frm.layout.refresh_dependency(),

	source_type(frm, cdt, cdn) {
		// For File Query, crema_stamp_row's own source_doctype="File" set_value re-enters
		// this doctype's own handler below and re-renders the filter dialog on it. This
		// call still matters on its own: switching between two non-Document-Query types
		// (URL <-> File Query with no filters yet) never fires that handler.
		crema_stamp_row(frm, locals[cdt][cdn]);
		crema_render_row_filters(frm, locals[cdt][cdn]);
	},
	source_url: (frm, cdt, cdn) => crema_stamp_row(frm, locals[cdt][cdn]),
	source_limit: (frm, cdt, cdn) => crema_stamp_row(frm, locals[cdt][cdn]),
	source_filters(frm, cdt, cdn) {
		crema_stamp_row(frm, locals[cdt][cdn]);
		crema_render_row_filters(frm, locals[cdt][cdn]);
	},

	// A different record type needs differently-shaped filters, so the old ones cannot
	// carry over.
	source_doctype(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		// Which Records depends_on this field, so frappe re-renders that control — as part
		// of this set_value's own trigger chain, which set_value resolves its promise
		// after. Painting before then is undone, leaving the raw JSON box on screen, and a
		// setTimeout is only a guess at when "then" is.
		frappe.model.set_value(row.doctype, row.name, "source_filters", "[]").then(() => {
			crema_stamp_row(frm, row);
			crema_render_row_filters(frm, row);
		});
	},
});

// Match Records On is admin-entered, not model-authored (see automation.py's module
// docstring and CremaAutomationTask._validate_match_on): the target doctype's own
// metadata already answers "what identifies this record" deterministically, so this is
// a picker over that metadata, not a planning call.
function crema_default_match_fields(doctype) {
	const meta = frappe.get_meta(doctype);
	if (!meta) return [];
	const unique = meta.fields.filter((df) => df.unique).map((df) => df.fieldname);
	if (unique.length) return unique;
	const autoname = (meta.autoname || "").match(/^field:(.+)$/);
	return autoname ? [autoname[1]] : [];
}

// A clickable "Pick fields…" link under Match Records On, mirroring the click-to-open
// pattern crema_render_row_filters uses for a source row's Which Records — the Data
// field stays the source of truth and degrades to editable text if this cannot render.
function crema_render_match_on_picker(frm) {
	const field = frm.fields_dict.match_on;
	if (!field) return;
	$(field.wrapper).find(".crema-match-on-pick").remove();
	if (!frm.doc.target_doctype || !frm.has_perm("write")) return;

	$(
		`<a class="crema-match-on-pick small text-muted" style="cursor:pointer">${__(
			"Pick fields…"
		)}</a>`
	)
		.appendTo($(field.wrapper).find(".control-input-wrapper"))
		.on("click", () => {
			const doctype = frm.doc.target_doctype;
			frappe.model.with_doctype(doctype, () => {
				const options = frappe
					.get_meta(doctype)
					.fields.filter((df) => !df.hidden && frappe.model.is_value_type(df))
					.map((df) => df.fieldname)
					.concat("name");

				const dialog = new frappe.ui.Dialog({
					title: __("Match Records On"),
					fields: [
						{
							fieldtype: "MultiSelectPills",
							fieldname: "fields",
							label: __("Fields"),
							get_data: (txt) => options.filter((f) => f.includes(txt || "")),
						},
					],
					primary_action_label: __("Set"),
					primary_action(values) {
						frm.set_value("match_on", (values.fields || []).join(", "));
						dialog.hide();
					},
				});
				dialog.set_value(
					"fields",
					(frm.doc.match_on || "")
						.split(",")
						.map((f) => f.trim())
						.filter(Boolean)
				);
				dialog.show();
			});
		});
}

frappe.ui.form.on("Crema Automation Task", {
	onload(frm) {
		frappe.call({
			method: "crema.api.get_interfaces",
			callback(r) {
				// The values are already in this field's meta (install.sync_interface_options);
				// this only swaps in {value, label} objects, since a Select options string
				// cannot carry a label separate from its value. Degrades to the meta options
				// on failure, which are the same values without the friendly names.
				frm.set_df_property("interface", "options", r.message || []);
			},
			error: crema_show_error,
		});
	},

	// Mirrors CremaAutomationTask._apply_email_trigger, which appends the same row in
	// validate(). The server stays the source of truth; doing it here too means the row
	// the trigger promises shows up when the trigger is picked, instead of the grid
	// sitting empty — and mandatory — until a save the user cannot make yet. Like the
	// server, this never removes the row when the trigger changes away.
	// Prefill from the doctype's own metadata rather than leaving the admin to guess a
	// fieldname — never overwrites a value they already set.
	target_doctype(frm) {
		crema_render_match_on_picker(frm);
		if (!frm.doc.target_doctype || frm.doc.match_on) return;
		frappe.model.with_doctype(frm.doc.target_doctype, () => {
			const fields = crema_default_match_fields(frm.doc.target_doctype);
			if (fields.length) frm.set_value("match_on", fields.join(", "));
		});
	},

	trigger(frm) {
		if (frm.doc.trigger !== "Incoming Email") return;
		const seeded = (frm.doc.sources || []).some(
			(row) => row.source_type === "Document Query" && row.source_doctype === "Communication"
		);
		if (seeded) return;

		const row = frm.add_child("sources", {
			source_type: "Document Query",
			source_doctype: "Communication",
			source_filters: JSON.stringify([["sent_or_received", "=", "Received"]]),
			source_limit: 5,
			incremental: 1,
			read_attachments: 1,
		});
		crema_stamp_row(frm, row);
		frm.refresh_field("sources");
		// add_child does not fire sources_add, and "Stop if a Source Fails" counts rows.
		frm.layout.refresh_dependency();
	},

	refresh(frm) {
		crema_render_status(frm);
		crema_render_match_on_picker(frm);
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
