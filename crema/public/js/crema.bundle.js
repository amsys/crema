// Crema desk UI — a robot button on doctype list views, a robot button on doctype form
// views, and an "Ask …" option in the awesomebar. The list-view button opens a dialog
// that does two things: read an uploaded document (proposing one or more new records),
// or turn a typed request into one of five actions — change the List/Report/Kanban
// view, create one or more new records, edit records matched by a filter, delete
// records matched by a filter, or an honest refusal when none of those fit. Which the
// model picked is the "action" key in its JSON answer; see crema_view_prompt for the
// contract. The form-view button opens a dialog that proposes a diff for the document
// already open, for the user to apply and save themselves.
//
// Deliberately almost all client-side. Reading the schema (frappe.get_meta), applying
// filters (frappe.route_options), inserting a record (frappe.model.get_new_doc, saved by
// the user), applying a diff (frm.set_value/add_child, saved by the user), and a bulk
// edit/delete (frappe's own bulk_update/delete_items endpoints, after a confirm dialog
// listing every affected record) all go through ordinary desk paths, fenced as the
// session user — the server is only ever asked to run the LLM (crema.api.ask_api /
// extract_api / transform_api). See the "implement-in-desk-interface" plan for why: a
// server-side endpoint doing this would run under the interface's isolation user, not
// the desk user who clicked. The create/edit/delete affordance checks below
// (frappe.model.can_create, frappe.perm.has_perm) are UI honesty, not the fence — the
// server enforces on save/delete regardless of what this file lets through.

function crema_allowed() {
	return (
		frappe.user_roles.includes("Crema User") || frappe.user_roles.includes("System Manager")
	);
}

function crema_strip_fence(text) {
	const m = /^```(?:json)?([\s\S]*)```$/.exec((text || "").trim());
	return m ? m[1].trim() : text;
}

// The site's own red-line doctypes (Crema Settings' Blocked Doctypes table, published
// to frappe.boot by crema.policy.extend_bootinfo). An interface's own isolation user
// might still be able to write one of these; the desk assistant's write actions never
// may. Read-only browsing (the "view" action) is unaffected — this only gates
// create/edit/delete.
function crema_blocked(doctype) {
	return (frappe.boot.crema_blocked_doctypes || []).includes(doctype);
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

// Fields this session user may WRITE — the create/edit fence, one notch tighter than
// crema_readable_fields above. Two corrections a naive version of this gets wrong:
//   * get_field_display_status(df, null, perm) returns BEFORE its own read_only
//     demotion when doc is null (frappe/public/js/frappe/model/perm.js: the "Write" ->
//     "Read" downgrade for a read-only field only runs if a doc was passed in), so it
//     reports "Write" for every read-only field — amended_from included. Checked here
//     instead, alongside frappe.model.is_value_type/hidden/is_virtual, the same
//     editability test list_view.js's own Bulk Edit menu uses.
//   * perm is a PARAMETER, not refetched per call: a child doctype carries no DocPerm
//     rows of its own, so frappe.perm.get_perm(child_doctype) returns read:0 for
//     everything and would silently drop every child row. A child docfield's permlevel
//     indexes into the PARENT's perm array — the same array grid.js passes in when it
//     builds a grid column's display status.
function crema_writable_fields(doctype, perm) {
	const meta = frappe.get_meta(doctype);
	// A child doctype's meta only arrives bundled with its parent's
	// (frappe.desk.form.load.get_meta_bundle, one level deep) and frappe.model.with_doctype
	// short-circuits on a doctype already in locals — so it can genuinely be missing here.
	// Fail closed: no meta means no field is provably writable. Same fail-closed shape
	// for a doctype this site has blocked outright.
	if (!meta || crema_blocked(doctype)) return [];
	return meta.fields.filter(
		(df) =>
			df.fieldname &&
			frappe.model.is_value_type(df) &&
			!df.hidden &&
			!df.is_virtual &&
			!df.read_only &&
			df.fieldtype !== "Read Only" &&
			frappe.perm.get_field_display_status(df, null, perm) === "Write"
	);
}

// The Table half of the write fence: crema_writable_fields only sees value-type fields
// (frappe.model.is_value_type excludes "Table"), so a Table docfield is never in its set —
// this is the question that set structurally can't answer. Returns the parent's own
// docfield when this user may write rows into it, else null.
function crema_writable_table(doctype, fieldname, perm) {
	const df = frappe.meta.get_docfield(doctype, fieldname);
	if (df?.fieldtype !== "Table" || df.hidden || df.read_only) return null;
	if (crema_blocked(doctype) || crema_blocked(df.options)) return null;
	return frappe.perm.get_field_display_status(df, null, perm) === "Write" ? df : null;
}

// api._filter_diff (crema/api.py) client-side, fenced on writable rather than merely
// existing fields. Needed because the create/edit actions' answers come back from the
// "view" interface's raw ask_api call, which — unlike extract_api/transform_api — never
// runs through that server-side filter. Drops "name": it is a standard docfield, never
// listed in meta.fields, so it is never "writable" here — which is what stops a
// model-authored set.name from reaching a bulk update's doc.update(data) and silently
// retargeting the save onto a different, unrelated record (the same footgun
// automation._upsert guards against). Must run after frappe.model.with_doctype has
// loaded the parent and every child doctype's meta.
function crema_filter_diff(doctype, raw) {
	const perm = frappe.perm.get_perm(doctype);
	const writable = new Set(crema_writable_fields(doctype, perm).map((df) => df.fieldname));
	const set = Object.fromEntries(Object.entries(raw.set || {}).filter(([k]) => writable.has(k)));

	const child_set = {};
	for (const [table, rows] of Object.entries(raw.child_set || {})) {
		const df = crema_writable_table(doctype, table, perm);
		if (!df || !Array.isArray(rows)) continue;
		const child_writable = new Set(
			crema_writable_fields(df.options, perm).map((c) => c.fieldname)
		);
		const filtered = rows
			.filter((r) => r && typeof r === "object")
			.map((r) =>
				Object.fromEntries(Object.entries(r).filter(([k]) => child_writable.has(k)))
			)
			.filter((r) => Object.keys(r).length); // an all-invented row is no row at all
		if (filtered.length) child_set[table] = filtered;
	}
	return { set, child_set };
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
	if (
		typeof cur_list !== "undefined" &&
		cur_list &&
		cur_list.doctype === doctype &&
		cur_list.columns
	) {
		return new Set(cur_list.columns.filter((c) => c.df?.fieldname).map((c) => c.df.fieldname));
	}
	const meta = frappe.get_meta(doctype);
	const fields = meta.fields.filter((df) => df.in_list_view).map((df) => df.fieldname);
	fields.push("name");
	if (meta.title_field) fields.push(meta.title_field);
	return new Set(fields);
}

// The Table half of crema_field_line: crema_writable_fields excludes Table
// (frappe.model.is_value_type), so it is never in the plain `writable` set and would
// otherwise read [read-only] — contradicting the child_set shape the action contract
// advertises for this same field.
function crema_table_line(doctype, df, perm) {
	const table_df = perm ? crema_writable_table(doctype, df.fieldname, perm) : null;
	if (!table_df) return " [read-only]";
	// Reuse the PARENT's perm array, same as crema_filter_diff does — a child doctype
	// carries no DocPerm rows of its own, so frappe.perm.get_perm(child) always comes
	// back permission-less and every child field reads as unwritable.
	const child_fields = crema_writable_fields(table_df.options, perm);
	const preferred = child_fields.filter((c) => c.in_list_view);
	const names = (preferred.length ? preferred : child_fields)
		.slice(0, CREMA_MAX_CHILD_FIELDS)
		.map((c) => c.fieldname);
	return names.length ? ` [rows: ${names.join(", ")}]` : " [read-only]";
}

// One line of crema_view_prompt's field listing: name, type, a Link/Select's option
// list, whether it is shown in the open list, and whether (and how) it may be written.
function crema_field_line(doctype, df, { visible, writable, perm }) {
	const shown = visible.has(df.fieldname) ? " [shown in list]" : "";
	// A Link/Select value the model has to guess with no target doctype or option
	// list will never match — give it what it needs to pick a real one.
	let opts = "";
	if (df.fieldtype === "Link") {
		opts = ` -> ${df.options}`;
	} else if (df.fieldtype === "Select") {
		opts = ` [${(df.options || "").split("\n").filter(Boolean).join("|")}]`;
	}
	let write = "";
	if (df.fieldtype === "Table") {
		write = crema_table_line(doctype, df, perm);
	} else if (writable) {
		if (writable.has(df.fieldname)) {
			write = df.reqd ? " [required]" : "";
		} else {
			write = " [read-only]";
		}
	}
	return `- ${df.fieldname} (${df.fieldtype})${opts} ${df.label || ""}${shown}${write}`.trim();
}

// interfaces.DEFAULT_PROMPTS["view"] is now just a role statement — the output contract
// itself lives here, in the per-request user turn (see patches/refresh_view_prompt.py
// for why: a system prompt only seeds once, at install time, and never sees a later
// edit). This also tells the model which fields it may use, which are visible on
// screen, which it may WRITE (create/edit/delete each only appear in the contract when
// the session user actually has that permission on doctype), and the operator
// vocabulary list filters actually support (frappe/public/js/frappe/ui/filters/filter.js)
// — without this the model has no basis for picking a field for an unqualified request
// like "items starting with foo", and no way to know "like" is its only substring tool.
function crema_view_prompt(doctype) {
	const visible = crema_visible_list_fields(doctype);
	const blocked = crema_blocked(doctype);
	const can_create = !blocked && frappe.model.can_create(doctype);
	const can_write = !blocked && frappe.perm.has_perm(doctype, 0, "write");
	const can_delete = !blocked && frappe.perm.has_perm(doctype, 0, "delete");
	// Writability only matters for create/edit; skip computing it for a read-only user —
	// same reasoning crema_readable_fields' comment gives for staying permlevel-aware.
	// Hoisted so crema_table_line can ask the same question with the same perm array,
	// rather than each recomputing frappe.perm.get_perm(doctype).
	const perm = can_create || can_write ? frappe.perm.get_perm(doctype) : null;
	const writable = perm
		? new Set(crema_writable_fields(doctype, perm).map((df) => df.fieldname))
		: null;

	const lines = crema_readable_fields(doctype).map((df) =>
		crema_field_line(doctype, df, { visible, writable, perm })
	);
	// "name" is a standard docfield, never in meta.fields above, but crema_valid_filters
	// accepts it as a filter target (the ID is the list's own first column) — listed here,
	// read-only, so the model knows the field exists without being told it can write it.
	lines.unshift("- name (Data) ID [shown in list] [read-only]");

	// actions/shapes/rules grow together, one array entry per action the model may pick
	// — the three permission `if`s below are the single place each optional entry is
	// decided, rather than every caller of Array#join re-asking the same question as a
	// ternary. Order matters: it is the order the model sees them in.
	const actions = ['"view" — change what this list shows.'];
	const shapes = [
		'{"action": "view", "view": "List"|"Report"|"Kanban", ' +
			'"filters": {fieldname: [operator, value]}, ' +
			'"group_by": [fieldname, aggregate_fieldname, "count"|"sum"|"avg"] or null, ' +
			'"order_by": "fieldname asc"|"fieldname desc" or null, "page_length": integer or null, ' +
			'"columns": [fieldname, ...], "reason": "..."}',
	];
	const rules = [
		"- If the request does not name a field, filter on a field marked [shown in list] " +
			"(prefer a Data/Text field over a Link/Select).",
		'- "starting with X" -> ["like", "X%"]. "containing X" or "with X" -> ["like", "%X%"]. ' +
			'"ending with X" -> ["like", "%X"].',
		"- Text matching is already case-insensitive on this system — never try to force a " +
			"case-sensitive match, and do not mention case in the reason.",
		'- Legal operators: "=", "!=", "like", "not like", "in", "not in", "is", ">", "<", ' +
			'">=", "<=", "Between", "Timespan". "is" takes only "set" or "not set" as its value. ' +
			'"Timespan" takes a relative phrase such as "last week", "yesterday", "this month". ' +
			'"in"/"not in" take a list of values.',
		"- Every filter is combined with AND — there is no way to OR across different fields. " +
			"If the request implies an OR across fields, say so plainly in the reason instead of " +
			"approximating it with AND.",
		'- order_by sorts the whole list: "fieldname asc" or "fieldname desc".',
		'- page_length is an integer row limit for requests like "top 10" or "first 5".',
		'- columns only take effect when view is "Report" — set view to "Report" for a ' +
			"request about which columns are shown.",
		'- group_by requires view to be "Report" too.',
		"- This works only on the doctype above — a request naming a different doctype is " +
			'"none".',
		'- A request to find or open one record is still "view": filter tightly enough ' +
			'that one row matches, do not use "create" or "edit" for it.',
	];

	if (can_create) {
		actions.push(
			'"create" — make one or more new, unsaved records. Use every value the request ' +
				"states and invent nothing else. Never set a [read-only] field."
		);
		shapes.push(
			'{"action": "create", "records": [{"set": {fieldname: value}, ' +
				'"child_set": {table_fieldname: [row dicts]}}, ...], "reason": "..."}'
		);
		rules.push(
			'- A create request naming several records lists them all under "records" in ' +
				"one create action; whether a required field is missing is checked when each " +
				"record is created, not by you."
		);
	}
	if (can_write) {
		actions.push(
			'"edit" — change existing records matched by "filters". child_set is honored ' +
				"only when the filters match exactly one record."
		);
		shapes.push(
			'{"action": "edit", "filters": {fieldname: [operator, value]}, ' +
				'"set": {fieldname: value}, "child_set": {table_fieldname: [row dicts]}, ' +
				'"reason": "..."}'
		);
	}
	if (can_delete) {
		actions.push('"delete" — remove existing records matched by "filters".');
		shapes.push(
			'{"action": "delete", "filters": {fieldname: [operator, value]}, "reason": "..."}'
		);
		rules.push(
			'- "delete the ones that are X" is one delete action with filters, not a view.'
		);
	}
	actions.push(
		'"none" — anything this list cannot do: a bulk export, sending mail, a question ' +
			"with no view that answers it, a request about a different doctype, or several " +
			"actions in one request. Say plainly, in one sentence, what you cannot do and " +
			"what the user should do instead. Never approximate a request you cannot serve."
	);
	shapes.push('{"action": "none", "reason": "..."}');

	if ((can_create || can_write) && lines.some((l) => l.includes("[rows: "))) {
		rules.push(
			"- A child_set row for a table field may only use the fieldnames listed after " +
				'"rows:" for that field — invent nothing else.'
		);
	}
	rules.push("- Pick exactly one action, even for a request that asks for more than one thing.");

	return (
		`Fields of doctype '${doctype}':\n${lines.join("\n")}\n\n` +
		'Output ONLY one JSON object. Its "action" key picks exactly one of:\n' +
		actions.map((a) => `- ${a}`).join("\n") +
		"\n\n" +
		shapes.join("\n") +
		"\n\n" +
		"Rules for building the view specification:\n" +
		rules.join("\n")
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
			message: frappe.utils.escape_html(data.reason),
			indicator: "red",
		});
		return;
	}
	// frappe's own statusCode handlers (403/404/413/500/504/508) already msgprint
	// something and then call error_callback with no arguments — only 417 passes a body
	// through, and it renders nothing on its own. So `r` present without a `reason` is
	// still a silent case and needs a message; `r` absent means frappe already spoke.
	if (r) {
		frappe.msgprint({
			message: __("Crema could not complete this request."),
			indicator: "red",
		});
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
	const empty_row = `<tr><td colspan="2">${__("Nothing found")}</td></tr>`;
	return `<table class="table table-bordered"><thead><tr><th>${__("Field")}</th><th>${__(
		"Value"
	)}</th></tr></thead>${rows || empty_row}</table>`;
}

// Both create paths end here — a record read out of an uploaded document (Path A), and
// a record described in a typed request (the "create" action, below). Fills an unsaved
// form and routes to it; nothing is written until the user saves. Must run inside
// frappe.model.with_doctype: get_new_doc and add_child both read frappe.get_meta, for
// the parent and for each child doctype a child_set row belongs to.
function crema_open_new_doc(doctype, record) {
	const doc = frappe.model.get_new_doc(doctype);
	Object.assign(doc, record.set || {});
	for (const [fieldname, rows] of Object.entries(record.child_set || {})) {
		(rows || []).forEach((row) => Object.assign(frappe.model.add_child(doc, fieldname), row));
	}
	frappe.set_route("Form", doctype, doc.name);
}

// Both edit paths end here — the single-record "edit" action (below) and the form-view
// transform dialog (Path C). frm may be undefined (the record isn't the one currently
// open, or isn't open at all): fall back to mutating the doc in locals directly via
// frappe.model, the same locals-first trick crema_open_new_doc already relies on for a
// brand new doc, so the Form these route to opens already showing the change — routing
// AFTER the mutation sidesteps ever having to know when frappe.set_route's promise means
// "the form finished rendering". Precondition when frm is omitted: the doc must already
// be in locals (frappe.model.with_doc(doctype, name) resolved) — frappe.model.set_value
// silently no-ops on a doc that isn't loaded yet.
function crema_apply_diff(doctype, name, diff, frm) {
	for (const [fieldname, value] of Object.entries(diff.set || {})) {
		if (frm) frm.set_value(fieldname, value);
		else frappe.model.set_value(doctype, name, fieldname, value);
	}
	for (const [table_fieldname, rows] of Object.entries(diff.child_set || {})) {
		const target = frm ? frm.doc : frappe.get_doc(doctype, name);
		(rows || []).forEach((row) =>
			Object.assign(frappe.model.add_child(target, table_fieldname), row)
		);
	}
	if (frm) frm.refresh_fields();
	else frappe.get_doc(doctype, name).__unsaved = 1;
}

// ---- Path A: file -> new document(s) ------------------------------------------------

// extract() can hold a web worker for minutes on a scanned file (OCR, its advanced_ocr
// escalation, then the extraction call itself) — long enough that freezing the desk for
// it starves everyone else on a small bench, not just the user waiting. So this path
// goes through crema.api.extract_async, which only queues the job and returns a
// request_id immediately; the answer arrives later as a crema_extract realtime event.
// One listener for the whole desk session reconnects that event back to the dialog and
// doctype it was reading into, via this map — request_id -> {doctype, dialog}. The
// dialog object survives dialog.hide(): frappe.ui.Dialog only toggles the Bootstrap
// modal's visibility, so re-populating its preview field and calling .show() again
// later reopens the same dialog with the result.
const CREMA_PENDING_EXTRACTS = new Map();

// Lazy, not a bare top-level frappe.realtime.on() call: RealTimeClient.on() (frappe
// core's socketio_client.js) is a silent no-op until frappe.realtime.init() has run,
// and that happens in Application.startup(), on $(document).ready — AFTER every
// app_include_js bundle's own top-level code, this file included, has already been
// evaluated as a <script> tag. Registering here instead, on the first extract, means
// boot has always long finished by the time this runs.
let crema_extract_listener_registered = false;

function crema_ensure_extract_listener() {
	if (crema_extract_listener_registered) return;
	crema_extract_listener_registered = true;
	frappe.realtime.on("crema_extract", (data) => {
		const pending = CREMA_PENDING_EXTRACTS.get(data.request_id);
		if (!pending) return; // a stale/foreign event, or this tab wasn't the one that started it
		CREMA_PENDING_EXTRACTS.delete(data.request_id);
		if (!data.ok) {
			crema_show_error({ message: data });
			return;
		}
		pending.dialog.show();
		crema_show_extract_preview(pending.doctype, data.result, pending.dialog);
	});
}

function crema_extract_into_new_doc(doctype, file_url, instruction, dialog) {
	crema_ensure_extract_listener();
	frappe.call({
		method: "crema.api.extract_async",
		args: { doctype, file_url, instruction },
		callback(r) {
			const request_id = r.message;
			if (!request_id) return;
			CREMA_PENDING_EXTRACTS.set(request_id, { doctype, dialog });
			dialog.hide();
			frappe.show_alert(__("Reading document…"));
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

	const csv_cell = (v) => `"${String(v ?? "").replaceAll('"', '""')}"`;
	const lines = [columns.map(csv_cell).join(",")];

	records.forEach((r) => {
		const child_rows_by_table = Object.entries(child_tables).map(([table, fields]) => ({
			table,
			fields,
			rows: r.child_set?.[table] || [],
		}));
		const max_rows = Math.max(1, ...child_rows_by_table.map((t) => t.rows.length));

		for (let i = 0; i < max_rows; i++) {
			const line = columns.map((col) => {
				if (i > 0) {
					// Every further row is a child-only continuation — parent columns blank.
					if (parent_fields.includes(col)) return csv_cell("");
				} else if (parent_fields.includes(col)) {
					return csv_cell(r.set?.[col]);
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
				frappe.msgprint({
					message: __("Could not upload the generated file."),
					indicator: "red",
				});
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
			frappe.msgprint({
				message: __("Could not upload the generated file."),
				indicator: "red",
			});
		});
}

function crema_show_extract_preview(doctype, data, dialog) {
	const records = data.records || [];
	const header = `<div class="text-muted small">${__("Confidence")}: ${(
		data.confidence ?? 0
	).toFixed(2)} — ${frappe.utils.escape_html(data.reason || "")}</div>`;

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
				dialog.hide();
				// The server already ran this record through api._filter_diff (extract() ->
				// _filter_diff); running it through the client fence too is free and closes
				// the read_only/permlevel gap that server-side filter doesn't check.
				crema_open_new_doc(doctype, crema_filter_diff(doctype, records[0]));
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
	"=",
	"!=",
	"like",
	"not like",
	"in",
	"not in",
	"is",
	">",
	"<",
	">=",
	"<=",
	"Between",
	"Timespan",
]);
const CREMA_VALID_AGGREGATES = new Set(["count", "sum", "avg"]);

// MariaDB/Postgres LIKE treat "_" as a single-character wildcard and "\" as the escape
// character, but crema_view_prompt only ever advertises "%" as a wildcard — so a value
// the model meant literally ("starting with _" -> ["like", "_%"]) silently matched every
// record. Escape everything the model did not ask to be a wildcard; "%" is left alone.
function crema_like_escape(value) {
	return String(value ?? "").replace(/[\\_]/g, String.raw`\$&`);
}

// Fieldtypes worth probing when a text lookup came back empty. Link/Select are in on
// purpose — "todos for acme" often means a Link value, not a Data field.
const CREMA_TEXTISH = new Set([
	"Data",
	"Small Text",
	"Text",
	"Long Text",
	"Text Editor",
	"Link",
	"Dynamic Link",
	"Select",
	"Read Only",
]);
const CREMA_FALLBACK_FIELD_CAP = 8;
// Words worth trying individually when the whole failed term matches nowhere — capped
// so the per-word probe's or_filters (candidates x words) stays a bounded scan, same
// reasoning as CREMA_FALLBACK_FIELD_CAP above.
const CREMA_FALLBACK_WORD_CAP = 4;
// How many child-table fieldnames crema_view_prompt lists per table field — a wide child
// doctype (e.g. Sales Invoice Item) would otherwise balloon every prompt built against it.
const CREMA_MAX_CHILD_FIELDS = 10;

// Same discipline as automation._validate_plan, just client-side: drop any filter whose
// fieldname isn't readable, whose condition isn't a well-formed [operator, value] pair,
// or whose operator isn't in the whitelist. Shared by the view action and by edit/
// delete's target resolution below — "delete the ones that are X" must not silently
// expand into "every record" because a stray or malformed key survived. "name" is added
// to the readable set here (not in crema_readable_fields itself) — it's a standard
// docfield never listed in meta.fields, but it is the list's own first column and is
// already a valid order_by target (crema_apply_view_spec). It stays out of the WRITE
// fence (crema_filter_diff) on purpose: filterable, never writable.
function crema_valid_filters(doctype, raw) {
	const allowed = new Set([...crema_readable_fields(doctype).map((df) => df.fieldname), "name"]);
	const filters = {};
	for (const [fieldname, cond] of Object.entries(raw || {})) {
		if (!allowed.has(fieldname) || !Array.isArray(cond) || cond.length !== 2) continue;
		if (!CREMA_VALID_OPERATORS.has(cond[0])) continue;
		const [op, value] = cond;
		filters[fieldname] =
			op === "like" || op === "not like" ? [op, crema_like_escape(value)] : cond;
		// always [operator, value] — a bare value round-trips broken through
		// frappe.route_options (router.js JSON-stringifies it, but list_view.js only
		// JSON.parses values starting with "[").
	}
	if (Object.keys(filters).length !== Object.keys(raw || {}).length) {
		frappe.show_alert({
			message: __("Crema named a condition this list cannot use — it was ignored."),
			indicator: "orange",
		});
	}
	return filters;
}

// with_doctype ensures every child (Table) doctype's meta is loaded before the prompt is
// built — crema_view_prompt's row-schema branch needs frappe.get_meta(child) populated,
// and without this it silently falls back to "[read-only]" depending on whether some
// earlier, unrelated call happened to have loaded that child doctype already.
function crema_ask(doctype, prompt) {
	frappe.model.with_doctype(doctype, () => {
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
				crema_apply_spec(doctype, data);
			},
			error: crema_show_error,
		});
	});
}

// The "view" interface answers with one of five shapes, discriminated by "action" — see
// crema_view_prompt for the contract. A missing action means "view": a site whose stored
// Crema Model Assignment "view" row still carries an admin-customized system prompt
// written before the contract moved into the user turn (patches/refresh_view_prompt.py
// only rewrites a row matching a crema default byte-for-byte) may still steer a model
// toward the old single-shape answer, and that answer is still a valid view spec.
function crema_apply_spec(doctype, data) {
	let spec;
	try {
		spec = JSON.parse(crema_strip_fence(data.result));
	} catch (e) {
		frappe.msgprint({ message: __("Crema returned an unreadable answer."), indicator: "red" });
		return;
	}
	if (spec.action === "create") return crema_apply_create_spec(doctype, spec);
	if (spec.action === "edit") return crema_apply_edit_spec(doctype, spec);
	if (spec.action === "delete") return crema_apply_delete_spec(doctype, spec);
	if (spec.action === "none") {
		// A refusal is the honest answer, not a failure — orange, and a msgprint rather
		// than an alert (which crema_apply_view_spec's reason gets) because it is the
		// whole response, not a footnote to a view that changed.
		frappe.msgprint({
			title: __("Crema"),
			message:
				frappe.utils.escape_html(spec.reason || "") || __("Crema cannot do that here."),
			indicator: "orange",
		});
		return;
	}
	crema_apply_view_spec(doctype, spec);
}

// Everything the model authored is validated here and nowhere else: view, filters,
// group_by, columns, order_by, page_length, each checked against the readable-field
// fence and its own legal shape before it ever reaches a live list or a route.
function crema_view_state(doctype, spec) {
	// name/creation/modified are standard docfields, never listed in meta.fields, but are
	// always valid sort fields (frappe/public/js/frappe/ui/sort_selector.js).
	const allowed = new Set(crema_readable_fields(doctype).map((df) => df.fieldname));
	const sortable = new Set([...allowed, "name", "creation", "modified"]);
	const view = CREMA_VALID_VIEWS.has(spec.view) ? spec.view : "List";

	const filters = crema_valid_filters(doctype, spec.filters);
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

	return { view, filters, group_by, columns, order_by, page_length };
}

// Already on the target list: skip the router entirely and let one refresh() be the
// only list query — FilterArea.set() deliberately does not refresh
// (frappe/list/base_list.js), and get_args() reads the sort from
// sort_selector.get_sql_string(), not sort_by, so seeding the sort/page_length before
// that one refresh is enough; going through frappe.set_route costs a second query
// (the route's own refresh, then on_sort_change/refresh() again). The final URL is
// identical either way — router.js's push_state drops the query string, and
// list_view.refresh() always rewrites it from the live filters — so this doesn't
// change what lands in the address bar.
function crema_refresh_live(live, doctype, view, state, filters, spec) {
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
}

// The router path: a doctype change, Report/Kanban, or a group_by — _group_by only
// round-trips through frappe.route_options (report_view.js).
function crema_route_to_view(doctype, view, state, filters, group_by, spec) {
	frappe.route_options = filters;
	if (group_by) frappe.route_options._group_by = JSON.stringify(group_by);
	frappe
		.set_route("List", doctype, view)
		.then(() => {
			const seeded = crema_seed_list_state(cur_list, doctype, view, state);
			// Same stale-filter fence as the fast path, from the other side: before_refresh()
			// only clears when the route carried filters, so a zero-filter spec would
			// otherwise inherit the previous view's (Report/Kanban/group_by share one
			// filter set with the List). A non-empty spec is already applied by then.
			const stale = !Object.keys(filters).length && cur_list.filter_area?.get().length;
			return (stale ? cur_list.filter_area.clear(false) : Promise.resolve()).then(() => {
				if (seeded || stale) cur_list.refresh();
				crema_after_view(doctype, spec, filters);
			});
		})
		.catch(crema_show_error);
}

function crema_apply_view_spec(doctype, spec) {
	const { view, filters, group_by, columns, order_by, page_length } = crema_view_state(
		doctype,
		spec
	);
	const state = { columns, order_by, page_length };

	const live = typeof cur_list !== "undefined" && cur_list;
	const fast =
		live &&
		live.doctype === doctype &&
		live.view_name === "List" &&
		view === "List" &&
		live.filter_area &&
		!group_by;

	if (fast) {
		crema_refresh_live(live, doctype, view, state, filters, spec);
	} else {
		crema_route_to_view(doctype, view, state, filters, group_by, spec);
	}
}

// {fieldname: [op, value]} -> [[doctype, fieldname, op, value], ...] — same data,
// the shape frappe.ui.FilterArea.set() (rather than frappe.route_options) expects.
function crema_filter_rows(doctype, filters) {
	return Object.entries(filters).map(([fieldname, [op, value]]) => [
		doctype,
		fieldname,
		op,
		value,
	]);
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

	// crema_valid_filters already ran this value through crema_like_escape — undo it to
	// get back the literal the user actually typed before probing other fields with it.
	let bare = String(value ?? "");
	while (bare.startsWith("%")) bare = bare.slice(1);
	while (bare.endsWith("%")) bare = bare.slice(0, -1);
	const term = bare.replace(/\\([\\_])/g, "$1");
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

// or_filters only says a row matched, not which field matched it — this is how both
// probe stages below turn a flat row list back into "which candidate actually held
// `needle`": count real per-field substring hits, highest count wins, ties break on
// candidate order (visible columns first, from crema_fallback_candidates).
function crema_rank_candidate(candidates, rows, needle) {
	const counts = candidates.map(
		(f) =>
			rows.filter((r) =>
				String(r[f] ?? "")
					.toLowerCase()
					.includes(needle)
			).length
	);
	const best_i = counts.reduce((best, c, i) => (c > counts[best] ? i : best), 0);
	return { field: candidates[best_i], count: counts[best_i] };
}

// The failed term split into words worth trying individually — "Acme Corp Ltd" against
// a record named "Acme Corporation" fails a whole-term substring probe outright, but
// "Acme" alone finds it. Words under 2 characters are noise (a stray "a"/"of" would
// match almost every row and win on volume, not relevance).
function crema_term_words(term) {
	return term
		.split(/\s+/)
		.filter((w) => w.length >= 2)
		.slice(0, CREMA_FALLBACK_WORD_CAP);
}

// The clear-then-set-then-refresh idiom both probe stages below apply their winning
// filter through, plus the alert that names what happened — list_view.js's own
// before_refresh() uses the same clear-then-set shape.
function crema_apply_widened_filter(doctype, fieldname, operator, value, message) {
	cur_list.filter_area
		.clear(false)
		.then(() => cur_list.filter_area.set([[doctype, fieldname, operator, value]]))
		.then(() => {
			cur_list.start = 0;
			return cur_list.refresh();
		})
		.then(() => frappe.show_alert({ message, indicator: "orange" }))
		.catch(() =>
			frappe.show_alert({
				message: __("Could not search other fields."),
				indicator: "orange",
			})
		);
}

// The zero-result fallback: no second LLM call. Fires only when the list just rendered
// zero rows for a single text-lookup filter. One probe for the whole term (exact match
// is free from its results — a `=` hit is also a substring hit — falling back to
// substring); a second probe, word by word, only if that first one matches nowhere.
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
	const needle = term.toLowerCase();

	const probe = (words) =>
		frappe.db.get_list(doctype, {
			fields: candidates,
			or_filters: candidates.flatMap((f) =>
				words.map((w) => [f, "like", `%${crema_like_escape(w)}%`])
			),
			limit: 20,
		});

	const giveUp = () =>
		frappe.show_alert({
			message: __('Nothing contains "{0}" in this list.', [term]),
			indicator: "orange",
		});

	probe([term])
		.then((rows) => {
			const { field, count } = crema_rank_candidate(candidates, rows, needle);
			if (count) {
				// A row whose winning field equals the term outright is a tighter filter
				// than the substring probe that found it, at no extra query.
				const exact = rows.some((r) => String(r[field] ?? "").toLowerCase() === needle);
				const [operator, value] = exact
					? ["=", term]
					: ["like", `%${crema_like_escape(term)}%`];
				crema_apply_widened_filter(
					doctype,
					field,
					operator,
					value,
					__('No match in {0}. Showing rows where {1} {2} "{3}".', [
						label(failed.fieldname),
						label(field),
						exact ? "is" : "contains",
						term,
					])
				);
				return;
			}

			const words = crema_term_words(term);
			if (!words.length) return giveUp();

			return probe(words).then((word_rows) => {
				// Rank every (field, word) pair independently, not just field — a word
				// that never appears in a given field must not win it by default just
				// because a different word matched a different candidate there.
				let best = null;
				for (const word of words) {
					const ranked = crema_rank_candidate(candidates, word_rows, word.toLowerCase());
					if (ranked.count && (!best || ranked.count > best.count)) {
						best = { ...ranked, word };
					}
				}
				if (!best) return giveUp();
				crema_apply_widened_filter(
					doctype,
					best.field,
					"like",
					`%${crema_like_escape(best.word)}%`,
					__('No match in {0}. Showing rows where {1} contains "{2}".', [
						label(failed.fieldname),
						label(best.field),
						best.word,
					])
				);
			});
		})
		.catch(() =>
			frappe.show_alert({
				message: __("Could not search other fields."),
				indicator: "orange",
			})
		);
}

// Where both apply paths in crema_apply_view_spec converge once the list has rendered:
// the model's reason alert, then the zero-result fallback.
function crema_after_view(doctype, spec, filters) {
	// spec.reason is model-authored text; frappe.show_alert interpolates its message raw
	// into an HTML template (frappe/public/js/frappe/ui/messages.js), so this is a DOM
	// sink without the escape.
	if (spec.reason) {
		frappe.show_alert({ message: frappe.utils.escape_html(spec.reason), indicator: "blue" });
	}
	// live.refresh() can be a no-op: filter_area's own debounced refresh (300ms,
	// base_list.js) may not yet have fired — or may have JUST fired — by the time
	// crema_refresh_live calls it, and no_change() then hands back an already-resolved
	// promise while the real query is still in flight, or not even dispatched yet.
	// frappe.after_ajax only catches a query already under way (it checks
	// frappe.request.ajax_count synchronously) — a debounced refresh whose setTimeout
	// hasn't fired at all yet slips past that check with cur_list.data still the
	// previous page of rows. Outlast the debounce window itself before checking, so a
	// not-yet-dispatched debounced refresh can't slip past frappe.after_ajax either.
	setTimeout(() => frappe.after_ajax(() => crema_widen_if_empty(doctype, filters)), 300);
}

// ---- Path B continued: prompt -> create / edit / delete ----------------------------

// Resolves a model-authored filter set to actual records, under the desk user's own
// permissions (frappe.db.get_list, same call crema_widen_if_empty already makes) — the
// target set for edit/delete. An action naming no valid filter is refused outright
// (rejects with "crema:no-filters") rather than silently read as "every record". frappe's
// own bulk endpoints only handle up to 500 documents in one call
// (bulk_update.py submit_cancel_or_update_docs) — a match past that rejects with
// "crema:too-many" instead of quietly picking the first 500.
function crema_resolve_targets(doctype, raw_filters) {
	const filters = crema_valid_filters(doctype, raw_filters);
	if (!Object.keys(filters).length) return Promise.reject(new Error("crema:no-filters"));
	const meta = frappe.get_meta(doctype);
	const title_field = meta.title_field && meta.title_field !== "name" ? meta.title_field : null;
	const fields = title_field ? ["name", title_field] : ["name"];
	return frappe.db.get_list(doctype, { filters, fields, limit: 501 }).then((rows) => {
		if (rows.length > 500) throw new Error("crema:too-many");
		return { filters, rows, title_field };
	});
}

function crema_resolve_error(e) {
	if (e?.message === "crema:no-filters") {
		frappe.msgprint({
			message: __('Say which records — a request naming none is not "all of them".'),
			indicator: "orange",
		});
		return;
	}
	if (e?.message === "crema:too-many") {
		frappe.msgprint({
			message: __("That matches more than 500 records — ask for something narrower."),
			indicator: "orange",
		});
		return;
	}
	crema_show_error();
}

// Shared confirm dialog for edit/delete on more than one record, and for a multi-record
// create — lists every affected record by name/title (or, for create, the proposed
// records themselves via extra_html) before anything happens. Native elements only:
// frappe.ui.Dialog, a plain bordered table, no bespoke widget.
//
// Hides itself on confirm rather than disabling the primary button and waiting for
// onConfirm to close it: the dialog holds no input to protect mid-flight (just a
// read-only list), the bulk-update and BulkOperations.delete calls each already show
// their own freeze/progress overlay, and BulkOperations.delete (frappe core) accepts no
// error callback at all — a "re-enable on failure" path was never reachable for delete,
// so applying it only to edit/create would be an inconsistency, not a fix.
function crema_confirm_records({
	title,
	rows,
	title_field,
	list_heading,
	extra_html,
	extra_heading,
	primary_label,
	danger,
	onConfirm,
}) {
	const heading = (text) =>
		text ? `<h5 class="text-muted">${frappe.utils.escape_html(text)}</h5>` : "";
	const list_html = rows?.length
		? `${heading(
				list_heading
		  )}<div style="max-height: 40vh; overflow-y: auto; margin-bottom: 1rem;">
					<table class="table table-bordered"><thead><tr><th>#</th><th>${__(
						"Record"
					)}</th></tr></thead><tbody>${rows
				.map(
					(r, i) =>
						`<tr><td>${i + 1}</td><td>${frappe.utils.escape_html(
							String((title_field ? r[title_field] : null) ?? r.name)
						)}</td></tr>`
				)
				.join("")}</tbody></table>
				</div>`
		: "";
	const dialog = new frappe.ui.Dialog({
		title,
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "body",
				options: `${list_html}${heading(extra_heading)}${extra_html || ""}`,
			},
		],
		primary_action_label: primary_label,
		primary_action() {
			dialog.hide();
			onConfirm();
		},
	});
	if (danger) dialog.get_primary_btn().removeClass("btn-primary").addClass("btn-danger");
	dialog.show();
}

// Single-record edit: shown as a diff before anything is applied — the diff table is how
// the user knows what changed — then routes to the Form with the change already applied
// (crema_apply_diff, locals-first — see its own comment for why routing happens AFTER
// the mutation), left dirty for the user to review and save themselves.
function crema_edit_one(doctype, name, diff, reason) {
	const dialog = new frappe.ui.Dialog({
		// name is a record name, not a fixed label — a field-autonamed doctype can put
		// arbitrary text (including markup) into it, and Dialog.set_title is .html().
		title: __("Update {0}", [frappe.utils.escape_html(name)]),
		fields: [{ fieldtype: "HTML", fieldname: "body", options: crema_diff_table(diff) }],
		primary_action_label: __("Apply"),
		primary_action() {
			dialog.hide();
			frappe.model.with_doc(doctype, name).then(() => {
				crema_apply_diff(doctype, name, diff);
				frappe.set_route("Form", doctype, name);
				if (reason) {
					frappe.show_alert({
						message: frappe.utils.escape_html(reason),
						indicator: "blue",
					});
				}
			});
		},
	});
	dialog.show();
}

function crema_apply_create_spec(doctype, spec) {
	// An affordance check, not the fence — stops an unsaveable form/import rather than an
	// unauthorised write; frappe.model.get_new_doc + save (single) or Data Import (many)
	// still enforce for real. Same gate the dialog's own upload area already uses.
	if (!frappe.model.can_create(doctype) || crema_blocked(doctype)) {
		frappe.msgprint({
			message: __("You cannot create a {0}.", [__(doctype)]),
			indicator: "red",
		});
		return;
	}
	const records = Array.isArray(spec.records) ? spec.records : [];
	if (!records.length) {
		frappe.msgprint({
			message: __("Crema did not propose any records."),
			indicator: "orange",
		});
		return;
	}
	frappe.model.with_doctype(doctype, () => {
		// The "view" interface's raw ask_api answer never runs through api._filter_diff
		// server-side (unlike extract_api/transform_api) — this is the only fence a
		// create action's fields get. A record left with nothing set (every proposed
		// field read-only or invented) is dropped rather than opened as a blank form.
		const filtered = records
			.map((r) => crema_filter_diff(doctype, r || {}))
			.filter((r) => Object.keys(r.set).length || Object.keys(r.child_set).length);
		if (!filtered.length) {
			frappe.msgprint({
				message: __("Crema did not propose any records."),
				indicator: "orange",
			});
			return;
		}

		if (filtered.length === 1) {
			crema_open_new_doc(doctype, filtered[0]);
			if (spec.reason) {
				frappe.show_alert({
					message: frappe.utils.escape_html(spec.reason),
					indicator: "blue",
				});
			}
			return;
		}
		if (!frappe.model.can_import(doctype)) {
			frappe.msgprint({
				message: __("Creating several records at once needs the System Manager role."),
				indicator: "orange",
			});
			return;
		}
		crema_confirm_records({
			title: __("Create {0} {1} records?", [filtered.length, __(doctype)]),
			rows: null,
			extra_heading: __("Records to create"),
			extra_html: filtered.map((r, i) => `<b>${i + 1}.</b>${crema_diff_table(r)}`).join(""),
			primary_label: __("Create {0} Documents", [filtered.length]),
			onConfirm: () => crema_import_records(doctype, filtered),
		});
	});
}

function crema_apply_edit_spec(doctype, spec) {
	if (!frappe.perm.has_perm(doctype, 0, "write") || crema_blocked(doctype)) {
		frappe.msgprint({
			message: __("You cannot edit {0} records.", [__(doctype)]),
			indicator: "red",
		});
		return;
	}
	crema_resolve_targets(doctype, spec.filters)
		.then(({ rows, title_field }) => {
			if (!rows.length) {
				frappe.msgprint({
					message: __("Nothing in this list matches that."),
					indicator: "orange",
				});
				return;
			}
			frappe.model.with_doctype(doctype, () => {
				const diff = crema_filter_diff(doctype, spec);
				// bulk_update.py's action=="update" branch calls doc.save() unconditionally,
				// even when data is {} — an unguarded no-op would silently re-save every
				// matched record (bumping modified, firing hooks) and report "success". Bulk
				// drops child_set below, so a child-only change only counts for one record.
				const has_changes =
					Object.keys(diff.set).length ||
					(rows.length === 1 && Object.keys(diff.child_set).length);
				if (!has_changes) {
					frappe.msgprint({
						message: __("Crema did not propose any change you may write."),
						indicator: "orange",
					});
					return;
				}

				if (rows.length === 1) {
					crema_edit_one(doctype, rows[0].name, diff, spec.reason);
					return;
				}
				// frappe.desk.doctype.bulk_update.bulk_update.submit_cancel_or_update_docs
				// refuses without the Bulk Actions permission (User.bulk_actions) — check it
				// up front rather than let the user confirm into a PermissionError.
				if (!frappe.boot.desk_settings?.bulk_actions) {
					frappe.msgprint({
						message: __("You are not allowed to perform bulk actions."),
						indicator: "red",
					});
					return;
				}
				// Bulk edit carries only "set" — frappe's own bulk_update endpoint only
				// overwrites a whole child table field uniformly, not per-row like child_set
				// describes, so a multi-target edit's child_set is dropped rather than
				// applied wrong; the prompt tells the model child_set is single-record only.
				crema_confirm_records({
					title: __("Update {0} {1} records?", [rows.length, __(doctype)]),
					rows,
					title_field,
					list_heading: __("Records to update"),
					extra_heading: __("Change applied to every record"),
					extra_html: crema_diff_table({ set: diff.set }),
					primary_label: __("Update {0} records", [rows.length]),
					onConfirm: () => {
						frappe.call({
							method: "frappe.desk.doctype.bulk_update.bulk_update.submit_cancel_or_update_docs",
							args: {
								doctype,
								docnames: rows.map((r) => r.name),
								action: "update",
								data: diff.set,
							},
							freeze: true,
							callback() {
								frappe.show_alert(__("Updated successfully"));
								if (
									typeof cur_list !== "undefined" &&
									cur_list &&
									cur_list.doctype === doctype
								) {
									cur_list.refresh();
								}
							},
							error: crema_show_error,
						});
					},
				});
			});
		})
		.catch(crema_resolve_error);
}

function crema_apply_delete_spec(doctype, spec) {
	if (!frappe.perm.has_perm(doctype, 0, "delete") || crema_blocked(doctype)) {
		frappe.msgprint({
			message: __("You cannot delete {0} records.", [__(doctype)]),
			indicator: "red",
		});
		return;
	}
	crema_resolve_targets(doctype, spec.filters)
		.then(({ rows, title_field }) => {
			if (!rows.length) {
				frappe.msgprint({
					message: __("Nothing in this list matches that."),
					indicator: "orange",
				});
				return;
			}
			// frappe.desk.reportview.delete_items refuses without the Bulk Actions
			// permission (User.bulk_actions), whatever the row count — check it up front
			// rather than let the user confirm into a PermissionError.
			if (!frappe.boot.desk_settings?.bulk_actions) {
				frappe.msgprint({
					message: __("You are not allowed to perform bulk actions."),
					indicator: "red",
				});
				return;
			}
			crema_confirm_records({
				title: __("Delete {0} {1} records?", [rows.length, __(doctype)]),
				rows,
				title_field,
				list_heading: __("Records to delete"),
				primary_label: __("Delete {0} records", [rows.length]),
				danger: true,
				onConfirm: () => {
					// list_view.js's own delete confirmation uses this exact call
					// (BulkOperations.delete -> frappe.desk.reportview.delete_items).
					new frappe.ui.BulkOperations({ doctype }).delete(
						rows.map((r) => r.name),
						() => {
							frappe.show_alert(__("Deleted successfully"));
							if (
								typeof cur_list !== "undefined" &&
								cur_list &&
								cur_list.doctype === doctype
							) {
								cur_list.refresh();
							}
						}
					);
				},
			});
		})
		.catch(crema_resolve_error);
}

// ---- Dialog ---------------------------------------------------------------------------

// Ctrl+Enter (Cmd+Enter on macOS folds into the same "ctrl+" prefix —
// frappe.ui.keys.get_key merges ctrlKey/metaKey) submits without the mouse. Plain Enter
// can't do this: both dialogs put the request in a Small Text textarea, where Enter has
// to stay a newline.
function crema_bind_go_shortcut(dialog) {
	const shortcut = frappe.ui.keys.get_shortcut_label("ctrl+enter");
	dialog.get_primary_btn().attr("title", shortcut);
	dialog.$wrapper.on("keydown", (e) => {
		if (frappe.ui.keys.get_key(e) === "ctrl+enter") {
			e.preventDefault();
			dialog.get_primary_btn().trigger("click");
		}
	});
}

// Bootstrap/frappe draw a hairline under the header, above the footer, and between
// every Section Break past the first — none of them carry information in a two-field
// dialog, just visual noise. Scoped to this dialog's own $wrapper (inline, matching the
// min-width/min-height overrides above) rather than the shared stylesheet, so no other
// dialog in the desk loses its border.
function crema_strip_dialog_borders(dialog) {
	dialog.$wrapper.find(".modal-header, .modal-footer").css("border", "none");
	dialog.$wrapper.find(".form-section").css("border-top", "none");
}

function crema_open_dialog(doctype, prefill) {
	const can_create = !crema_blocked(doctype) && frappe.model.can_create(doctype);

	// Instruction first, upload second: the instruction isn't an alternative to the
	// upload, it steers it (on_success below passes it straight into extract_api) — so
	// the layout says that, instead of a label reading "Or ask a question" that implies
	// they're two unrelated choices.
	const dialog = new frappe.ui.Dialog({
		title: __("Ask Crema about {0}", [__(doctype)]),
		size: "large",
		fields: [
			{
				fieldtype: "Small Text",
				fieldname: "instruction",
				label: __("What do you want to do?"),
				placeholder: __("e.g. show only the ones added this month"),
				description: __(
					"Crema can change what this list shows, or add, change, or delete records. It does only what your permissions allow."
				),
				default: prefill || "",
			},
			...(can_create
				? [
						{ fieldtype: "Section Break", label: __("Add a document (optional)") },
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
			crema_ask(doctype, values.instruction);
		},
	});

	// Inline drop zone instead of a plain Attach control: passing `wrapper` (rather
	// than letting FileUploader make its own dialog) renders the widget in place, so
	// picking a file no longer pops a second modal on top of this one. How many
	// records the file holds is no longer asked up front — extract() decides that
	// itself; see crema_show_extract_preview.
	if (can_create) {
		const uploader = new frappe.ui.FileUploader({
			wrapper: dialog.fields_dict.upload.$wrapper,
			disable_file_browser: true,
			allow_multiple: false,
			allow_web_link: false,
			allow_take_photo: false,
			on_success: (file_doc) => {
				dialog.fields_dict.upload.$wrapper.hide();
				crema_extract_into_new_doc(
					doctype,
					file_doc.file_url,
					dialog.get_value("instruction"),
					dialog
				);
			},
		});
		// FileUploader's .file-upload-area is a Vue *scoped* style (min-height: 16rem),
		// which a plain stylesheet rule can't outrank on specificity — an inline style
		// can. app.mount() inside the FileUploader constructor is synchronous, so
		// `uploader.wrapper` (the mounted element) already exists here.
		$(uploader.wrapper).find(".file-upload-area").css("min-height", "7rem");
		// A Section Break's own `description` sits in a Bootstrap col with different
		// left padding than the section's bold title above it — visibly out of line. A
		// plain .help-box (the same class the instruction field's description above
		// uses) reads correctly under a control, so put it under the uploader instead,
		// as a sibling appended after the Vue-mounted drop zone rather than inside it.
		$(
			`<div class="help-box small text-extra-muted">${__(
				"Crema reads it and proposes new records, guided by the text above."
			)}</div>`
		).appendTo(dialog.fields_dict.upload.$wrapper);
	}

	crema_bind_go_shortcut(dialog);
	crema_strip_dialog_borders(dialog);
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
						crema_apply_diff(frm.doctype, frm.docname, data, frm);
						dialog.hide();
					});
				},
				error: crema_show_error,
			});
		},
	});
	crema_bind_go_shortcut(dialog);
	crema_strip_dialog_borders(dialog);
	dialog.show();
}

// ---- Path D: instruction -> draft for one field ------------------------------------

// A narrower sibling of Path C: same instruction -> diff round trip, but the model is
// asked about one field and the client keeps only that one key out of whatever it
// returns — data.set may carry other fields the model proposed anyway (nothing server-
// side stops it), so `fieldname in data.set` and reading exactly that key, nothing else
// out of data.set/data.child_set, is what keeps this the one-field button it claims to
// be. See crema_field_draft_control below for where this is opened from.
function crema_open_field_draft_dialog(frm, fieldname, label) {
	const dialog = new frappe.ui.Dialog({
		title: __("Draft {0}", [__(label)]),
		fields: [
			{
				fieldtype: "Small Text",
				fieldname: "instruction",
				label: __("What should it say?"),
			},
			{ fieldtype: "HTML", fieldname: "preview", hidden: 1 },
		],
		primary_action_label: __("Go"),
		primary_action(values) {
			if (!values.instruction) {
				frappe.msgprint(__("Type what it should say, then press Go."));
				return;
			}
			frappe.call({
				method: "crema.api.transform_api",
				args: {
					doctype: frm.doctype,
					name: frm.docname,
					instruction: `Draft only the "${fieldname}" field. ${values.instruction}`,
				},
				freeze: true,
				freeze_message: __("Thinking…"),
				callback(r) {
					const data = r.message;
					if (!data || !(fieldname in (data.set || {}))) {
						frappe.msgprint({
							message: __("Crema had nothing to propose for this field."),
							indicator: "orange",
						});
						return;
					}
					const value = data.set[fieldname];
					dialog.fields_dict.preview.df.hidden = 0;
					dialog.set_df_property(
						"preview",
						"options",
						`<div class="text-muted small">${frappe.utils.escape_html(
							data.reason || ""
						)}</div>${crema_diff_table({ set: { [fieldname]: value } })}`
					);
					dialog.refresh();
					dialog.set_primary_action(__("Apply"), () => {
						crema_apply_diff(
							frm.doctype,
							frm.docname,
							{ set: { [fieldname]: value } },
							frm
						);
						dialog.hide();
					});
				},
				error: crema_show_error,
			});
		},
	});
	crema_bind_go_shortcut(dialog);
	crema_strip_dialog_borders(dialog);
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
		// v17's page.html pre-renders this slot as an .es-button, not a .btn, so
		// set_action's frappe.ui.button.dress() runs instead of the old get_icon_label
		// path. dress() already squares an icon-with-no-label button via
		// data-icon-button="true" (width: var(--es-bt-h) = 28px) — but
		// .page-actions .secondary-action carries its own unconditional
		// "min-width: 40px" (desk/page.scss), and min-width beats width regardless of
		// selector specificity. Override min-width inline (highest specificity) to match
		// the square reload button next to it. .icon-btn/.html() overrides from the old
		// .btn-based fix are gone: .icon-btn only matches a .btn (common/buttons.scss)
		// so it's inert here, and overwriting .html() would wipe dress()'s own
		// .es-spinner markup that the busy state needs.
		this.page
			.set_secondary_action("", () => crema_open_dialog(this.doctype), "bot")
			.css("min-width", "var(--btn-height)")
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

// Field views: a draft button next to the label of any writable Text/Long Text/Small
// Text field on a saved document — Path D. Hooked on refresh(), not make_input(): a
// field's writable/hidden status and the document's own local/write status can both
// change after the control is first built (a new document becomes saved; depends_on
// flips a field read-only), and refresh() is what re-evaluates them on every render —
// make_input() only ever runs once. This is the same hook core's own
// show_translatable_button uses, right above it in base_control.js, for the same
// reason, with the same "already attached" guard. ControlLongText is literally
// ControlText (form/controls/text.js) and ControlSmallText extends it without
// overriding refresh, so one wrap covers all three; Text Editor has its own toolbar
// and is out of scope.
(() => {
	const refresh = frappe.ui.form.ControlText.prototype.refresh;
	frappe.ui.form.ControlText.prototype.refresh = function () {
		refresh.call(this);
		const frm = this.frm;
		if (
			!frm ||
			!crema_allowed() ||
			frm.doc.__islocal ||
			!frm.perm[0]?.write ||
			this.df.parent !== frm.doctype || // excludes a child-table grid cell
			this.disp_status !== "Write" ||
			this.$wrapper.find(".btn-crema-draft").length
		) {
			return;
		}
		const fieldname = this.df.fieldname;
		$(
			`<a class="btn-crema-draft no-decoration text-muted" title="${__(
				"Draft with Crema"
			)}">${frappe.utils.icon("bot", "sm")}</a>`
		)
			.appendTo(this.$wrapper.find(".clearfix"))
			.on("click", () =>
				crema_open_field_draft_dialog(frm, fieldname, this.df.label || fieldname)
			);
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
			onclick: () => crema_ask(doctype, txt),
		});
	};
})();

// ---- Shared helpers — used by the Crema Settings form and by the Crema Provider
// doctype JS, so the model list fetch and the template dialog exist in exactly one
// place. Exported on `window`: esbuild's *.bundle.js output wraps this whole file in
// an IIFE, so a plain top-level `function` here is module-scoped and invisible to
// crema_settings.js / crema_provider_list.js, which are evaluated as separate
// scripts and can only reach it through the global object. ------------------------

// Month-to-date spend as a pill: grey when the interface or provider has no budget (0
// means unlimited), otherwise a percentage that goes orange at 80 and red at 100. Shared
// by the Model Assignments grid on Crema Settings and the Crema Provider list.
function crema_usage_pill(usage) {
	if (!usage) return "";
	const spend = `$${crema_fmt_usd(usage.spend)}`;
	if (!usage.budget) return frappe.ui.badge.html({ label: spend, theme: "gray" });
	const pct = Math.round((usage.spend / usage.budget) * 100);
	let theme = "green";
	if (pct >= 100) {
		theme = "red";
	} else if (pct >= 80) {
		theme = "orange";
	}
	return frappe.ui.badge.html({
		label: `${pct}%`,
		theme,
		title: `${spend} of $${crema_fmt_usd(usage.budget)}`,
	});
}

function crema_fmt_usd(value) {
	return (value || 0).toFixed(2);
}

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
							if (values.set_as_default) {
								// create_from_template's nested Crema Settings save bumped that
								// doc's `modified`. A copy already in locals — an earlier visit
								// to the Settings form this session — is now stale, and
								// frappe.model.with_doc short-circuits on a cached doc, so
								// navigating there would not refetch it; saving it would then
								// fail frappe's own timestamp check.
								frappe.model.remove_from_locals(
									"Crema Settings",
									"Crema Settings"
								);
							}
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
window.crema_usage_pill = crema_usage_pill;
// crema_automation_task.js is likewise a separate script, evaluated outside this IIFE.
window.crema_show_error = crema_show_error;
// The automation form's Dry Run preview renders extracted rows and a plan's field map
// with the same key/value table the extract and transform previews already use.
window.crema_diff_table = crema_diff_table;
