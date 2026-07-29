// Copyright (c) 2026, Crema and contributors
// For license information, please see license.txt

// crema_show_error is defined once in crema.bundle.js (app_include_js, so it's already
// loaded as window.crema_show_error by the time this form script runs).
frappe.ui.form.on("Crema Automation Task", {
	onload(frm) {
		frappe.call({
			method: "crema.api.get_interfaces",
			callback(r) {
				frm.set_df_property("interface", "options", (r.message || []).join("\n"));
			},
			// Without this the Interface Select is just empty on failure, with nothing to
			// tell the user why — see crema.bundle.js's crema_show_error.
			error: crema_show_error,
		});
	},
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Run Now"), () => {
			frappe.call({
				method: "crema.api.run_automation_now",
				args: { task: frm.doc.name },
				freeze: true,
				callback(r) {
					frappe.show_alert({
						message: __("Queued as {0}", [r.message]),
						indicator: "green",
					});
				},
				error: crema_show_error,
			});
		});
	},
});
