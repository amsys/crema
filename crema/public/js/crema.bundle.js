// Crema desk UI — a robot button on doctype list views, a robot button on doctype form
// views, and an "Ask …" option in the awesomebar. The list-view button opens a dialog
// that does two things: read an uploaded document (proposing one or more new records),
// or turn a typed request into a filtered List/Report/Kanban view. The form-view button
// opens a dialog that proposes a diff for the document already open, for the user to
// apply and save themselves.
//
// Deliberately almost all client-side. Reading the schema (frappe.get_meta), applying
// filters (frappe.route_options), inserting a record (frappe.model.get_new_doc, saved by
// the user), and applying a diff (frm.set_value/add_child, saved by the user) all go
// through ordinary desk paths, fenced as the session user — the server is only ever
// asked to run the LLM (crema.api.ask_api / extract_api / transform_api). See the
// "implement-in-desk-interface" plan for why: a server-side endpoint doing this would
// run under the interface's isolation user, not the desk user who clicked.

function crema_allowed() {
	return (
		frappe.user_roles.includes("Crema User") || frappe.user_roles.includes("System Manager")
	);
}

function crema_strip_fence(text) {
	const m = /^```(?:json)?\s*([\s\S]*?)\s*```$/.exec((text || "").trim());
	return m ? m[1] : text;
}

// Fields this session user may at least read. frappe.get_meta is NOT permlevel-filtered
// server-side (frappe.desk.form.load.getdoctype has no permission check at all), so this
// is what stops a user's prompt — and the model's answer — from touching fields they
// have no access to.
function crema_readable_fields(doctype) {
	const perm = frappe.perm.get_perm(doctype);
	return frappe
		.get_meta(doctype)
		.fields.filter(
			(df) => df.fieldname && frappe.perm.get_field_display_status(df, null, perm) !== "None"
		);
}

function crema_schema_prompt(doctype) {
	const lines = crema_readable_fields(doctype).map((df) =>
		`- ${df.fieldname} (${df.fieldtype}) ${df.label || ""}`.trim()
	);
	return `Fields of doctype '${doctype}':\n${lines.join("\n")}`;
}

// Fieldnames actually shown as columns in the open list view — cur_list.columns only
// exists once ListView.setup_columns has run for THIS doctype, so fall back to the same
// in_list_view/title_field/name rule the framework itself builds columns from
// (list_view.js setup_columns) when no live list view matches.
function crema_visible_list_fields(doctype) {
	if (typeof cur_list !== "undefined" && cur_list && cur_list.doctype === doctype && cur_list.columns) {
		return new Set(cur_list.columns.filter((c) => c.df && c.df.fieldname).map((c) => c.df.fieldname));
	}
	const meta = frappe.get_meta(doctype);
	const fields = meta.fields.filter((df) => df.in_list_view).map((df) => df.fieldname);
	fields.push("name");
	if (meta.title_field) fields.push(meta.title_field);
	return new Set(fields);
}

// The view interface's system prompt (interfaces.DEFAULT_PROMPTS["view"]) states the
// output shape; this is the per-request user turn, telling the model which fields it
// may use, which of those are actually visible on screen, and the operator vocabulary
// list filters actually support (frappe/public/js/frappe/ui/filters/filter.js) — without
// this the model has no basis for picking a field for an unqualified request like
// "items starting with foo", and no way to know "like" is its only substring/prefix tool.
function crema_view_prompt(doctype) {
	const visible = crema_visible_list_fields(doctype);
	const lines = crema_readable_fields(doctype).map((df) => {
		const shown = visible.has(df.fieldname) ? " [shown in list]" : "";
		return `- ${df.fieldname} (${df.fieldtype}) ${df.label || ""}${shown}`.trim();
	});
	return (
		`Fields of doctype '${doctype}':\n${lines.join("\n")}\n\n` +
		"Rules for building the view specification:\n" +
		"- If the request does not name a field, filter on a field marked [shown in list] " +
		"(prefer a Data/Text field over a Link/Select).\n" +
		'- "starting with X" -> ["like", "X%"]. "containing X" or "with X" -> ["like", "%X%"]. ' +
		'"ending with X" -> ["like", "%X"].\n' +
		"- Text matching is already case-insensitive on this system — never try to force a " +
		"case-sensitive match, and do not mention case in the reason.\n" +
		'- Legal operators: "=", "!=", "like", "not like", "in", "not in", "is", ">", "<", ' +
		'">=", "<=", "Between", "Timespan". "is" takes only "set" or "not set" as its value. ' +
		'"Timespan" takes a relative phrase such as "last week", "yesterday", "this month". ' +
		'"in"/"not in" take a list of values.\n' +
		"- Every filter is combined with AND — there is no way to OR across different fields. " +
		"If the request implies an OR across fields, say so plainly in the reason instead of " +
		"approximating it with AND.\n" +
		'- order_by sorts the whole list: "fieldname asc" or "fieldname desc".\n' +
		'- page_length is an integer row limit for requests like "top 10" or "first 5".'
	);
}

// A blocked prompt/document, a budget stop, or a config error comes back as HTTP 417
// with {blocked, reason} (crema.api._error_response). frappe routes 417 to frappe.call's
// `error` option, never `callback` (request.js maps opts.error -> opts.error_callback,
// and the 417 statusCode handler only fires error_callback, passing the parsed body
// through without rendering anything itself) — so this must be wired as `error` on
// every crema frappe.call, not read inside `callback`.
function crema_show_error(r) {
	const data = r?.message;
	if (data?.reason) {
		frappe.msgprint({
			title: data.blocked ? __("Blocked") : __("Crema"),
			message: data.reason,
			indicator: "red",
		});
		return;
	}
	// frappe's own statusCode handlers (403/404/413/500/504/508) already msgprint
	// something and then call error_callback with no arguments — only 417 passes a body
	// through, and it renders nothing on its own. So `r` present without a `reason` is
	// still a silent case and needs a message; `r` absent means frappe already spoke.
	if (r) {
		frappe.msgprint({ message: __("Crema could not complete this request."), indicator: "red" });
	}
}

// ---- Shared preview rendering -------------------------------------------------------

// One record's field/value + child-table-row-count table, used by both the extract
// preview (one row per record) and the transform preview (a single diff).
function crema_diff_table(record) {
	const rows = Object.entries(record.set || {})
		.map(
			([k, v]) =>
				`<tr><td>${frappe.utils.escape_html(k)}</td><td>${frappe.utils.escape_html(
					String(v)
				)}</td></tr>`
		)
		.concat(
			Object.entries(record.child_set || {}).map(
				([k, v]) =>
					`<tr><td>${frappe.utils.escape_html(k)}</td><td>${(v || []).length} ${__(
						"row(s)"
					)}</td></tr>`
			)
		)
		.join("");
	return `<table class="table table-bordered">${rows || `<tr><td>${__("Nothing found")}</td></tr>`}</table>`;
}

// ---- Path A: file -> new document(s) ------------------------------------------------

function crema_extract_into_new_doc(doctype, file_url, instruction, dialog) {
	frappe.call({
		method: "crema.api.extract_api",
		args: { doctype, file_url, instruction },
		freeze: true,
		freeze_message: __("Reading document…"),
		callback(r) {
			const data = r.message;
			if (!data) return;
			crema_show_extract_preview(doctype, data, dialog);
		},
		error: crema_show_error,
	});
}

// Parent column header = the bare fieldname; child column header =
// "<table_fieldname>.<child_fieldname>" (frappe's importer.py Header/get_df_for_column_header).
// A record's extra child rows are separate CSV rows with every parent column left blank
// (importer.py parse_next_row_for_import) — that's the "blank row = more child rows for the
// document above" rule Data Import itself uses, not a Crema invention.
function crema_records_to_csv(doctype, records) {
	const meta = frappe.get_meta(doctype);
	const parent_fields = meta.fields
		.map((df) => df.fieldname)
		.filter((f) => records.some((r) => f in (r.set || {})));

	const child_tables = {}; // table_fieldname -> ordered list of child fieldnames seen
	records.forEach((r) => {
		Object.entries(r.child_set || {}).forEach(([table, table_rows]) => {
			const seen = (child_tables[table] ||= []);
			(table_rows || []).forEach((row) =>
				Object.keys(row).forEach((f) => seen.includes(f) || seen.push(f))
			);
		});
	});

	const columns = [...parent_fields];
	Object.entries(child_tables).forEach(([table, fields]) =>
		fields.forEach((f) => columns.push(`${table}.${f}`))
	);

	const csv_cell = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
	const lines = [columns.map(csv_cell).join(",")];

	records.forEach((r) => {
		const child_rows_by_table = Object.entries(child_tables).map(([table, fields]) => ({
			table,
			fields,
			rows: (r.child_set || {})[table] || [],
		}));
		const max_rows = Math.max(1, ...child_rows_by_table.map((t) => t.rows.length));

		for (let i = 0; i < max_rows; i++) {
			const line = columns.map((col) => {
				if (i > 0) {
					// Every further row is a child-only continuation — parent columns blank.
					if (parent_fields.includes(col)) return csv_cell("");
				} else if (parent_fields.includes(col)) {
					return csv_cell((r.set || {})[col]);
				}
				const [table, field] = col.includes(".") ? col.split(/\.(.*)/s) : [null, null];
				const t = child_rows_by_table.find((x) => x.table === table);
				return csv_cell(t?.rows[i]?.[field]);
			});
			lines.push(line.join(","));
		}
	});
	return lines.join("\r\n");
}

function crema_import_records(doctype, records) {
	const csv = crema_records_to_csv(doctype, records);
	const fd = new FormData();
	fd.append("file", new Blob([csv], { type: "text/csv" }), `crema-${doctype}.csv`);
	fd.append("is_private", 1);
	fd.append("folder", "Home");
	// A raw fetch, not frappe.call: /api/method/upload_file reads frappe.request.files,
	// which only exists on a real multipart POST.
	fetch("/api/method/upload_file", {
		method: "POST",
		headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
		body: fd,
	})
		.then((r) => (r.ok ? r.json() : Promise.reject(new Error(`upload failed: ${r.status}`))))
		.then((r) => {
			const file_url = r.message?.file_url;
			if (!file_url) {
				frappe.msgprint({ message: __("Could not upload the generated file."), indicator: "red" });
				return;
			}
			frappe.new_doc("Data Import", {
				reference_doctype: doctype,
				import_type: "Insert New Records",
				import_file: file_url,
			});
		})
		// Non-2xx response or a network failure — both leave the dialog already hidden
		// (crema_show_extract_preview's primary_action closes it before calling this), so
		// without this the user is just left staring at the list with no explanation.
		.catch(() => {
			frappe.msgprint({ message: __("Could not upload the generated file."), indicator: "red" });
		});
}

function crema_show_extract_preview(doctype, data, dialog) {
	const records = data.records || [];
	const header = `<div class="text-muted small">${__("Confidence")}: ${(data.confidence ?? 0).toFixed(
		2
	)} — ${frappe.utils.escape_html(data.reason || "")}</div>`;

	if (records.length === 0) {
		dialog.fields_dict.preview.df.hidden = 0;
		dialog.set_df_property("preview", "options", `${header}<p>${__("Nothing found")}</p>`);
		dialog.refresh();
		dialog.set_primary_action(__("Go"), () => dialog.hide());
		return;
	}

	if (records.length === 1) {
		dialog.fields_dict.preview.df.hidden = 0;
		dialog.set_df_property("preview", "options", `${header}${crema_diff_table(records[0])}`);
		dialog.refresh();

		dialog.set_primary_action(__("Create Document"), () => {
			frappe.model.with_doctype(doctype, () => {
				const doc = frappe.model.get_new_doc(doctype);
				Object.assign(doc, records[0].set || {});
				for (const [fieldname, child_rows] of Object.entries(records[0].child_set || {})) {
					(child_rows || []).forEach((row) =>
						Object.assign(frappe.model.add_child(doc, fieldname), row)
					);
				}
				dialog.hide();
				frappe.set_route("Form", doctype, doc.name);
			});
		});
		return;
	}

	// Several records: one row per record, no in-place insert — hand off to Data Import,
	// which runs its own preview before writing anything.
	const rows = records
		.map((r, i) => `<tr><td>${i + 1}</td><td>${crema_diff_table(r)}</td></tr>`)
		.join("");
	dialog.fields_dict.preview.df.hidden = 0;
	dialog.set_df_property(
		"preview",
		"options",
		`${header}<table class="table table-bordered"><tbody>${rows}</tbody></table>`
	);
	dialog.refresh();

	if (!frappe.model.can_import(doctype)) {
		dialog.set_primary_action(__("Go"), () => dialog.hide());
		frappe.msgprint({
			message: __("Importing several records at once needs the System Manager role."),
			indicator: "orange",
		});
		return;
	}
	dialog.set_primary_action(__("Import {0} Documents", [records.length]), () => {
		dialog.hide();
		crema_import_records(doctype, records);
	});
}

// ---- Path B: prompt -> view ---------------------------------------------------------

const CREMA_VALID_VIEWS = new Set(["List", "Report", "Kanban"]);
// filter.js's own conditions list (Between/Timespan included, nested-set "descendants
// of" family left out — those only make sense for a tree doctype the model can't know
// about from the schema prompt alone).
const CREMA_VALID_OPERATORS = new Set([
	"=", "!=", "like", "not like", "in", "not in", "is", ">", "<", ">=", "<=", "Between", "Timespan",
]);
const CREMA_VALID_AGGREGATES = new Set(["count", "sum", "avg"]);

// Fieldtypes worth probing when a text lookup came back empty. Link/Select are in on
// purpose — "todos for acme" often means a Link value, not a Data field.
const CREMA_TEXTISH = new Set([
	"Data", "Small Text", "Text", "Long Text", "Text Editor",
	"Link", "Dynamic Link", "Select", "Read Only",
]);
const CREMA_FALLBACK_FIELD_CAP = 8;

function crema_ask_for_view(doctype, prompt) {
	frappe.call({
		method: "crema.api.ask_api",
		args: {
			interface: "view",
			prompt: `${crema_view_prompt(doctype)}\n\nRequest: ${prompt}`,
			response_json: 1,
		},
		freeze: true,
		freeze_message: __("Thinking…"),
		callback(r) {
			const data = r.message;
			if (!data) return;
			crema_apply_view_spec(doctype, data);
		},
		error: crema_show_error,
	});
}

function crema_apply_view_spec(doctype, data) {
	let spec;
	try {
		spec = JSON.parse(crema_strip_fence(data.result));
	} catch (e) {
		frappe.msgprint({
			message: __("Crema returned an unreadable view spec."),
			indicator: "red",
		});
		return;
	}

	// Same discipline as automation._validate_plan, just client-side: drop anything the
	// model invented that isn't actually a field this user may see.
	const allowed = new Set(crema_readable_fields(doctype).map((df) => df.fieldname));
	// name/creation/modified are standard docfields, never listed in meta.fields, but are
	// always valid sort fields (frappe/public/js/frappe/ui/sort_selector.js).
	const sortable = new Set([...allowed, "name", "creation", "modified"]);
	const view = CREMA_VALID_VIEWS.has(spec.view) ? spec.view : "List";

	const filters = {};
	for (const [fieldname, cond] of Object.entries(spec.filters || {})) {
		if (!allowed.has(fieldname) || !Array.isArray(cond) || cond.length !== 2) continue;
		if (!CREMA_VALID_OPERATORS.has(cond[0])) continue;
		filters[fieldname] = cond; // always [operator, value] — a bare value round-trips
		// broken through frappe.route_options (router.js JSON-stringifies it, but
		// list_view.js only JSON.parses values starting with "[").
	}
	const group_by =
		Array.isArray(spec.group_by) &&
		spec.group_by.length === 3 &&
		allowed.has(spec.group_by[0]) &&
		(spec.group_by[1] === null || allowed.has(spec.group_by[1])) &&
		CREMA_VALID_AGGREGATES.has(spec.group_by[2])
			? spec.group_by
			: null;

	const columns = Array.isArray(spec.columns) ? spec.columns.filter((f) => allowed.has(f)) : [];

	let order_by = null;
	if (typeof spec.order_by === "string") {
		const [field, dir] = spec.order_by.trim().split(/\s+/);
		if (field && sortable.has(field) && ["asc", "desc"].includes((dir || "").toLowerCase())) {
			order_by = { field, dir: dir.toLowerCase() };
		}
	}
	const page_length = Number.isInteger(spec.page_length)
		? Math.min(500, Math.max(1, spec.page_length))
		: null;
	const state = { columns, order_by, page_length };

	// Already on the target list: skip the router entirely and let one refresh() be the
	// only list query — FilterArea.set() deliberately does not refresh
	// (frappe/list/base_list.js), and get_args() reads the sort from
	// sort_selector.get_sql_string(), not sort_by, so seeding the sort/page_length before
	// that one refresh is enough; going through frappe.set_route costs a second query
	// (the route's own refresh, then on_sort_change/refresh() again). The final URL is
	// identical either way — router.js's push_state drops the query string, and
	// list_view.refresh() always rewrites it from the live filters — so this doesn't
	// change what lands in the address bar. Report/Kanban/group_by keep the route path:
	// _group_by only round-trips through frappe.route_options (report_view.js).
	const live = typeof cur_list !== "undefined" && cur_list;
	const fast = live && live.doctype === doctype && live.view_name === "List" && view === "List" &&
		live.filter_area && !group_by;

	if (fast) {
		crema_seed_list_state(live, doctype, view, state);
		const rows = crema_filter_rows(doctype, filters);
		// Clear even when the spec has no filters: "show me everything" must not inherit
		// the previous prompt's filters. list_view.js's own before_refresh() skips the
		// clear in exactly that case (its this.filters.length > 0 guard) — that quirk is
		// the bug, not the model. filter_area.get() is checked first so an already-
		// unfiltered list doesn't pay for a clear(): a standard-filter field (e.g. ToDo's
		// status) fires a debounced refresh on its own set_value() regardless of
		// clear(false)'s refresh flag, arming a spurious extra query 300ms later.
		let apply = Promise.resolve();
		if (rows.length || live.filter_area.get().length) {
			apply = live.filter_area.clear(false);
			if (rows.length) apply = apply.then(() => live.filter_area.set(rows));
		}
		apply
			.then(() => {
				live.start = 0;
				return live.refresh();
			})
			.then(() => crema_after_view(doctype, spec, filters))
			// filter_area.set/refresh rejecting would otherwise skip the reason alert and
			// the zero-result widen fallback with nothing shown at all.
			.catch(crema_show_error);
		return;
	}

	frappe.route_options = filters;
	if (group_by) frappe.route_options._group_by = JSON.stringify(group_by);
	frappe.set_route("List", doctype, view)
		.then(() => {
			const seeded = crema_seed_list_state(cur_list, doctype, view, state);
			// Same stale-filter fence as the fast path, from the other side: before_refresh()
			// only clears when the route carried filters, so a zero-filter spec would
			// otherwise inherit the previous view's (Report/Kanban/group_by share one
			// filter set with the List). A non-empty spec is already applied by then.
			const stale =
				!Object.keys(filters).length &&
				cur_list.filter_area &&
				cur_list.filter_area.get().length;
			return (stale ? cur_list.filter_area.clear(false) : Promise.resolve()).then(() => {
				if (seeded || stale) cur_list.refresh();
				crema_after_view(doctype, spec, filters);
			});
		})
		.catch(crema_show_error);
}

// {fieldname: [op, value]} -> [[doctype, fieldname, op, value], ...] — same data,
// the shape frappe.ui.FilterArea.set() (rather than frappe.route_options) expects.
function crema_filter_rows(doctype, filters) {
	return Object.entries(filters).map(([fieldname, [op, value]]) => [doctype, fieldname, op, value]);
}

// Sort/page_length/Report columns have no frappe.route_options path — they can only be
// set on the live view object. Returns whether the caller still owes it a refresh (the
// router path calls this after set_route resolves and only refreshes if something here
// actually applied; the fast path always refreshes anyway, so it ignores the return).
function crema_seed_list_state(list, doctype, view, state) {
	const on_this_view = list && list.doctype === doctype;
	let changed = false;

	if (state.columns.length && view === "Report" && on_this_view) {
		list.fields = state.columns.map((f) => [f, doctype]);
		list.build_fields();
		list.setup_columns();
		changed = true;
	}
	if (state.page_length && on_this_view) {
		list.page_length = state.page_length;
		changed = true;
	}
	// sort_selector doesn't exist on every list-family view (e.g. Kanban).
	if (state.order_by && on_this_view && list.sort_selector) {
		list.sort_selector.set_value(state.order_by.field, state.order_by.dir);
		list.sort_by = state.order_by.field;
		list.sort_order = state.order_by.dir;
		changed = true;
	}
	return changed;
}

// The literal a failed text lookup wanted — fires only for exactly ONE filter whose
// operator is "like" or "=" on a CREMA_TEXTISH readable field. Everything else (a
// number/date field, "is", "in", a multi-filter spec) -> null, and the whole fallback
// below is a no-op.
// ponytail: single-filter only. Zero rows under an AND is ambiguous about which filter
// failed — widen to "replace the text filter, keep the rest" only if real specs need it.
function crema_failed_term(doctype, filters) {
	const entries = Object.entries(filters);
	if (entries.length !== 1) return null;
	const [fieldname, [op, value]] = entries[0];
	if (op !== "like" && op !== "=") return null;

	const df = frappe.get_meta(doctype).fields.find((f) => f.fieldname === fieldname);
	if (!df || !CREMA_TEXTISH.has(df.fieldtype)) return null;

	const term = String(value ?? "").replace(/^%+|%+$/g, "");
	if (!term || term.includes("%") || term.length < 2) return null;
	return { fieldname, term };
}

// Readable text-ish fields to probe, [shown in list] first (Array#sort is stable, so
// declaration order holds within each group), capped so the OR-LIKE stays a bounded
// scan. Built from crema_readable_fields — the same permission fence, never widened.
// "name" is deliberately not probed: it is not in crema_readable_fields.
function crema_fallback_candidates(doctype) {
	const visible = crema_visible_list_fields(doctype);
	return crema_readable_fields(doctype)
		.filter((df) => CREMA_TEXTISH.has(df.fieldtype))
		.map((df) => df.fieldname)
		.sort((a, b) => (visible.has(b) ? 1 : 0) - (visible.has(a) ? 1 : 0))
		.slice(0, CREMA_FALLBACK_FIELD_CAP);
}

// The zero-result fallback: no second LLM call, one probe query. Fires only when the
// list just rendered zero rows for a single text-lookup filter.
function crema_widen_if_empty(doctype, filters) {
	if (
		typeof cur_list === "undefined" ||
		!cur_list ||
		cur_list.doctype !== doctype ||
		!Array.isArray(cur_list.data) ||
		cur_list.data.length
	) {
		return;
	}
	const failed = crema_failed_term(doctype, filters);
	if (!failed) return;

	const candidates = crema_fallback_candidates(doctype);
	if (!candidates.length) return;
	const { term } = failed;
	const label = (f) => frappe.meta.get_docfield(doctype, f)?.label || f;

	frappe.db
		.get_list(doctype, {
			fields: candidates,
			or_filters: candidates.map((f) => [f, "like", `%${term}%`]),
			limit: 20,
		})
		.then((rows) => {
			// or_filters only says a row matched, not which field matched it — count how
			// many returned rows actually contain the term per candidate; highest count
			// wins, ties break on candidate order (visible columns first).
			const needle = term.toLowerCase();
			const counts = candidates.map(
				(f) => rows.filter((r) => String(r[f] ?? "").toLowerCase().includes(needle)).length
			);
			const best_i = counts.reduce((best, c, i) => (c > counts[best] ? i : best), 0);
			if (!counts[best_i]) {
				frappe.show_alert({
					message: __('Nothing contains "{0}" in this list.', [term]),
					indicator: "orange",
				});
				return;
			}
			const best = candidates[best_i];
			cur_list.filter_area
				.clear(false)
				.then(() => cur_list.filter_area.set([[doctype, best, "like", `%${term}%`]]))
				.then(() => {
					cur_list.start = 0;
					return cur_list.refresh();
				})
				.then(() => {
					frappe.show_alert({
						message: __('No match in {0}. Showing rows where {1} contains "{2}".', [
							label(failed.fieldname),
							label(best),
							term,
						]),
						indicator: "orange",
					});
				})
				.catch(() =>
					frappe.show_alert({ message: __("Could not search other fields."), indicator: "orange" })
				);
		})
		.catch(() =>
			frappe.show_alert({ message: __("Could not search other fields."), indicator: "orange" })
		);
}

// Where both apply paths in crema_apply_view_spec converge once the list has rendered:
// the model's reason alert, then the zero-result fallback.
function crema_after_view(doctype, spec, filters) {
	if (spec.reason) frappe.show_alert({ message: spec.reason, indicator: "blue" });
	crema_widen_if_empty(doctype, filters);
}

// ---- Dialog ---------------------------------------------------------------------------

function crema_open_dialog(doctype, prefill) {
	const can_create = frappe.model.can_create(doctype);

	// Instruction first, upload second: the instruction isn't an alternative to the
	// upload, it steers it (on_success below passes it straight into extract_api) — so
	// the layout says that, instead of a label reading "Or ask a question" that implies
	// they're two unrelated choices.
	const dialog = new frappe.ui.Dialog({
		title: __("Ask Crema about {0}", [__(doctype)]),
		size: "large",
		fields: [
			{ fieldtype: "Section Break", label: __("What do you want to do?") },
			{
				fieldtype: "Small Text",
				fieldname: "instruction",
				label: __("What do you want to do?"),
				description: can_create
					? __(
							"A request changes this list view. If you also upload a document below, this text guides how the document is read."
					  )
					: undefined,
				default: prefill || "",
			},
			...(can_create
				? [
						{ fieldtype: "Section Break", label: __("Upload a document") },
						{ fieldtype: "HTML", fieldname: "upload" },
				  ]
				: []),
			{ fieldtype: "HTML", fieldname: "preview", hidden: 1 },
		],
		primary_action_label: __("Go"),
		primary_action(values) {
			if (!values.instruction) {
				frappe.msgprint(__("Type what you want, then press Go."));
				return;
			}
			dialog.hide();
			crema_ask_for_view(doctype, values.instruction);
		},
	});

	// Inline drop zone instead of a plain Attach control: passing `wrapper` (rather
	// than letting FileUploader make its own dialog) renders the widget in place, so
	// picking a file no longer pops a second modal on top of this one. How many
	// records the file holds is no longer asked up front — extract() decides that
	// itself; see crema_show_extract_preview.
	if (can_create) {
		new frappe.ui.FileUploader({
			wrapper: dialog.fields_dict.upload.$wrapper,
			disable_file_browser: true,
			allow_multiple: false,
			allow_web_link: false,
			allow_take_photo: false,
			on_success: (file_doc) => {
				dialog.fields_dict.upload.$wrapper.hide();
				crema_extract_into_new_doc(doctype, file_doc.file_url, dialog.get_value("instruction"), dialog);
			},
		});
		// FileUploader's .file-upload-area is a Vue *scoped* style (min-height: 16rem),
		// which a plain stylesheet rule can't outrank on specificity — an inline style
		// can. app.mount() inside the FileUploader constructor is synchronous, so the
		// element already exists here.
		dialog.fields_dict.upload.$wrapper.find(".file-upload-area").css("min-height", "7rem");
	}

	dialog.show();
}

// ---- Path C: instruction -> diff for the open document -----------------------------

function crema_open_transform_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Ask Crema about {0}", [__(frm.doctype)]),
		fields: [
			{
				fieldtype: "Small Text",
				fieldname: "instruction",
				label: __("What should change?"),
			},
			{ fieldtype: "HTML", fieldname: "preview", hidden: 1 },
		],
		primary_action_label: __("Go"),
		primary_action(values) {
			if (!values.instruction) {
				frappe.msgprint(__("Type what should change, then press Go."));
				return;
			}
			frappe.call({
				method: "crema.api.transform_api",
				args: { doctype: frm.doctype, name: frm.docname, instruction: values.instruction },
				freeze: true,
				freeze_message: __("Thinking…"),
				callback(r) {
					const data = r.message;
					if (!data) return;
					dialog.fields_dict.preview.df.hidden = 0;
					dialog.set_df_property(
						"preview",
						"options",
						`<div class="text-muted small">${frappe.utils.escape_html(
							data.reason || ""
						)}</div>${crema_diff_table(data)}`
					);
					dialog.refresh();
					dialog.set_primary_action(__("Apply"), () => {
						for (const [fieldname, value] of Object.entries(data.set || {})) {
							frm.set_value(fieldname, value);
						}
						for (const [table_fieldname, rows] of Object.entries(data.child_set || {})) {
							(rows || []).forEach((row) =>
								Object.assign(frm.add_child(table_fieldname), row)
							);
						}
						frm.refresh_fields();
						dialog.hide();
					});
				},
				error: crema_show_error,
			});
		},
	});
	dialog.show();
}

// ---- Entry points -----------------------------------------------------------------------

// frappe.router's "change" event fires too early to find the list view: router.route()
// calls render() without awaiting it, and ListView.setup_page's `parent.list_view = this`
// only happens inside BaseList.show()'s async chain, after a fetch_meta() round-trip. So on
// a first visit frappe.container.page.list_view is still undefined when "change" fires.
// setup_page_head runs right after that assignment (list_view.js setup_page ->
// setup_page_head), which is the seam every list-family view (List/Report/Kanban/Image/
// Calendar/Gantt/File/Map/Inbox) actually shares — wrap it like add_defaults below.
(() => {
	const setup_page_head = frappe.views.ListView.prototype.setup_page_head;
	frappe.views.ListView.prototype.setup_page_head = function () {
		setup_page_head.call(this);
		// secondary_action is null for every core list-family view (base_list.js), so the
		// .btn-secondary slot is free — but don't stomp a view that claims it for itself.
		if (!crema_allowed() || this.secondary_action) return;
		// set_secondary_action always renders "<icon> <span>{label}</span>" (page.js
		// get_icon_label), so an empty label still lays out a hidden span and the
		// button's min-width padding makes it wider than the square reload button next
		// to it. Force the same .icon-btn geometry the reload button uses instead.
		this.page
			.set_secondary_action("", () => crema_open_dialog(this.doctype), "bot")
			.addClass("icon-btn")
			.html(frappe.utils.icon("bot", "sm"))
			.attr("title", __("Ask Crema"))
			.attr("data-crema", "1");
	};
})();

// Form views: Toolbar.refresh() (form/toolbar.js) runs on every render, and unlike
// set_secondary_action (claimed by Amend/status buttons on submittable doctypes —
// toolbar.js's own set_secondary_action calls), the icon-group slot add_action_icon uses
// is free. clear_user_actions() (called at the top of refresh()) does NOT clear that
// group, so refresh() firing again on the same page would otherwise stack a second
// button — guard on a data attribute instead.
(() => {
	const refresh = frappe.ui.form.Toolbar.prototype.refresh;
	frappe.ui.form.Toolbar.prototype.refresh = function () {
		refresh.call(this);
		const frm = this.frm;
		if (
			!crema_allowed() ||
			frm.doc.__islocal ||
			!frm.perm[0]?.write ||
			this.page.wrapper.find("[data-crema-form]").length
		) {
			return;
		}
		this.page
			.add_action_icon("bot", () => crema_open_transform_dialog(frm), "", __("Ask Crema"))
			.attr("data-crema-form", "1");
	};
})();

// No dedicated hook for awesomebar options exists, so wrap the method it builds its
// default suggestions from — the standard way to extend it from outside frappe/core.
(() => {
	const add_defaults = frappe.search.AwesomeBar.prototype.add_defaults;
	frappe.search.AwesomeBar.prototype.add_defaults = function (txt) {
		add_defaults.call(this, txt);
		if (!txt || txt.charAt(0) === "#" || !crema_allowed()) return;
		const doctype = frappe.container.page?.list_view?.doctype;
		if (!doctype) return;
		this.options.push({
			label: `<span class="ellipsis">${__("Ask {0}", [
				frappe.utils.xss_sanitise(txt).bold(),
			])}</span>`,
			value: __("Ask {0}", [frappe.utils.xss_sanitise(txt)]),
			match: txt,
			index: 99, // directly under "Search for …" (index 100)
			default: "Ask",
			onclick: () => crema_ask_for_view(doctype, txt),
		});
	};
})();

// ---- Shared helpers — used by the Crema Settings form and by the Crema Provider
// doctype JS, so the model list fetch and the template dialog exist in exactly one
// place. Exported on `window`: esbuild's *.bundle.js output wraps this whole file in
// an IIFE, so a plain top-level `function` here is module-scoped and invisible to
// crema_settings.js / crema_provider_list.js, which are evaluated as separate
// scripts and can only reach it through the global object. ------------------------

function crema_fetch_models(provider, callback) {
	if (!provider) {
		callback([]);
		return;
	}
	frappe.call({
		method: "crema.api.get_models",
		args: { provider },
		callback(r) {
			callback(r.message || []);
		},
		// Same fate as an empty model list — the caller (crema_settings.js) already
		// handles that by leaving the Autocomplete's data unset, and unlike a request the
		// user just triggered, no message is warranted for a background list load.
		error() {
			callback([]);
		},
	});
}

function crema_new_provider_dialog(on_created) {
	// A wizard, not a template picker that dumps the admin on an empty form: template
	// -> name/base URL prefill -> paste the key -> optionally set as default, all in
	// one step, backed by crema_provider.create_from_template.
	frappe.call({
		method: "crema.crema.doctype.crema_provider.crema_provider.get_presets",
		// Nothing here calls dialog.show() unless this succeeds — without an error handler
		// the button just does nothing at all on failure. The dialog needs preset_names to
		// build its fields, so there's nothing useful to open here; just say why it didn't.
		error: crema_show_error,
		callback(r) {
			const presets = r.message || {};
			const preset_names = Object.keys(presets);
			const dialog = new frappe.ui.Dialog({
				title: __("New Provider from Template"),
				fields: [
					{
						fieldname: "preset",
						fieldtype: "Select",
						label: __("Template"),
						options: preset_names.join("\n"),
						default: preset_names[0],
						reqd: 1,
						change() {
							const preset = dialog.get_value("preset");
							dialog.set_value("provider_name", preset);
							dialog.set_value("base_url", presets[preset] || "");
						},
					},
					{
						fieldname: "provider_name",
						fieldtype: "Data",
						label: __("Provider Name"),
						default: preset_names[0],
						reqd: 1,
						description: __("A second key for the same vendor needs its own name."),
					},
					{
						fieldname: "base_url",
						fieldtype: "Data",
						label: __("Base URL"),
						default: presets[preset_names[0]] || "",
						reqd: 1,
					},
					{ fieldname: "api_key", fieldtype: "Password", label: __("API Key") },
					{ fieldname: "enabled", fieldtype: "Check", label: __("Enabled"), default: 1 },
					{
						fieldname: "set_as_default",
						fieldtype: "Check",
						label: __("Set as default provider"),
						default: 1,
					},
				],
				primary_action_label: __("Create"),
				primary_action(values) {
					frappe.call({
						method: "crema.crema.doctype.crema_provider.crema_provider.create_from_template",
						args: values,
						freeze: true,
						callback() {
							dialog.hide();
							if (on_created) on_created();
						},
						error: crema_show_error,
					});
				},
			});
			dialog.show();
		},
	});
}

window.crema_fetch_models = crema_fetch_models;
window.crema_new_provider_dialog = crema_new_provider_dialog;
// crema_automation_task.js is likewise a separate script, evaluated outside this IIFE.
window.crema_show_error = crema_show_error;
