// Copyright (c) 2026, Crema and contributors
// For license information, please see license.txt

/* global crema_show_error, crema_diff_table */
// crema_show_error and crema_diff_table are defined once in crema.bundle.js
// (app_include_js, so they're already on window by the time this form script runs).
//
// The run status and the stored plan are ordinary doctype fields — the "Last Run" section
// and the virtual plan_* fields declared in the JSON, filled server-side by properties on
// CremaAutomationTask. This file only adds what a field cannot express: the header
// indicator (including the site-wide "Crema is off" state), the pipeline strip rendered
// into the pipeline_html field (and reused at the top of the Dry Run dialog), the failure
// warning, the source-filter picker, and Dry Run and Run Now (the affordances that make it
// safe to schedule something that writes).

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

	// frappe.boot.crema_disabled (policy.extend_bootinfo, read once at page load) wins
	// over the failure-streak warning below — a task that is off cannot also be failing,
	// and the off state is the more urgent thing to tell the user.
	if (frappe.boot.crema_disabled) {
		frm.dashboard.set_headline_alert(__("Crema is off. No task runs."), "red");
		return;
	}

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

// --- the pipeline strip --------------------------------------------------------------
//
// A read-only diagram of the fixed Trigger -> Source -> Plan -> Extract -> Write chain,
// rendered into the pipeline_html field (crema_render_pipeline) and reused, unclickable,
// at the top of the Dry Run dialog (crema_show_dry_run passes dry_run_stages instead of
// reading the saved run status). Every card is built from the document itself — nothing
// here calls the server except the Propose Only pending count.

// A card's "stage" key, matched case-insensitively against last_failed_stage (Setup is
// deliberately absent: a Setup failure has no card of its own, see the line rendered
// under the cards below). "ask" shares write's rank: for a No Changes task the merged
// card stands in for Plan, Extract and Write together, and the run's own stage tag for
// that branch is "Ask", never "Write".
const CREMA_PIPELINE_STAGE_RANK = { source: 0, plan: 1, extract: 2, write: 3, ask: 3 };

function crema_pipeline_stage_marks(doc, dry_run_stages) {
	if (dry_run_stages) {
		const done = new Set(dry_run_stages);
		const marks = {};
		Object.keys(CREMA_PIPELINE_STAGE_RANK).forEach((key) => {
			marks[key] = done.has(key) ? "ok" : "not-reached";
		});
		return marks;
	}
	if (!doc.last_status) return {};
	if (doc.last_status !== "Failed") {
		return { source: "ok", plan: "ok", extract: "ok", write: "ok", ask: "ok" };
	}
	const failed_rank = CREMA_PIPELINE_STAGE_RANK[(doc.last_failed_stage || "").toLowerCase()];
	const marks = {};
	Object.keys(CREMA_PIPELINE_STAGE_RANK).forEach((key) => {
		if (failed_rank === undefined) {
			// Setup failed (or, defensively, an unrecognised stage) — nothing below it ran.
			marks[key] = "not-reached";
			return;
		}
		const rank = CREMA_PIPELINE_STAGE_RANK[key];
		marks[key] = rank < failed_rank ? "ok" : rank === failed_rank ? "failed" : "not-reached";
	});
	return marks;
}

function crema_pipeline_mark_html(mark) {
	if (!mark) return "";
	const cls = { ok: "text-success", failed: "text-danger", "not-reached": "text-muted" }[mark];
	const label = { ok: __("Done"), failed: __("Failed"), "not-reached": __("Not reached") }[mark];
	return `<div class="small ${cls}" data-crema-mark="${mark}">${label}</div>`;
}

function crema_pipeline_trigger_text(doc) {
	const esc = frappe.utils.escape_html;
	const title = __(doc.trigger || "Not set");
	if (doc.trigger === "Schedule") {
		const detail =
			doc.schedule_preset && doc.schedule_preset !== "Custom"
				? esc(doc.schedule_preset)
				: esc(doc.schedule || __("Not set"));
		return `${title} — ${detail}`;
	}
	if (doc.trigger === "Once") {
		return `${title} — ${
			doc.run_at ? esc(frappe.datetime.str_to_user(doc.run_at)) : __("Not set")
		}`;
	}
	if (doc.trigger === "Document Event") {
		return `${title} — ${doc.event ? esc(doc.event) : __("Not set")}`;
	}
	return title; // Incoming Email / Webhook — the trigger name says it all
}

function crema_pipeline_source_text(doc) {
	const sources = doc.sources || [];
	if (!sources.length) return __("Not set");
	const esc = frappe.utils.escape_html;
	const types = [...new Set(sources.map((row) => esc(row.source_type || "")))].join(", ");
	return sources.length === 1
		? __("1 source: {0}", [types])
		: __("{0} sources: {1}", [sources.length, types]);
}

function crema_pipeline_plan_text(doc) {
	if (!doc.plan_target_doctype) return __("Not planned yet");
	const esc = frappe.utils.escape_html;
	return doc.plan_match_fields
		? __("Writes to {0}, matched by {1}", [
				esc(doc.plan_target_doctype),
				esc(doc.plan_match_fields),
		  ])
		: __("Writes to {0}", [esc(doc.plan_target_doctype)]);
}

function crema_pipeline_extract_text(doc) {
	return doc.plan_prompt ? __("Prompt set") : __("Not planned yet");
}

function crema_pipeline_write_text(doc, pending_slot) {
	const esc = frappe.utils.escape_html;
	let text = __(doc.action || "Not set");
	if (doc.target_doctype) text += ` → ${esc(doc.target_doctype)}`;
	const emails = (doc.notify_to || "")
		.split(",")
		.map((s) => s.trim())
		.filter(Boolean);
	if (emails.length === 1) text += ` · ${__("emails 1 recipient")}`;
	else if (emails.length > 1) text += ` · ${__("emails {0} recipients", [emails.length])}`;
	if (pending_slot) {
		text += `<br><a class="small" data-crema-pending href="javascript:void(0)"></a>`;
	}
	return text;
}

function crema_pipeline_card(title, body_html, opts) {
	opts = opts || {};
	const field_attr = opts.field ? ` data-crema-field="${opts.field}"` : "";
	const style = [
		"flex:1 1 0",
		"min-width:130px",
		"border:1px solid var(--border-color)",
		"border-radius:var(--border-radius, 6px)",
		"background:var(--control-bg)",
		"padding:8px 10px",
		opts.field ? "cursor:pointer" : "",
		opts.grey ? "opacity:0.55" : "",
	]
		.filter(Boolean)
		.join(";");
	return `<div class="crema-pipeline-card"${field_attr} style="${style}">
		<div class="small text-muted" style="text-transform:uppercase; letter-spacing:.02em">${title}</div>
		<div class="small" style="margin-top:2px">${body_html}</div>
		${opts.mark_html || ""}
	</div>`;
}

// The card content always comes from the document; only the marks differ between the
// saved form (dry_run_stages omitted, read from last_status/last_failed_stage) and the
// Dry Run dialog (dry_run_stages given — a Dry Run never writes, so "write" is never
// marked done, and a Dry Run that raised never reaches this function at all: the error
// goes through crema_show_error instead, so there is no "failed" mark to show here).
function crema_pipeline_html(frm, dry_run_stages) {
	// While Crema is off the cards still show the configuration, but grey and with no
	// marks: the last run's marks describe a past state, not what happens next.
	const off = frappe.boot.crema_disabled;
	const doc = frm.doc;
	const esc = frappe.utils.escape_html;
	const marks = off ? {} : crema_pipeline_stage_marks(doc, dry_run_stages);
	const trigger_mark = off || dry_run_stages ? null : doc.last_status ? "ok" : null;
	const show_pending = !dry_run_stages && !frm.is_new() && doc.action === "Propose Only";

	const cards = [
		crema_pipeline_card(__("Trigger"), crema_pipeline_trigger_text(doc), {
			field: "trigger",
			mark_html: crema_pipeline_mark_html(trigger_mark),
		}),
		crema_pipeline_card(__("Source"), crema_pipeline_source_text(doc), {
			field: "sources",
			mark_html: crema_pipeline_mark_html(marks.source),
		}),
	];

	const is_file_query = (doc.sources || []).some((row) => row.source_type === "File Query");
	if (doc.action === "No Changes") {
		cards.push(
			crema_pipeline_card(__("Plan, Extract, Write"), __("Ask, then report"), {
				field: "instruction",
				mark_html: crema_pipeline_mark_html(marks.ask),
			})
		);
	} else if (is_file_query) {
		const not_used = __("Not used: each file is read directly into records");
		cards.push(crema_pipeline_card(__("Plan"), not_used, { field: "plan_json", grey: true }));
		cards.push(
			crema_pipeline_card(__("Extract"), not_used, { field: "plan_prompt", grey: true })
		);
		cards.push(
			crema_pipeline_card(__("Write"), crema_pipeline_write_text(doc, show_pending), {
				field: "action",
				mark_html: crema_pipeline_mark_html(marks.write),
			})
		);
	} else {
		cards.push(
			crema_pipeline_card(__("Plan"), crema_pipeline_plan_text(doc), {
				field: "plan_json",
				mark_html: crema_pipeline_mark_html(marks.plan),
			})
		);
		cards.push(
			crema_pipeline_card(__("Extract"), crema_pipeline_extract_text(doc), {
				field: "plan_prompt",
				mark_html: crema_pipeline_mark_html(marks.extract),
			})
		);
		cards.push(
			crema_pipeline_card(__("Write"), crema_pipeline_write_text(doc, show_pending), {
				field: "action",
				mark_html: crema_pipeline_mark_html(marks.write),
			})
		);
	}

	let html = off
		? `<div class="text-danger small" style="margin-bottom:4px">${__(
				"Crema is off. No task runs."
		  )}</div>`
		: "";
	html += `<div class="crema-pipeline-cards" style="display:flex; flex-wrap:wrap; gap:8px${
		off ? "; opacity:0.55" : ""
	}">${cards.join("")}</div>`;

	// last_failed_stage is "Setup" for a Setup failure (run_task's own Setup guard,
	// outside _run_inside) — "setup" carries no rank in CREMA_PIPELINE_STAGE_RANK, so no
	// card above is marked failed; this line is the only place that failure shows.
	if (!dry_run_stages && doc.last_status === "Failed" && doc.last_failed_stage === "Setup") {
		html += `<div class="text-danger small" style="margin-top:4px">${__(
			"The task setup failed before any step ran."
		)}</div>`;
	}

	if (!dry_run_stages && !frm.is_new()) {
		const lines = [];
		if (doc.last_run) {
			let line = __("Last run {0}", [esc(frappe.datetime.str_to_user(doc.last_run))]);
			if (doc.last_error) {
				const err =
					doc.last_error.length > 160
						? doc.last_error.slice(0, 160) + "…"
						: doc.last_error;
				line += ` — ${esc(err)}`;
			}
			lines.push(line);
		} else {
			lines.push(__("Never run"));
		}
		const runs_as = doc.run_as ? esc(doc.run_as) : __("the AI profile's account");
		lines.push(
			doc.next_run
				? __("Next run {0} · Runs As {1}", [
						esc(frappe.datetime.str_to_user(doc.next_run)),
						runs_as,
				  ])
				: __("Runs As {0}", [runs_as])
		);
		html += `<div class="text-muted small" style="margin-top:6px">${lines.join(" · ")}</div>`;
	}

	return html;
}

// frappe.db.count is permission-scoped the same way a list view's own count is — a user
// who cannot read Crema Proposal sees no number rather than an error.
function crema_pipeline_update_pending_link($wrapper, frm) {
	const $slot = $wrapper.find("[data-crema-pending]");
	if (!$slot.length) return;
	frappe.db
		.count("Crema Proposal", { filters: { task: frm.doc.name, status: "Pending" } })
		.then((count) => {
			$slot.text(__("{0} pending", [count])).on("click", (e) => {
				e.stopPropagation(); // the card itself scrolls on click; this link opens the list instead
				frappe.set_route("List", "Crema Proposal", {
					task: frm.doc.name,
					status: "Pending",
				});
			});
		});
}

function crema_render_pipeline(frm) {
	const $wrapper = $(frm.fields_dict.pipeline_html.wrapper);
	$wrapper.html(crema_pipeline_html(frm, null));
	if (frappe.boot.crema_disabled) return;

	$wrapper.find(".crema-pipeline-card[data-crema-field]").on("click", function () {
		frm.scroll_to_field($(this).attr("data-crema-field"));
	});
	if (!frm.is_new()) crema_pipeline_update_pending_link($wrapper, frm);
}

function crema_last_written(frm) {
	try {
		return JSON.parse(frm.doc.last_written_json || "{}");
	} catch (e) {
		return {};
	}
}

function crema_write_list_html(pairs) {
	return (
		pairs
			// Pass the escaped name as the label: older frappe builds use the raw name when
			// no label is given, so leaving it to frappe is not safe on every version.
			.map(
				([doctype, name]) =>
					`<li>${frappe.utils.get_form_link(
						doctype,
						name,
						true,
						frappe.utils.escape_html(name)
					)}</li>`
			)
			.join("")
	);
}

function crema_undo_last_run(frm) {
	const written = crema_last_written(frm);
	const created = written.created || [];
	const updated = written.updated || [];
	if (!created.length) return;

	let message = __("This run created {0} record(s). They will be deleted.", [created.length]);
	message += `<ul>${crema_write_list_html(created)}</ul>`;
	if (updated.length) {
		message +=
			__("It also updated {0} record(s). They are not touched — review them yourself.", [
				updated.length,
			]) + `<ul>${crema_write_list_html(updated)}</ul>`;
	}
	if (written.truncated) {
		message += `<p>${__(
			"Only the first records are listed; the run wrote more than that."
		)}</p>`;
	}

	frappe.confirm(message, () => {
		frappe.call({
			method: "crema.api.undo_last_run",
			args: { task: frm.doc.name },
			freeze: true,
			callback(r) {
				frm.reload_doc();
				const result = r.message || {};
				frappe.show_alert({
					message: __("Deleted {0} record(s).", [result.deleted || 0]),
					indicator: "green",
				});
				if ((result.failed || []).length) {
					frappe.msgprint({
						title: __("Some records were not deleted"),
						message: result.failed
							.map((f) => frappe.utils.escape_html(f))
							.join("<br>"),
						indicator: "orange",
					});
				}
			},
			error: crema_show_error,
		});
	});
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
	// What a Dry Run actually runs: always the source; Plan and Extract too for a
	// plan-based task, once there is something to extract; never Write — Dry Run reads,
	// plans and previews, but creates and changes nothing (see automation.dry_run). A No
	// Changes task's single Ask call IS its whole action, so that one is marked done. A
	// File Query task has no Plan/Extract step to run either way (the preview comes
	// straight from reading the files) — its Plan/Extract cards are greyed "Not used" in
	// the strip regardless, so this only matters for correctness, not for what renders.
	const is_file_query = (frm.doc.sources || []).some((row) => row.source_type === "File Query");
	const dry_run_stages =
		result.action === "No Changes"
			? ["source", "ask"]
			: !result.row_count || is_file_query
			? ["source"]
			: ["source", "plan", "extract"];
	const strip = crema_pipeline_html(frm, dry_run_stages);

	if (result.action === "No Changes") {
		frappe.msgprint({
			title: __("Dry Run"),
			message:
				strip +
				`<pre style="white-space:pre-wrap">${frappe.utils.escape_html(
					result.report || result.note || ""
				)}</pre>`,
			wide: true,
		});
		return;
	}

	if (!result.row_count) {
		frappe.msgprint({
			title: __("Dry Run"),
			message:
				strip +
				frappe.utils.escape_html(result.note || __("The source produced no rows.")),
		});
		return;
	}

	const verb =
		{
			"Update the Records It Read": __("would be updated"),
			"Propose Only": __("would be proposed"),
		}[frm.doc.action] || __("would be written");
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
	frappe.msgprint({ title: __("Dry Run"), message: strip + header + rows, wide: true });
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

	// Ticking Enabled does not block — the task may well be new and untested, and
	// _warn_unreadable_sources already shows the pattern of an orange, non-blocking
	// warning at save time for the same kind of "this will probably fail" concern.
	enabled(frm) {
		if (frm.is_new() || !frm.doc.enabled || frm.doc.last_dry_run) return;
		frappe.show_alert({
			message: __("Do a Dry Run before you turn this task on."),
			indicator: "orange",
		});
	},

	refresh(frm) {
		crema_render_status(frm);
		crema_render_pipeline(frm);
		crema_render_match_on_picker(frm);
		if (frm.is_new()) return;

		if (!frappe.boot.crema_disabled) {
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
			}).addClass("btn-primary");
		}
		if ((crema_last_written(frm).created || []).length) {
			frm.add_custom_button(
				__("Undo Last Run"),
				() => crema_undo_last_run(frm),
				__("More")
			).addClass("text-danger");
		}
	},
});
