// Copyright (c) 2026, Crema and contributors
// For license information, please see license.txt

/* global crema_show_error */
// crema_show_error is defined once in crema.bundle.js (app_include_js, already loaded
// here) — every mutating frappe.call needs it explicitly, or the button silently does
// nothing on failure (see crema.bundle.js's own comment on crema_new_provider_dialog).

// word has no in_list_view: autoname is field:word, so the subject column is already
// the word — title_field + hide_name_column below keep it to one, same convention as
// Crema Provider's list. enabled is read by get_indicator, so it stays off the columns
// too (a Select/Check column would otherwise get a second, redundant pill).
frappe.listview_settings["Crema Health Word"] = {
	hide_name_column: true,
	add_fields: ["enabled"],

	get_indicator(doc) {
		return doc.enabled ? [__("On"), "green", "enabled,=,1"] : [__("Off"), "gray", "enabled,=,0"];
	},

	onload(listview) {
		listview.page.add_inner_button(__("Translate Built-in List"), () =>
			crema_translate_health_words_dialog(() => listview.refresh())
		);
	},
};

function crema_translate_health_words_dialog(on_done) {
	const dialog = new frappe.ui.Dialog({
		title: __("Translate Built-in List"),
		fields: [
			{
				fieldname: "language",
				fieldtype: "Link",
				label: __("Language"),
				options: "Language",
				reqd: 1,
				description: __(
					"Crema asks its Translation use case for this language's version of the built-in word list. New words are filed switched off, for you to review and enable."
				),
			},
		],
		primary_action_label: __("Translate"),
		primary_action(values) {
			frappe.call({
				method: "crema.crema.doctype.crema_health_word.crema_health_word.translate_words",
				args: values,
				freeze: true,
				callback(r) {
					dialog.hide();
					const { added, skipped } = r.message || {};
					frappe.msgprint(
						__("Added {0} new word(s), switched off. Skipped {1} already on the list.", [
							added || 0,
							skipped || 0,
						])
					);
					if (on_done) on_done();
				},
				error: crema_show_error,
			});
		},
	});
	dialog.show();
}
