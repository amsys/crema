// Covers crema_allowed() (crema.bundle.js: Crema User OR System Manager) and the "you
// lack the role" notice on Crema Settings (crema_settings.js crema_explain_user_role).
//
// Neither is reachable while logged in as Administrator: frappe.permissions.get_roles
// hands Administrator every Role in the system (permissions.py), so
// frappe.user_roles.includes("Crema User") is always true for that account. This file
// logs in as two purpose-built users instead: one with neither role (the button must not
// exist at all), one a System Manager without Crema User (the settings-page banner and
// its self-service grant button).
//
// Tests run in sequence within the file: later ones depend on state earlier ones create.
import { randomUUID } from "node:crypto";
import { test, expect, goto_desk, login_as } from "../support";

// Generated per run rather than committed: a literal reads as a hard-coded credential
// to static analysis, and both users are created and deleted inside this file's own
// beforeAll/afterAll. Nothing outside the run needs the value.
const PASSWORD = `pw-${randomUUID()}`;
const NO_ROLE_USER = "pw-crema-no-role@example.com";
const SYS_MGR_USER = "pw-crema-sysmgr@example.com";
const THROWAWAY_ROLE = "_pw_crema_no_perm";

async function remove_fixtures(admin) {
	await admin.remove_doc("User", NO_ROLE_USER, true);
	await admin.remove_doc("User", SYS_MGR_USER, true);
	await admin.remove_doc("Role", THROWAWAY_ROLE, true);
}

test.describe("Crema role gate", () => {
	test.describe.configure({ mode: "serial" });

	test.beforeAll(async ({ admin }) => {
		// An aborted earlier run leaves these users behind with "Crema User" already
		// granted (test 2's own grant_crema_role call), so reusing them would carry that
		// contaminated state into tests that assume a fresh one. Delete first,
		// unconditionally, so this suite is self-healing regardless of prior runs.
		await remove_fixtures(admin);

		await admin.insert_doc("Role", { role_name: THROWAWAY_ROLE, desk_access: 1 });
		// Contact (what desk_ui drives) grants read/write/create/delete to the built-in
		// "All" role, which every user holds regardless of any role assigned here, so
		// neither user needs a Contact-specific permission to reach /desk/contact.
		await admin.insert_doc("User", {
			email: NO_ROLE_USER,
			first_name: "Playwright No Role",
			send_welcome_email: 0,
			new_password: PASSWORD,
			roles: [{ role: THROWAWAY_ROLE }],
		});
		await admin.insert_doc("User", {
			email: SYS_MGR_USER,
			first_name: "Playwright Sys Mgr",
			send_welcome_email: 0,
			new_password: PASSWORD,
			roles: [{ role: "System Manager" }],
		});
	});

	test.afterAll(async ({ admin }) => {
		await remove_fixtures(admin);
	});

	test("shows no robot button and no awesomebar entry for a user with neither role", async ({
		page,
	}) => {
		await login_as(page, NO_ROLE_USER, PASSWORD);
		await goto_desk(page, "/desk/contact", NO_ROLE_USER);

		await expect(page.locator("[data-crema]")).toHaveCount(0);

		await page.locator(".navbar-modal-search-mobile:visible").click();
		await page.locator("#navbar-search").pressSequentially("overdue items");
		const awesomebar = page.locator(".awesomplete", { has: page.locator("#navbar-search") });
		// The list must have rendered its other entries before the absence of one means anything.
		await expect(awesomebar.getByRole("listbox")).toContainText("Search for overdue items");
		await expect(awesomebar).not.toContainText("Ask overdue items");
	});

	test("shows the robot button once an admin grants Crema User", async ({ page, admin }) => {
		// grant_crema_role(user) is the same production endpoint the Settings button below
		// calls for the caller's own session. Here it is called for someone else's, which
		// only a System Manager (Administrator) may do.
		await admin.call("crema.api.grant_crema_role", { user: NO_ROLE_USER });

		await login_as(page, NO_ROLE_USER, PASSWORD);
		await goto_desk(page, "/desk/contact", NO_ROLE_USER);

		await expect(page.locator("[data-crema]")).toBeVisible();

		await page.locator(".navbar-modal-search-mobile:visible").click();
		await page.locator("#navbar-search").pressSequentially("overdue items");
		await expect(
			page
				.locator(".awesomplete", { has: page.locator("#navbar-search") })
				.getByRole("listbox")
		).toContainText("Ask overdue items");
	});

	test("tells a System-Manager-only user they lack the role, with a self-service grant button", async ({
		page,
	}) => {
		// crema_allowed() is Crema User OR System Manager. This user can already use
		// Crema (the button above is visible for them too), but crema_explain_user_role on
		// Crema Settings still flags that they personally don't hold Crema User, since
		// that's what everyone *else* on the site needs to see the robot button.
		await login_as(page, SYS_MGR_USER, PASSWORD);
		await goto_desk(page, "/desk/crema-settings", SYS_MGR_USER);

		const grant = page.locator("button", { hasText: "Give Me the Crema User Role" });
		await expect(page.getByText("You do not have the Crema User role")).toBeVisible();
		await expect(grant).toBeVisible();

		await grant.click();
		await expect(page.locator(".es-toast")).toContainText("Role added");

		await page.reload();
		await expect(
			page.getByText(
				"Other people need the Crema User role to see the Ask Crema robot button."
			)
		).toBeVisible();
		await expect(grant).toHaveCount(0);
	});
});
