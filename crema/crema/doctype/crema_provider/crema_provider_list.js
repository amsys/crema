// Copyright (c) 2026, Crema and contributors
// For license information, please see license.txt

// crema_new_provider_dialog is defined once in crema.bundle.js (app_include_js, so it
// is already loaded here) and shared with the Crema Settings form.
frappe.listview_settings["Crema Provider"] = {
	onload(listview) {
		listview.page.add_inner_button(__("New from Template"), () =>
			crema_new_provider_dialog(() => listview.refresh())
		);
	},
};
