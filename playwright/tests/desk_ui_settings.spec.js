// Covers the Crema Settings form (crema_settings.js) and the Crema Guardrails form
// (crema_guardrails.js). The one model-adjacent call, get_models, is stubbed with
// page.route; the backstop fixture fails any billable call that no stub answered.
import { test, expect, goto_desk, count_requests } from "../support";

const SAVEDOCS = "frappe.desk.form.save.savedocs";

test.describe("Crema Settings", () => {
	test.beforeEach(async ({ page }) => {
		await goto_desk(page, "/desk/crema-settings");
		// A logged-out visit 301s to /login, where every not-present check below passes
		// vacuously — assert the form actually rendered.
		await expect(page.locator('[data-fieldname="assignments"]')).toBeAttached();
	});

	test.afterAll(async ({ admin }) => {
		// The kill switch stops every LLM call site-wide, and the specs that follow this
		// file assume it is off — so it must not survive a test that failed halfway
		// through toggling it.
		await admin.set_value("Crema Settings", "Crema Settings", {
			disabled: 0,
			auto_disable_unreachable: 0,
			default_monthly_budget_usd_per_user: 0,
		});
	});

	// The Use Cases grid always shows exactly one row per interfaces.names()
	// (core PREDEFINED + whatever installed apps register via the crema_interfaces
	// hook), seeded at install/migrate, none addable or removable (crema_settings.js
	// sets grid.df.cannot_add_rows / cannot_delete_rows). Counted live rather than
	// hardcoded, since the app-registered half varies per site.
	//
	// get_interfaces() is the narrower list the automation task's AI Profile picker
	// shows — interfaces.selectable(), i.e. names() minus interfaces.INTERNAL (1:
	// advanced_ocr) and minus interfaces.NOT_FOR_TASKS (4: view, transform, ocr,
	// transcribe). The grid still carries a row for each of those five, so it is a
	// superset by exactly that count.
	const UNSELECTABLE_INTERFACE_COUNT = 5;

	test("shows the full set of model assignment rows with no add/delete affordance", async ({
		page,
		api,
	}) => {
		const selectable = (await api.call("crema.api.get_interfaces")).message;
		// .grid-row also matches the heading row (grid.js nests one inside
		// .grid-heading-row) — data rows live in .grid-body .rows.
		await expect(
			page.locator('[data-fieldname="assignments"] .grid-body .rows > .grid-row')
		).toHaveCount(selectable.length + UNSELECTABLE_INTERFACE_COUNT);

		// The grid always renders its bulk-action buttons (Add row, Delete rows, …) in the
		// DOM — cannot_add_rows/cannot_delete_rows only ever CSS-hides them, so the real
		// assertion is visibility, not existence.
		await expect(
			page
				.locator('[data-fieldname="assignments"]')
				.locator("button, a")
				.filter({ visible: true, hasText: /add row|delete/i })
		).toHaveCount(0);
	});

	// Providers are the Crema Provider list view now, not a panel on this form, and the
	// sidebar lists Providers above Settings — so the form carries no link of its own.
	// The one case it does interrupt for is having no enabled provider at all, since
	// nothing here resolves without one.
	test("does not link out to the provider list", async ({ page }) => {
		await expect(page.locator("button").filter({ hasText: /^AI Services$/ })).toHaveCount(0);
	});

	// Stubbed rather than acted out: the alternative is disabling every provider on the
	// site mid-suite, and the count call is the only input the warning reads.
	test("warns when no provider is switched on", async ({ page }) => {
		await page.route(/\/api\/method\/frappe\.desk\.reportview\.get_count(\?|$)/, (route) =>
			route.fulfill({ status: 200, json: { message: 0 } })
		);
		await goto_desk(page, "/desk/crema-settings");
		await expect(page.getByText("No AI service is switched on")).toBeVisible();
		await expect(
			page.locator("button").filter({ hasText: /Add an AI Service/ })
		).toBeVisible();
	});

	// The role gate itself — no role, System Manager without the role, System Manager
	// with the role — is covered end to end in role_gate.spec.js: as Administrator,
	// frappe.permissions.get_roles hands every role including Crema User, so the "you
	// lack the role" banner can never appear here.
	test("does not warn Administrator about a missing role", async ({ page }) => {
		await expect(page.getByText("You do not have the Crema User role")).toHaveCount(0);
	});

	// crema_load_default_models fetches Default Model's data from Default Provider (an
	// Autocomplete with no built-in source of its own) and, per CLAUDE.md's "flag
	// uncertainty" rule for a model that has drifted out of the provider's list, paints an
	// inline red warning rather than failing silently.
	test("warns inline when the default model isn't offered by the default provider", async ({
		page,
	}) => {
		const GET_MODELS = /\/api\/method\/crema\.api\.get_models(\?|$)/;
		await page.route(GET_MODELS, (route) =>
			route.fulfill({ status: 200, json: { message: ["gpt-4o"] } })
		);
		const get_models_calls = count_requests(
			page,
			(request) => request.method() === "POST" && GET_MODELS.test(request.url())
		);

		// Wait for a field to actually render before touching cur_frm. The rendered control
		// alone does not prove the doctype controller is wired: its JS arrives with the
		// form's own requests, and a set_value fired before they settle changes the doc
		// without running the default_provider handler (seen on the CI runner). Let
		// frappe's after_ajax settle them first.
		await expect(
			page.locator('.frappe-control[data-fieldname="default_provider"]')
		).toBeAttached();
		await page.evaluate(() => new Promise((resolve) => frappe.after_ajax(resolve)));

		// set_value, not typing into the Link field: this only exercises the client-side
		// warning, and the field's server-side existence is validated at save, not here.
		// Blank first: set_value skips the change handler when the value is unchanged,
		// and this test cannot know what the site already holds.
		await page.evaluate(() => cur_frm.set_value("default_provider", ""));
		await page.evaluate(() => cur_frm.set_value("default_provider", "Test Provider"));
		await expect
			.poll(() => get_models_calls.length, { timeout: 20000 })
			.toBeGreaterThanOrEqual(1);
		await page.evaluate(() => cur_frm.set_value("default_model", "made-up-model"));
		await expect
			.poll(() => get_models_calls.length, { timeout: 20000 })
			.toBeGreaterThanOrEqual(2);

		await expect(
			page.locator(".frappe-control[data-fieldname=default_model] .help-box")
		).toContainText("is not offered by");
	});

	// The Operations section — the site-wide kill switch and the hourly reachability
	// sweep — plus the per-user ceiling beside the site-wide budget. None of the three has
	// any client-side behaviour of its own in crema_settings.js: `disabled` is read by
	// policy.disabled on the server at call time, auto_disable_unreachable by the hourly
	// client.check_providers sweep, and the per-user ceiling by client._load_from_db. So
	// what a browser can check is that each field is on the form and that a save keeps it.
	test("carries the kill switch, the per-user budget and the auto-disable check", async ({
		page,
	}) => {
		await expect(page.locator('.frappe-control[data-fieldname="disabled"]')).toContainText(
			"Crema Is Off"
		);
		await expect(
			page.locator('.frappe-control[data-fieldname="auto_disable_unreachable"]')
		).toContainText("Switch Off Services That Fail");
		await expect(
			page.locator('.frappe-control[data-fieldname="default_monthly_budget_usd_per_user"]')
		).toContainText("Per-User Monthly Budget");
	});

	test("saves all three Operations settings, then switches the kill switch back off", async ({
		page,
	}) => {
		const kill_switch = page.locator(
			'.frappe-control[data-fieldname="disabled"] .input-area input[type="checkbox"]'
		);
		const auto_disable = page.locator(
			'.frappe-control[data-fieldname="auto_disable_unreachable"] .input-area input[type="checkbox"]'
		);

		await kill_switch.check();
		await auto_disable.check();
		await page.evaluate(() => cur_frm.set_value("default_monthly_budget_usd_per_user", 12));
		let saved = page.waitForResponse((res) => res.url().includes(SAVEDOCS));
		await page.evaluate(() => cur_frm.save());
		await saved;
		await expect(page.locator(".page-head")).not.toContainText("Not Saved");

		// A full reload rather than reading cur_frm again: the point is that the three
		// values reached the database, not that the form still holds what was set on it.
		await goto_desk(page, "/desk/crema-settings");
		await expect.poll(() => page.evaluate(() => cur_frm?.doc?.disabled)).toBe(1);
		await expect
			.poll(() => page.evaluate(() => cur_frm?.doc?.auto_disable_unreachable))
			.toBe(1);
		await expect
			.poll(() => page.evaluate(() => cur_frm?.doc?.default_monthly_budget_usd_per_user))
			.toBe(12);

		// Switched back off here, not only in afterAll: every test after this one — in
		// this file and the next — runs against a site whose LLM calls still work.
		await kill_switch.uncheck();
		await auto_disable.uncheck();
		await page.evaluate(() => cur_frm.set_value("default_monthly_budget_usd_per_user", 0));
		saved = page.waitForResponse((res) => res.url().includes(SAVEDOCS));
		await page.evaluate(() => cur_frm.save());
		await saved;
		await expect(page.locator(".page-head")).not.toContainText("Not Saved");

		await goto_desk(page, "/desk/crema-settings");
		await expect.poll(() => page.evaluate(() => cur_frm?.doc?.disabled)).toBe(0);
	});
});

test.describe("Crema Guardrails", () => {
	test.beforeEach(async ({ page }) => {
		await goto_desk(page, "/desk/crema-guardrails");
		// A logged-out visit 301s to /login — assert the form actually rendered.
		await expect(page.locator('[data-fieldname="guardrails"]')).toBeAttached();
	});

	// crema_guardrails.js renders one "label — help" line per registered check into
	// the guardrail_help HTML field, from the same get_guardrails call that relabels
	// the Guardrail picker — so a reader learns what each built-in does without
	// opening the dropdown.
	test("lists every built-in check with a one-line description above the grid", async ({
		page,
	}) => {
		const help = page.locator('[data-fieldname="guardrail_help"]');
		for (const label of [
			"Text Scan",
			"Reply Filter",
			"Hide Personal Information",
			"Hide Health Information",
			"Reply Check",
		]) {
			await expect(help.locator("strong").filter({ hasText: label })).toBeVisible();
		}
		await expect(help.getByText("prompt injection")).toBeVisible();
	});

	// The brief used to be the grid's own field description, rendered below the grid
	// under a redundant "Guardrails" heading — crema_render_check_list now puts it first,
	// inside the same HTML field, above the one-liners it introduces.
	test("puts the brief above the one-liners and drops the Guardrails heading", async ({
		page,
	}) => {
		const brief = page.locator('[data-fieldname="guardrail_help"] > div > :first-child');
		await expect(brief).toHaveJSProperty("tagName", "P");
		await expect(brief).toContainText("top to bottom");
		await expect(page.locator('[data-fieldname="guardrails"]')).not.toContainText(
			"Guardrails"
		);
	});
});
