// Copyright (c) 2026, Crema and contributors
// For license information, please see license.txt

/* global crema_show_error */
// Warn before a delete quietly resets Crema Settings: if this provider is the
// Default Provider, or a use case's own Model Assignment row names it directly,
// deleting it clears those references (see crema_provider.py's
// _release_from_settings, run from on_trash). Frappe has no client-side
// before_delete event and its own delete confirmation text isn't extensible, so
// this banner is the only place the consequence is visible before the admin
// commits — the msgprint after the delete (which also covers the list view's own
// delete and bulk delete) is the other half.

frappe.ui.form.on("Crema Provider", {
	refresh(frm) {
		warn_if_referenced(frm);
	},
});

function warn_if_referenced(frm) {
	if (frm.is_new()) return;
	frappe.call({
		method: "crema.crema.doctype.crema_provider.crema_provider.get_settings_references",
		args: { provider: frm.doc.name },
		callback(r) {
			const { is_default, use_cases } = r.message || {};
			if (!is_default && !(use_cases || []).length) return;

			const parts = [];
			if (is_default) parts.push(__("the Default Provider (and Default Model)"));
			if (use_cases && use_cases.length) {
				parts.push(__("the use case(s) {0}", [use_cases.map((u) => `'${u}'`).join(", ")]));
			}
			frm.dashboard.add_comment(
				__("Crema Settings names this as {0}. Deleting it will clear that.", [
					parts.join(__(" and ")),
				]),
				"red",
				true
			);
		},
		error: crema_show_error,
	});
}
