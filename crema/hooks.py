app_name = "crema"
app_title = "Crema"
app_publisher = "martin@its.mu"
app_description = "LLM interface and security layer for Frappe apps"
app_email = "martin@its.mu"
app_license = "mit"

# Apps
# ------------------

required_apps = ["frappe"]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
app_include_css = "crema.bundle.css"
app_include_js = "crema.bundle.js"

# Desk workspace dock
# --------------------

# Crema is a layer inside the framework, not an app of its own -- pin its workspace into
# Framework's rail (frappe.boot.get_app_rail_map) instead of taking its own apps-screen
# slot. on_apps_screen is False for a dock app (boot.py: "the dock hook wins"), and
# app_route/app_title then fall back to the workspace's own route ("/desk/crema") and to
# app_title above -- nothing here is lost by dropping add_to_apps_screen.
add_to_workspace_dock = [{"app": "frappe", "workspace": "Crema"}]

# Boot
# ----

# Publishes frappe.boot.crema_blocked_doctypes so the desk UI can fence its own write
# shapes -- Crema Settings (where the list is entered) is System-Manager read-only, so
# this is the only way an ordinary Crema User sees it at all. See crema/policy.py.
extend_bootinfo = "crema.policy.extend_bootinfo"

# Installation
# ------------

after_install = "crema.install.after_install"

# Re-run the interface + dashboard seeding on every migrate so a name added to
# interfaces.PREDEFINED later, an interface a newly installed app registers through the
# crema_interfaces hook, or a Number Card an admin deleted, shows up again without a
# manual step. sync_interface_options keeps the Crema Automation Task interface picker's
# meta options in sync with the same set.
after_migrate = [
    "crema.install.sync_interfaces",
    "crema.install.sync_dashboard",
    "crema.install.sync_interface_options",
    "crema.install.sync_guardrail_options",
    "crema.install.sync_guardrails",
    "crema.install.sync_notification",
    "crema.install.sync_example_task",
]

# Document Events
# ---------------

# A wildcard handler, so it runs on every document write on the site. on_doc_event is
# written to be a flag check plus one dict lookup on a cached map for that reason — see
# crema/automation.py. A Document Event automation task is the only thing it can act on.
doc_events = {
    "*": {
        "after_insert": "crema.automation.on_doc_event",
        "on_update": "crema.automation.on_doc_event",
        "on_submit": "crema.automation.on_doc_event",
    }
}

# Scheduled Tasks
# ---------------

# The 15-minute tick is the granularity floor for a Crema Automation Task's cron
# expression: a finer schedule still runs at most once per tick.
scheduler_events = {
    "cron": {"*/15 * * * *": ["crema.automation.tick"]},
    "hourly": ["crema.client.check_providers"],
    "daily": ["crema.automation.cleanup_logs"],
}
