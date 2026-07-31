// Copyright (c) 2026, Crema and contributors
// For license information, please see license.txt

// Crema Settings — a Single, so it's one ordinary form: Providers (rendered into an
// HTML field) + Model Assignments (a Table field, one row per crema.interfaces.names()
// name — core PREDEFINED plus whatever installed apps register through the
// crema_interfaces hook; see CremaSettings.validate for the reconcile). No add/delete row
// affordance: the set is not the user's to edit here. Saving goes through the normal
// frm.save() -> Document.validate/
// on_update path, so a real validation error surfaces as a real error, not a discarded one.

// Usage (spend + budget, per interface and per provider) is fetched once per refresh
// and stashed here — both the assignments grid formatter and the providers table read
// from it, rather than each firing their own frappe.call.
let usage_cache = { interfaces: {}, providers: {} };

frappe.ui.form.on("Crema Settings", {
	refresh(frm) {
		fetch_usage(frm, () => render_providers(frm));
		setup_assignments_grid(frm);
		load_default_models(frm);
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

function fetch_usage(frm, callback) {
	frappe.call({
		method: "crema.api.get_usage",
		callback(r) {
			usage_cache = r.message || { interfaces: {}, providers: {} };
			frm.get_field("assignments").grid.refresh();
			callback();
		},
		error() {
			// Deliberately no message here — this only feeds the Usage pills, and
			// render_providers must still run. Reset to blank rather than leaving a stale
			// usage_cache from a prior successful load, which would show numbers that no
			// longer match what's on screen.
			usage_cache = { interfaces: {}, providers: {} };
			callback();
		},
	});
}

function usage_pill(usage) {
	if (!usage) return "";
	const spend = `$${fmt_usd(usage.spend)}`;
	if (!usage.budget) return `<span class="indicator-pill gray">${spend}</span>`;
	const pct = Math.round((usage.spend / usage.budget) * 100);
	const color = pct >= 100 ? "red" : pct >= 80 ? "orange" : "green";
	return `<span class="indicator-pill ${color}" title="${spend} of $${fmt_usd(
		usage.budget
	)}">${pct}%</span>`;
}

function fmt_usd(value) {
	return (value || 0).toFixed(2);
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
		usage_pill(usage_cache.interfaces[doc.interface])
	);

	grid.add_custom_button(__("Reset Assignments"), () => {
		frappe.confirm(
			__(
				"Clear every per-row override? All interfaces fall back to the Defaults above. Nothing is saved until you save the form."
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
		frappe.model.set_value(row.doctype, row.name, "enable_prompt_scan", 1);
		frappe.model.set_value(row.doctype, row.name, "enable_llm_guard", 0);
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
				  )}' is not offered by '${frappe.utils.escape_html(frm.doc.default_provider)}'.</span>`
				: base_description
		);
	});
}

function render_providers(frm) {
	const $wrapper = frm.get_field("providers_html").$wrapper.empty();
	$(`<button class="btn btn-default btn-sm">${__("New from Template")}</button>`)
		.on("click", () => crema_new_provider_dialog(() => frm.reload_doc()))
		.appendTo($wrapper);
	const $table = $(`<div class="crema-providers-table" style="margin-top: 8px;"></div>`).appendTo(
		$wrapper
	);

	frappe.db
		.get_list("Crema Provider", {
			fields: ["name", "base_url", "enabled"],
			limit_page_length: 0,
			order_by: "provider_name asc",
		})
		.then((rows) => {
			const rows_html = rows.length
				? rows
						.map(
							(d) => `<tr class="crema-provider-row" data-name="${frappe.utils.escape_html(
								d.name
							)}">
								<td>${frappe.utils.escape_html(d.name)}</td>
								<td>${frappe.utils.escape_html(d.base_url || "")}</td>
								<td>${
									d.enabled
										? `<span class="indicator-pill green">${__("Enabled")}</span>`
										: `<span class="indicator-pill gray">${__("Disabled")}</span>`
								}</td>
								<td class="crema-provider-conn">${
									d.enabled
										? `<span class="text-muted">${__("Checking…")}</span>`
										: `<span class="indicator-pill gray">${__("Disabled")}</span>`
								}</td>
								<td>${usage_pill(usage_cache.providers[d.name])}</td>
							</tr>`
						)
						.join("")
				: `<tr><td colspan="5" class="text-muted">${__("No providers yet.")}</td></tr>`;
			$table.html(`<table class="table table-bordered">
				<thead><tr><th>${__("Provider")}</th><th>${__("Base URL")}</th><th>${__(
				"Status"
			)}</th><th>${__("Connection")}</th><th>${__("Usage")}</th></tr></thead>
				<tbody>${rows_html}</tbody>
			</table>`);
			$table.find(".crema-provider-row").on("click", function () {
				frappe.set_route("Form", "Crema Provider", $(this).attr("data-name"));
			});

			// One uncached check_provider call per enabled row, in parallel — an hour-stale
			// "Connected" pill (list_models' own cache TTL) is worse than one extra request
			// per page load, hence the separate uncached endpoint (crema.api.check_provider).
			rows.filter((d) => d.enabled).forEach((d) => {
				const $cell = $table.find(`.crema-provider-row[data-name="${frappe.utils.escape_html(
					d.name
				)}"] .crema-provider-conn`);
				frappe.call({
					method: "crema.api.check_provider",
					args: { provider: d.name },
					callback(r) {
						const status = r.message || { ok: false, detail: __("No response") };
						const color = status.ok ? "green" : "red";
						$cell.html(
							`<span class="indicator-pill ${color}">${frappe.utils.escape_html(status.detail)}</span>`
						);
					},
					error() {
						$cell.html(`<span class="indicator-pill red">${__("Error")}</span>`);
					},
				});
			});
		})
		.catch(() => {
			$table.html(`<div class="text-danger">${__("Could not load providers.")}</div>`);
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
