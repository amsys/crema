// Copyright (c) 2026, Crema and contributors
// For license information, please see license.txt

/* global crema_fetch_models, crema_new_provider_dialog, crema_usage_pill */
// Crema Settings — a Single, so it's one ordinary form: Defaults + Model Assignments (a
// Table field, one row per crema.interfaces.names() name — core PREDEFINED plus whatever
// installed apps register through the crema_interfaces hook; see CremaSettings.validate
// for the reconcile). No add/delete row affordance: the set is not the user's to edit
// here. Saving goes through the normal frm.save() -> Document.validate/on_update path, so
// a real validation error surfaces as a real error, not a discarded one.
//
// AI services used to be a hand-written table in an HTML field on this form. They are the
// Crema Provider list view now — a real list, with sorting, filtering and paging for
// free; see crema_provider_list.js for the two computed columns it kept. This form does
// not link to it: Providers sits above Settings in the sidebar, which says the same thing
// without a button that is a no-op for everyone who already has one. The only exception
// is warn_if_no_providers below — nothing on this page resolves without a provider, so
// that case is worth interrupting for.

// Usage (spend + budget, per interface) is fetched once per refresh and stashed here, so
// the assignments grid formatter reads from it instead of firing its own frappe.call.
let usage_cache = { interfaces: {}, providers: {} };

frappe.ui.form.on("Crema Settings", {
	refresh(frm) {
		fetch_usage(frm);
		setup_assignments_grid(frm);
		load_default_models(frm);
		explain_crema_user_role(frm);
		warn_if_no_providers(frm);
	},
	default_provider(frm) {
		// set_value("default_model", "") fires the default_model trigger below, which
		// reloads the model list for the new provider — no separate call needed here.
		frm.set_value("default_model", "");
	},
	default_model(frm) {
		load_default_models(frm);
	},
});

function fetch_usage(frm) {
	frappe.call({
		method: "crema.api.get_usage",
		callback(r) {
			usage_cache = r.message || { interfaces: {}, providers: {} };
			frm.get_field("assignments").grid.refresh();
		},
		error() {
			// Deliberately no message here — this only feeds the Usage pills, and the rest
			// of the form is perfectly usable without them. Reset to blank rather than
			// leaving a stale usage_cache from a prior successful load, which would show
			// numbers that no longer match what's on screen.
			usage_cache = { interfaces: {}, providers: {} };
		},
	});
}

// The desk's Ask Crema button (the robot) is gated on Crema User *or* System Manager. Only
// a System Manager can reach this form, so they can always use Crema themselves — but
// every other user needs the role explicitly, and this is the one page that can say so.
// Always shown, not just when the reader lacks the role: the admin most likely to be
// provisioning other people is the one who already has it, and would otherwise never see
// the reminder.
function explain_crema_user_role(frm) {
	if (frappe.user_roles.includes("Crema User")) {
		frm.dashboard.add_comment(
			__(
				"Other people need the Crema User role to see the Ask Crema robot button. Add the role on their User record."
			),
			"blue",
			true
		);
		return;
	}

	frm.dashboard.add_comment(
		__(
			"You do not have the Crema User role. You can use Crema because you are a System Manager. Other people need this role to see the Ask Crema button."
		),
		"yellow",
		true
	);
	frm.add_custom_button(__("Give Me the Crema User Role"), () =>
		frappe.call({
			method: "crema.api.grant_crema_role",
			callback() {
				frappe.show_alert({
					message: __("Role added. Reload the page to see the Ask Crema button."),
					indicator: "green",
				});
			},
		})
	);
}

// Every default and every use case on this form resolves through an AI service. With none
// enabled, the whole page is a set of settings that cannot take effect — so say so, and
// offer the same template dialog the Providers list offers. Enabled, not merely present:
// a disabled service resolves no better than a missing one.
function warn_if_no_providers(frm) {
	frappe.db.count("Crema Provider", { filters: { enabled: 1 }, limit: 1 }).then((count) => {
		if (count) return;

		frm.dashboard.add_comment(
			__(
				"No AI service is switched on. Add one first — nothing on this page can work until you do."
			),
			"red",
			true
		);
		frm.add_custom_button(__("Add an AI Service"), () =>
			crema_new_provider_dialog(() => frm.reload_doc())
		);
	});
}

function setup_assignments_grid(frm) {
	// cannot_add_rows/cannot_delete_rows are read off the grid's docfield (grid.df), not
	// the grid object itself — see frappe/public/js/frappe/form/grid.js and grid_row.js.
	// Setting only grid.cannot_add_rows (as before) hid the add-row button (checked on
	// both) but left the row-level and footer delete buttons visible.
	const grid = frm.get_field("assignments").grid;
	grid.df.cannot_add_rows = true;
	grid.df.cannot_delete_rows = true;

	// Usage is display-only, painted from usage_cache — never persisted, so the
	// formatter reads live state instead of the (always-blank) stored field value.
	grid.update_docfield_property("usage", "formatter", (value, df, options, doc) =>
		crema_usage_pill(usage_cache.interfaces[doc.interface])
	);

	grid.add_custom_button(__("Reset All Use Cases"), () => {
		frappe.confirm(
			__(
				"Clear every setting on every use case? They all fall back to the Defaults above. Nothing is saved until you save the form."
			),
			() => reset_assignments(frm)
		);
	});

	grid.refresh();
}

// Blanks every per-row override so every interface falls back to the Defaults section.
// Left dirty on purpose — the user reviews the cleared grid and saves (or discards by
// reloading). A blank system_prompt is refilled from interfaces.DEFAULT_PROMPTS by
// CremaModelAssignment.validate on save, so it's safe to blank here too.
function reset_assignments(frm) {
	(frm.doc.assignments || []).forEach((row) => {
		["provider", "model", "isolation_user", "system_prompt"].forEach((fieldname) =>
			frappe.model.set_value(row.doctype, row.name, fieldname, "")
		);
		frappe.model.set_value(row.doctype, row.name, "monthly_budget_usd", 0);
		frappe.model.set_value(row.doctype, row.name, "temperature", 0);
		frappe.model.set_value(row.doctype, row.name, "cache_ttl", 0);
	});
	frm.refresh_field("assignments");
}

// ---- Default Model — an Autocomplete with no built-in data source; populate it from
// the selected Default Provider, same as the per-row Model cell (toggle_model_field
// below), and warn inline if the typed model isn't in that provider's list. ----------

function load_default_models(frm) {
	const model_field = frm.get_field("default_model");
	const base_description = "Used by any row that leaves Model blank.";
	model_field.df.read_only = !frm.doc.default_provider;
	model_field.refresh();
	crema_fetch_models(frm.doc.default_provider, (models) => {
		model_field.set_data(models);
		const model = frm.doc.default_model;
		const unknown = model && models.length && !models.includes(model);
		model_field.set_new_description(
			unknown
				? `<span class="text-danger">Model '${frappe.utils.escape_html(
						model
				  )}' is not offered by '${frappe.utils.escape_html(
						frm.doc.default_provider
				  )}'.</span>`
				: base_description
		);
	});
}

// ---- Model Assignments grid — provider -> model gating, per row. A blank
// provider/model/isolation_user here just means "use the Default above"; this toggle
// only concerns the Model Autocomplete's data source, not whether the row is
// otherwise valid. ------------------------------------------------------------------

frappe.ui.form.on("Crema Model Assignment", {
	form_render(frm, cdt, cdn) {
		toggle_model_field(frm, cdt, cdn);
	},
	provider(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, "model", "");
		toggle_model_field(frm, cdt, cdn);
	},
});

function toggle_model_field(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	const grid_row = frm.fields_dict.assignments.grid.grid_rows_by_docname[cdn];
	if (!grid_row) return;
	// A row with no Provider of its own still resolves through Default Provider
	// (client._load_from_db) — the Model cell should follow that same effective value,
	// not just the row's own override.
	const effective_provider = row.provider || frm.doc.default_provider;
	const model_field = grid_row.get_field("model");
	model_field.df.read_only = !effective_provider;
	model_field.refresh();
	crema_fetch_models(effective_provider, (models) => model_field.set_data(models));
}
