// Copyright (c) 2026, Crema and contributors
// For license information, please see license.txt

/* global crema_new_provider_dialog */
// crema_new_provider_dialog is defined once in crema.bundle.js (app_include_js, so it
// is already loaded here) and shared with the Crema Settings form.
//
// This list replaced the hand-written Providers table that used to live in an HTML field
// on Crema Settings. Name, Address and Status are ordinary list columns now. The Connection
// column (a live probe) is computed and never stored, so it is painted by a formatter over
// a field that is already on the row.
//
// provider_name has no in_list_view: autoname is field:provider_name, so the subject
// column is already the name, and title_field + hide_name_column below keep it to one.
//
// listview_settings formatters are synchronous, so the value has to be in hand before a
// row renders: before_render prefetches it and asks for one more refresh once the
// batch resolves. A row already in connection_cache (probed) or pending (in flight) is
// skipped, which is what stops that refresh from looping — and, unlike a single
// page-wide "done" flag, still probes a provider added after the first render, on
// whatever render comes next (New from Template's own listview.refresh() included).
const connection_cache = {};
const pending = new Set();
let list = null;

function prefetch(listview) {
	// before_render is invoked as this.settings.before_render(), so `this` inside it is
	// the settings object, not the list — hence the listview captured in onload.
	if (!listview) return;

	// One uncached check_provider call per enabled provider not yet probed or in
	// flight, in parallel — an hour-stale "Connected" pill (list_models' own cache
	// TTL) is worse than one extra request per new row, hence the separate uncached
	// endpoint (crema.api.check_provider).
	const probes = (listview.data || [])
		.filter((d) => d.enabled && !(d.name in connection_cache) && !pending.has(d.name))
		.map((d) => {
			pending.add(d.name);
			// frappe.call returns a jQuery-style promise (no native .finally), so
			// pending.delete has to run in both settle paths rather than chaining one.
			return frappe
				.call({ method: "crema.api.check_provider", args: { provider: d.name } })
				.then((r) => {
					connection_cache[d.name] = r.message || {
						ok: false,
						detail: __("No response"),
					};
					pending.delete(d.name);
				})
				.catch(() => {
					connection_cache[d.name] = { ok: false, detail: __("Error") };
					pending.delete(d.name);
				});
		});

	if (!probes.length) return;
	// allSettled, not all: a blank Connection column is better than a list that never
	// shows the connection results because one call failed.
	Promise.allSettled(probes).then(() => listview.refresh());
}

frappe.listview_settings["Crema Provider"] = {
	hide_name_column: true,
	add_fields: ["enabled", "auto_disabled", "last_checked", "last_check_detail"],

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
		if (doc.enabled) return [__("Enabled"), "green", "enabled,=,1"];
		if (doc.auto_disabled) return [__("Auto-disabled"), "orange", "auto_disabled,=,1"];
		return [__("Disabled"), "gray", "enabled,=,0"];
	},

	formatters: {
		enabled(value, df, doc) {
			if (!doc.enabled) {
				if (doc.auto_disabled) {
					// badge.html HTML-escapes the label itself.
					return frappe.ui.badge.html({
						label: doc.last_check_detail
							? `${__("Auto-disabled")}: ${doc.last_check_detail}`
							: __("Auto-disabled"),
						theme: "orange",
						title: doc.last_checked,
					});
				}
				return frappe.ui.badge.html({ label: __("Disabled"), theme: "gray" });
			}
			const status = connection_cache[doc.name];
			if (!status) return `<span class="text-muted">${__("Checking…")}</span>`;
			// badge.html HTML-escapes the label itself.
			return frappe.ui.badge.html({
				label: status.detail,
				theme: status.ok ? "green" : "red",
			});
		},
	},
};
