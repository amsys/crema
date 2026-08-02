// Copyright (c) 2026, Crema and contributors
// For license information, please see license.txt

// Without this the list shows last_status as plain text, so a failing task looks exactly
// like a healthy one. Mirrors crema_provider_list.js.
frappe.listview_settings["Crema Automation Task"] = {
	add_fields: ["enabled", "last_status"],
	get_indicator(doc) {
		if (!doc.enabled) {
			return [__("Disabled"), "gray", "enabled,=,0"];
		}
		const colors = { Success: "green", Replanned: "orange", Failed: "red" };
		if (!doc.last_status) {
			return [__("Never run"), "blue", "last_status,=,"];
		}
		return [__(doc.last_status), colors[doc.last_status] || "gray", `last_status,=,${doc.last_status}`];
	},
};
