// Copyright (c) 2026, Crema and contributors
// For license information, please see license.txt

frappe.ui.form.on("Crema Automation Task", {
	onload(frm) {
		frappe.call({
			method: "crema.api.get_interfaces",
			callback(r) {
				frm.set_df_property("interface", "options", (r.message || []).join("\n"));
			},
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
			});
		});
	},
});
