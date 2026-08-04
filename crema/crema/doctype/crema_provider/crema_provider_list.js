// Copyright (c) 2026, Crema and contributors
// For license information, please see license.txt

/* global crema_new_provider_dialog, crema_usage_pill */
// crema_new_provider_dialog and crema_usage_pill are defined once in crema.bundle.js
// (app_include_js, so it is already loaded here) and shared with the Crema Settings form.
//
// This list replaced the hand-written Providers table that used to live in an HTML field
// on Crema Settings. Name, Address and Status are ordinary list columns now. The two
// columns that were the reason for that HTML field — Connection (a live probe) and Usage
// (month-to-date spend) — are computed and never stored, so they are painted by
// formatters over fields that are already on the row.
//
// provider_name has no in_list_view: autoname is field:provider_name, so the subject
// column is already the name, and title_field + hide_name_column below keep it to one.
//
// listview_settings formatters are synchronous, so both values have to be in hand before
// a row renders: the first render prefetches them and asks for one more refresh. The
// `prefetched` flag is what stops that second refresh from looping.
let usage_cache = {};
const connection_cache = {};
let prefetched = false;
let list = null;

function prefetch(listview) {
	// before_render is invoked as this.settings.before_render(), so `this` inside it is
	// the settings object, not the list — hence the listview captured in onload.
	if (prefetched || !listview) return;
	prefetched = true;

	const usage = frappe
		.call({ method: "crema.api.get_usage" })
		.then((r) => {
			usage_cache = (r.message || {}).providers || {};
		})
		.catch(() => {
			usage_cache = {};
		});

	// One uncached check_provider call per enabled provider, in parallel — an hour-stale
	// "Connected" pill (list_models' own cache TTL) is worse than one extra request per
	// page load, hence the separate uncached endpoint (crema.api.check_provider).
	const probes = (listview.data || [])
		.filter((d) => d.enabled)
		.map((d) =>
			frappe
				.call({ method: "crema.api.check_provider", args: { provider: d.name } })
				.then((r) => {
					connection_cache[d.name] = r.message || {
						ok: false,
						detail: __("No response"),
					};
				})
				.catch(() => {
					connection_cache[d.name] = { ok: false, detail: __("Error") };
				})
		);

	// allSettled, not all: a blank Usage column is better than a list that never shows
	// the connection results because one call failed.
	Promise.allSettled([usage, ...probes]).then(() => listview.refresh());
}

frappe.listview_settings["Crema Provider"] = {
	hide_name_column: true,
	add_fields: ["enabled", "monthly_budget_usd"],

	onload(listview) {
		list = listview;
		listview.page.add_inner_button(__("New from Template"), () =>
			crema_new_provider_dialog(() => listview.refresh())
		);
	},

	before_render() {
		prefetch(list);
	},

	get_indicator(doc) {
		return doc.enabled
			? [__("Enabled"), "green", "enabled,=,1"]
			: [__("Disabled"), "gray", "enabled,=,0"];
	},

	formatters: {
		enabled(value, df, doc) {
			if (!doc.enabled) return `<span class="indicator-pill gray">${__("Disabled")}</span>`;
			const status = connection_cache[doc.name];
			if (!status) return `<span class="text-muted">${__("Checking…")}</span>`;
			return `<span class="indicator-pill ${
				status.ok ? "green" : "red"
			}">${frappe.utils.escape_html(status.detail)}</span>`;
		},

		monthly_budget_usd(value, df, doc) {
			return crema_usage_pill(usage_cache[doc.name]);
		},
	},
};
