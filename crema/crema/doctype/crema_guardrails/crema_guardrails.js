/* global crema_fetch_models */
// An ordinary editable grid: rows may be added, deleted and dragged into order. The
// Guardrail column is a Select (options stamped by install.sync_guardrail_options
// from crema.guardrails.registry()) rather than free text, so a row can only ever name
// a check this site actually has; crema.api.get_guardrails relabels it here the same
// way crema_automation_task.js relabels the AI Profile picker, including formatting the
// static (non-editing) cell so a friendly name shows without opening the dropdown.
// Everything else — the Checked By -> Guard Model gating below — mirrors
// crema_settings.js's Model Assignments grid, including its fall-through to Crema
// Settings' Default Provider (crema.client._scanner_cfg does the same at call time),
// fetched once per form load since this Single has no Settings fields of its own to
// read it from.
let default_provider = null;

frappe.ui.form.on("Crema Guardrails", {
	refresh(frm) {
		frappe.db.get_single_value("Crema Settings", "default_provider").then((value) => {
			default_provider = value;
			frm.fields_dict.guardrails.grid.grid_rows.forEach((grid_row) =>
				toggle_guard_model_field(frm, grid_row.doc.doctype, grid_row.doc.name)
			);
		});
		frappe
			.xcall("crema.api.get_guardrails")
			.then((options) => {
				const grid = frm.fields_dict.guardrails.grid;
				const labels = Object.fromEntries(options.map((o) => [o.value, o.label]));
				grid.update_docfield_property("guardrail", "options", options);
				grid.update_docfield_property(
					"guardrail",
					"formatter",
					(value) => labels[value] || value
				);
				grid.refresh();
				render_check_list(frm, options);
			})
			.catch(() => {}); // fall through to the raw meta options, same as the task picker
	},
});

frappe.ui.form.on("Crema Guardrail", {
	form_render(frm, cdt, cdn) {
		toggle_guard_model_field(frm, cdt, cdn);
	},
	guard_provider(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, "guard_model", "");
		toggle_guard_model_field(frm, cdt, cdn);
	},
});

// The brief, then one line per registered check — the same rows the Guardrail picker
// offers, described in place so a reader never has to open the dropdown to learn what
// "Hide Health Information" is. An app-registered guardrail with a `help` string on
// its module is listed the same way. This whole block replaces the grid's own field
// description, which frappe would otherwise render below the grid, ragged-right, under
// its own "Guardrails" label — none of which is ours to control there.
const GUARDRAILS_BRIEF =
	"Every request runs through these checks, top to bottom. Drag a row to change the " +
	"order, add a row to run a check again, delete a row to stop running it (the same " +
	"as Off). Checks that undo their work on the reply do it in reverse order, but only " +
	"for the rows that ran on the way in — so put a Hide row above anything that must " +
	"see masked text, including the AI Guard. Audio is not checked or masked before it " +
	"is sent: for Transcribe, only a Hide guardrail set to Block has an effect (it " +
	"refuses the call).";

function render_check_list(frm, options) {
	const items = options
		.filter((o) => o.help)
		.map(
			(o) =>
				`<div style="margin-bottom: var(--margin-xs)"><strong>${frappe.utils.escape_html(
					o.label
				)}</strong> — ${frappe.utils.escape_html(o.help)}</div>`
		)
		.join("");
	frm.get_field("guardrail_help").html(
		`<div class="small" style="text-align: justify">
			<p style="margin-bottom: var(--margin-sm)">${__(GUARDRAILS_BRIEF)}</p>
			${items}
		</div>`
	);
}

function toggle_guard_model_field(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	const grid_row = frm.fields_dict.guardrails.grid.grid_rows_by_docname[cdn];
	if (!grid_row) return;
	const effective_provider = row.guard_provider || default_provider;
	const model_field = grid_row.get_field("guard_model");
	model_field.df.read_only = !effective_provider;
	model_field.refresh();
	crema_fetch_models(effective_provider, (models) => model_field.set_data(models));
}
