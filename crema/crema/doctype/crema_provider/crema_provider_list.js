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
// batch resolves. A row with a fresh result in crema_connection_cache (probed less than
// CREMA_CONNECTION_TTL_MS ago) or in crema_pending_probes (in flight) is skipped, which
// is what stops that refresh from looping — and, unlike a single page-wide "done" flag,
// still probes a provider added after the first render, on whatever render comes next
// (New from Template's own listview.refresh() included). An older result stays on
// screen until its new probe returns.
//
// Top-level names in a doctype script share one page scope, so each has the crema_ prefix.
const CREMA_CONNECTION_TTL_MS = 60000;
const crema_connection_cache = {};
const crema_pending_probes = new Set();
let crema_provider_listview = null;

function crema_connection_fresh(name) {
	const status = crema_connection_cache[name];
	return Boolean(status) && Date.now() - status.checked_at < CREMA_CONNECTION_TTL_MS;
}

function crema_prefetch(listview) {
	// before_render is invoked as this.settings.before_render(), so `this` inside it is
	// the settings object, not the list — hence the listview captured in onload.
	if (!listview) return;

	// One uncached check_provider call per enabled provider with no fresh result and none
	// in flight, in parallel — an hour-stale "Connected" pill (list_models' own cache
	// TTL) is worse than one extra request per new row, hence the separate uncached
	// endpoint (crema.api.check_provider).
	const probes = (listview.data || [])
		.filter(
			(d) =>
				d.enabled && !crema_connection_fresh(d.name) && !crema_pending_probes.has(d.name)
		)
		.map((d) => {
			crema_pending_probes.add(d.name);
			// frappe.call returns a jQuery-style promise (no native .finally), so
			// crema_pending_probes.delete has to run in both settle paths rather than
			// chaining one.
			return frappe
				.call({ method: "crema.api.check_provider", args: { provider: d.name } })
				.then((r) => {
					crema_connection_cache[d.name] = {
						...(r.message || { ok: false, detail: __("No response") }),
						checked_at: Date.now(),
					};
					crema_pending_probes.delete(d.name);
				})
				.catch(() => {
					crema_connection_cache[d.name] = {
						ok: false,
						detail: __("Error"),
						checked_at: Date.now(),
					};
					crema_pending_probes.delete(d.name);
				});
		});

	if (!probes.length) return;
	// allSettled, not all: a blank Connection column is better than a list that never
	// shows the connection results because one call failed. `void`: allSettled never
	// rejects, and frappe shows a refresh error itself.
	// base_list.js refresh() does nothing when its args equal those of a refresh less than
	// 3 s ago (no_change), and the first probes often return in less time. Clear the
	// last args, or the pills stay at "Checking…".
	void Promise.allSettled(probes).then(() => {
		listview.last_args = null;
		listview.refresh();
	});
}

frappe.listview_settings["Crema Provider"] = {
	hide_name_column: true,
	add_fields: ["enabled", "auto_disabled", "last_checked", "last_check_detail"],

	onload(listview) {
		crema_provider_listview = listview;
		listview.page.add_inner_button(__("New from Template"), () =>
			crema_new_provider_dialog(() => listview.refresh())
		);
	},

	before_render() {
		crema_prefetch(crema_provider_listview);
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
			const status = crema_connection_cache[doc.name];
			if (!status) return `<span class="text-muted">${__("Checking…")}</span>`;
			// badge.html HTML-escapes the label itself.
			return frappe.ui.badge.html({
				label: status.detail,
				theme: status.ok ? "green" : "red",
			});
		},
	},
};
