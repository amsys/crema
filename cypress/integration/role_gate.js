// Covers crema_allowed() (crema.bundle.js: Crema User OR System Manager) and the "you
// lack the role" notice on Crema Settings (crema_settings.js explain_crema_user_role).
//
// Neither is reachable while logged in as Administrator: frappe.permissions.get_roles
// hands Administrator every Role in the system (permissions.py), so
// frappe.user_roles.includes("Crema User") is always true for that account — the old
// spec asserting the yellow banner as Administrator could never pass. This file logs in
// as two purpose-built users instead: one with neither role (the button must not exist
// at all), one a System Manager without Crema User (the settings-page banner and its
// self-service grant button).
//
// Own file, run last (spec files run in glob order; this name sorts after
// automation_task.js and desk_ui.js) so a non-Administrator session never bleeds into
// another spec. Tests run in sequence within the file — later ones depend on state
// earlier ones create, same as the fixture reuse in automation_task.js.
//
// Run with: bench --site fcr.local run-ui-tests crema --headless
// Generated per run rather than committed: a literal reads as a hard-coded credential
// to static analysis, and both users are created and deleted inside this file's own
// before/after — nothing outside the run needs the value. Cypress._ is lodash, bundled
// with cypress; crypto.randomUUID() needs a secure context, which this bench site
// (plain http, non-localhost host fcr.local) doesn't have.
const PASSWORD = `cypress-${Cypress._.random(1e15)}`;
const NO_ROLE_USER = "cypress-crema-no-role@example.com";
const SYS_MGR_USER = "cypress-crema-sysmgr@example.com";
const THROWAWAY_ROLE = "_cypress_crema_no_perm";

context("Crema role gate", () => {
	before(() => {
		cy.visit("/login");
		cy.login(); // Administrator

		// insert_doc(..., ignore_duplicate) silently skips the insert when the doc already
		// exists — an aborted earlier run leaves these users behind with "Crema User"
		// already granted (test 2/3's own grant_crema_role call), so a plain insert reuses
		// that contaminated state instead of the fresh one every test here assumes. Delete
		// first, unconditionally, so this suite is self-healing regardless of prior runs.
		cy.remove_doc("User", NO_ROLE_USER, true);
		cy.remove_doc("User", SYS_MGR_USER, true);
		cy.remove_doc("Role", THROWAWAY_ROLE, true);

		cy.insert_doc("Role", { role_name: THROWAWAY_ROLE, desk_access: 1 }, true);
		// Contact (what desk_ui.js drives) grants read/write/create/delete to the built-in
		// "All" role, which every user holds regardless of any role assigned here — so
		// neither user needs a Contact-specific permission to reach /desk/contact.
		cy.insert_doc(
			"User",
			{
				email: NO_ROLE_USER,
				first_name: "Cypress No Role",
				send_welcome_email: 0,
				new_password: PASSWORD,
				roles: [{ role: THROWAWAY_ROLE }],
			},
			true
		);
		cy.insert_doc(
			"User",
			{
				email: SYS_MGR_USER,
				first_name: "Cypress Sys Mgr",
				send_welcome_email: 0,
				new_password: PASSWORD,
				roles: [{ role: "System Manager" }],
			},
			true
		);
	});

	after(() => {
		cy.visit("/login");
		cy.login(); // back to Administrator to clean up
		cy.remove_doc("User", NO_ROLE_USER, true);
		cy.remove_doc("User", SYS_MGR_USER, true);
		cy.remove_doc("Role", THROWAWAY_ROLE, true);
	});

	it("shows no robot button and no awesomebar entry for a user with neither role", () => {
		cy.login(NO_ROLE_USER, PASSWORD);
		cy.visit("/desk/contact");

		cy.get("[data-crema]").should("not.exist");

		cy.get("#navbar-modal-search").click();
		cy.get("#navbar-search").type("overdue items");
		cy.wait(400);
		cy.get("#navbar-search")
			.parents(".awesomplete")
			.should("not.contain.text", "Ask overdue items");
	});

	it("shows the robot button once an admin grants Crema User", () => {
		// grant_crema_role(user) is the same production endpoint the Settings button below
		// calls for the caller's own session — here called for someone else's, which only a
		// System Manager (Administrator) may do.
		cy.visit("/login");
		cy.login(); // Administrator
		cy.visit("/desk/contact"); // establishes window.frappe for cy.call's csrf token
		cy.call("crema.api.grant_crema_role", { user: NO_ROLE_USER });

		cy.login(NO_ROLE_USER, PASSWORD);
		cy.visit("/desk/contact");

		cy.get("[data-crema]").should("be.visible");

		cy.get("#navbar-modal-search").click();
		cy.get("#navbar-search").type("overdue items");
		cy.wait(400);
		cy.get("#navbar-search")
			.parents(".awesomplete")
			.findByRole("listbox")
			.should("contain.text", "Ask overdue items");
	});

	it("tells a System-Manager-only user they lack the role, with a self-service grant button", () => {
		// crema_allowed() is Crema User OR System Manager — this user can already use
		// Crema (the button above is visible for them too), but explain_crema_user_role on
		// Crema Settings still flags that they personally don't hold Crema User, since
		// that's what everyone *else* on the site needs to see the robot button.
		cy.login(SYS_MGR_USER, PASSWORD);
		cy.visit("/app/crema-settings");

		cy.contains("You do not have the Crema User role").should("be.visible");
		cy.contains("button", "Give Me the Crema User Role").should("be.visible");

		cy.contains("button", "Give Me the Crema User Role").click();
		cy.get(".desk-alert").should("contain", "Role added");

		cy.reload();
		cy.contains(
			"Other people need the Crema User role to see the Ask Crema robot button."
		).should("be.visible");
		cy.contains("button", "Give Me the Crema User Role").should("not.exist");
	});
});
