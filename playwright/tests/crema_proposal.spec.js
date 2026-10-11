// Covers the Crema Proposal review workflow: the form (crema_proposal.js: the
// diff_preview table and the Approve/Discard/Undo buttons) and the list view
// (crema_proposal_list.js: the three bulk actions and the preview dialog Approve asks
// for before it writes anything).
//
// The three crema.api.*_proposals endpoints run for real here, except in the one test
// that stubs a failed approval. The rule the other specs follow is "never call a live
// model", and none of these does: apply_proposal replays the parked write through the
// same _upsert an unattended run uses, and undo_proposal deletes what that wrote. A stub
// could never show a row move to Approved, which is the whole point of this doctype.
//
// The price is a configured site: apply_proposal and undo_proposal both resolve the
// task's use case through client._resolve, which needs an enabled provider and an
// isolation user. beforeAll seeds those the same way crema.testing.seed_provider seeds
// them for the Python suite. The seed is non-overriding, so a bench that is already
// configured is left exactly as it is, and afterAll clears only what this file set.
import { test, expect, goto_desk, dialog_shown } from "../support";

const TASK = "_pw_crema_proposal_task";
const PROVIDER = "_pw_crema_proposal_provider";
const RUNNER = "pw-crema-proposal-runner@example.com";
const RUNNER_ROLE = "_pw_crema_proposal_role";

// What each parked row maps onto. Contact has two plain Data fields to write
// (first_name, designation), and read/write/create/delete for the built-in "All" role.
// So the low-privilege account the approval runs as needs no permission of its own.
const MAPPING = JSON.stringify({
	doctype: "Contact",
	match_fields: ["first_name"],
	field_map: { who: "first_name", what: "designation" },
});

// A payload key the field_map does not name. crema_proposal_record must drop it: only
// the mapped keys are ever written, so only those may appear in the preview.
const UNMAPPED = "must never be written";

const PROPOSALS = [
	{ fingerprint: "_pw_crema_proposal_one", who: "_pw Proposal One", what: "Tester" },
	{ fingerprint: "_pw_crema_proposal_two", who: "_pw Proposal Two", what: "Reviewer" },
];

// Filled by seed_proposals() in beforeEach. The rows are hash-named, so the name is
// only known once the insert comes back.
let names = [];

// Whether this file seeded Crema Settings' defaults, i.e. whether afterAll has anything
// to undo there. It also holds the values the site had before, so afterAll restores them
// instead of blanking: a bench can carry a Default Isolation User with no Default
// Provider, and that user is not this file's to clear.
let seeded_defaults = false;
let prior_defaults = { default_model: "", default_isolation_user: "" };

// add_custom_button renders into the page header (.page-actions), so every lookup of an
// Approve/Discard/Undo button is scoped there. An unscoped lookup of "Discard" also
// matches the form timeline's own comment-box Discard button, which frappe keeps in the
// DOM with display:none.
function action_button(page, label) {
	return page.locator(".page-actions").locator("button", { hasText: label });
}

// Frappe never removes a hidden dialog from the DOM, so .modal.show is the only reliable
// handle on the visible one.
function crema_modal(page) {
	return page.locator(".modal.show");
}

function proposal_rows(admin, fields) {
	return admin.get_list("Crema Proposal", fields, [["task", "=", TASK]]);
}

// Undo before delete: an Approved row wrote a real Contact, and deleting the row on its
// own would strand it. undo_proposals reports a row that is not Approved as a failure and
// changes nothing, which is exactly what a Pending or Discarded row needs.
async function clear_proposals(admin) {
	const stale = (await proposal_rows(admin, ["name"])).map((row) => row.name);
	if (!stale.length) return;
	await admin.call("crema.api.undo_proposals", { names: stale });
	for (const name of stale) await admin.remove_doc("Crema Proposal", name, true);
}

// An approval that matched an existing Contact updates it instead of creating it, and
// leaves nothing for Undo to delete. A Contact left behind by an aborted run would turn
// the "the record is gone again" assertions below into vacuous ones. Sweep them,
// unconditionally.
async function clear_contacts(admin) {
	const rows = await admin.get_list(
		"Contact",
		["name"],
		[["first_name", "in", PROPOSALS.map((row) => row.who)]]
	);
	for (const row of rows) await admin.remove_doc("Contact", row.name, true);
}

function contacts_named(admin, first_name) {
	return admin.get_list("Contact", ["name"], [["first_name", "=", first_name]]);
}

async function seed_proposals(admin) {
	names = [];
	for (const row of PROPOSALS) {
		const doc = await admin.insert_doc("Crema Proposal", {
			task: TASK,
			target_doctype: "Contact",
			fingerprint: row.fingerprint,
			payload_json: JSON.stringify({
				who: row.who,
				what: row.what,
				unmapped_key: UNMAPPED,
			}),
			mapping_json: MAPPING,
		});
		names.push(doc.name);
	}
}

test.describe("Crema Proposal review", () => {
	test.beforeAll(async ({ admin }) => {
		// Self-healing: an aborted earlier run leaves these behind, and reusing them
		// would reuse whatever state it left on them. The proposals go first. They link
		// to the task, which links to the user.
		await clear_proposals(admin);
		await clear_contacts(admin);
		await admin.remove_doc("Crema Automation Task", TASK, true);
		await admin.remove_doc("User", RUNNER, true);
		await admin.remove_doc("Role", RUNNER_ROLE, true);

		await admin.insert_doc("Role", { role_name: RUNNER_ROLE, desk_access: 1 });
		await admin.insert_doc("User", {
			email: RUNNER,
			first_name: "Playwright Proposal Runner",
			send_welcome_email: 0,
			roles: [{ role: RUNNER_ROLE }],
		});

		// seed_provider's rule, in playwright: fill a blank site, never overwrite a
		// configured one. A bench that already has a Default Provider resolves
		// apply_proposal on its own, and its admin's settings are not this file's to
		// rewrite.
		const settings = (
			await admin.call("frappe.client.get", {
				doctype: "Crema Settings",
				name: "Crema Settings",
			})
		).message;
		seeded_defaults = false;
		if (!settings.default_provider) {
			seeded_defaults = true;
			prior_defaults = {
				default_model: settings.default_model || "",
				default_isolation_user: settings.default_isolation_user || "",
			};
			await admin.remove_doc("Crema Provider", PROVIDER, true);
			await admin.insert_doc("Crema Provider", {
				provider_name: PROVIDER,
				// A loopback base URL is the one case CremaProvider.validate enables
				// without an api_key. Nothing here ever calls it: no test in this
				// file reaches a model.
				base_url: "http://localhost:11434/v1",
				enabled: 1,
				timeout_seconds: 5,
			});
			// One call, not three: CremaSettings._validate_provider_isolation_pairing
			// refuses a Default Provider with no Default Isolation User, so the pair has
			// to arrive in the same save. default_model spares that save
			// _warn_missing_models' dialog.
			await admin.set_value("Crema Settings", "Crema Settings", {
				default_provider: PROVIDER,
				default_model: "test-model",
				default_isolation_user: RUNNER,
			});
		}

		await admin.insert_doc("Crema Automation Task", {
			task_name: TASK,
			enabled: 0,
			trigger: "Schedule",
			schedule_preset: "Daily 03:00",
			sources: [
				{
					source_type: "Document Query",
					source_doctype: "Contact",
					source_filters: "[]",
					source_limit: 50,
				},
			],
			interface: "complex",
			instruction: "Park every contact for review.",
			action: "Propose Only",
			target_doctype: "Contact",
			// Neither Administrator nor a System Manager: validate_isolation_user
			// refuses both. This is the account apply_proposal writes the Contact as.
			run_as: RUNNER,
		});
	});

	test.beforeEach(async ({ admin }) => {
		// Every test starts from two fresh Pending rows and no leftover Contact: the
		// tests below approve and discard for real, so nothing here may inherit the
		// previous test's outcome.
		await clear_proposals(admin);
		await clear_contacts(admin);
		await seed_proposals(admin);
	});

	test.afterAll(async ({ admin }) => {
		await clear_proposals(admin);
		await clear_contacts(admin);
		await admin.remove_doc("Crema Automation Task", TASK, true);
		if (seeded_defaults) {
			// CremaProvider.on_trash (_release_from_settings) clears Default Provider and
			// Default Model itself. The model and isolation user are restored to what
			// the site held before this file seeded, and the restore has to land before
			// the User the seed named can be deleted.
			await admin.remove_doc("Crema Provider", PROVIDER, true);
			await admin.set_value("Crema Settings", "Crema Settings", {
				default_model: prior_defaults.default_model,
				default_isolation_user: prior_defaults.default_isolation_user,
			});
		}
		await admin.remove_doc("User", RUNNER, true);
		await admin.remove_doc("Role", RUNNER_ROLE, true);
	});

	test("renders the parked write as a table of the fields it would write", async ({ page }) => {
		await goto_desk(page, `/desk/crema-proposal/${names[0]}`);

		// The point of the doctype is a human reading the write before it happens, so
		// diff_preview shows it as a table. The raw payload and mapping stay in the
		// collapsed Detail section below.
		const preview = page.locator('[data-fieldname="diff_preview"] table');
		await expect(preview).toContainText("first_name");
		await expect(preview).toContainText(PROPOSALS[0].who);
		await expect(preview).toContainText("designation");
		await expect(preview).toContainText(PROPOSALS[0].what);
		// crema_proposal_record maps the payload through mapping_json's field_map: a
		// payload key the map does not name is not part of the write, so it may not
		// appear here either.
		await expect(preview).not.toContainText("unmapped_key");
		await expect(preview).not.toContainText(UNMAPPED);

		await expect(action_button(page, "Approve")).toBeVisible();
		await expect(action_button(page, "Discard")).toBeVisible();
		// Undo is for Approved rows only.
		await expect(action_button(page, "Undo")).toHaveCount(0);
	});

	test("approves a pending row from the form, and Undo puts the record back", async ({
		page,
		admin,
	}) => {
		await goto_desk(page, `/desk/crema-proposal/${names[0]}`);
		await action_button(page, "Approve").click();

		// A real write, made as the task's Runs As account, not a status flip.
		await expect.poll(() => page.evaluate(() => cur_frm.doc.status)).toBe("Approved");
		expect(await contacts_named(admin, PROPOSALS[0].who)).toHaveLength(1);

		// reload_doc re-runs refresh, so the buttons follow the new status.
		await expect(action_button(page, "Approve")).toHaveCount(0);
		await expect(action_button(page, "Discard")).toHaveCount(0);

		await action_button(page, "Undo").click();
		await expect.poll(() => page.evaluate(() => cur_frm.doc.status)).toBe("Pending");
		expect(await contacts_named(admin, PROPOSALS[0].who)).toHaveLength(0);
		await expect(action_button(page, "Undo")).toHaveCount(0);
		await expect(action_button(page, "Approve")).toBeVisible();
	});

	test("discards a pending row from the form, and writes nothing", async ({ page, admin }) => {
		await goto_desk(page, `/desk/crema-proposal/${names[1]}`);
		await action_button(page, "Discard").click();

		await expect.poll(() => page.evaluate(() => cur_frm.doc.status)).toBe("Discarded");
		expect(await contacts_named(admin, PROPOSALS[1].who)).toHaveLength(0);
		await expect(action_button(page, "Approve")).toHaveCount(0);
		await expect(action_button(page, "Discard")).toHaveCount(0);
		// Nothing was written, so there is nothing to reverse.
		await expect(action_button(page, "Undo")).toHaveCount(0);
	});

	// crema_run_proposal_action must escape a server-reported error before handing it to
	// msgprint, or markup in that text runs instead of reading as text.
	test("shows a failed approval's error as text, not markup", async ({ page }) => {
		await goto_desk(page, `/desk/crema-proposal/${names[0]}`);
		const XSS = "<img src=x onerror=window.__xss=1>";
		await page.route("**/api/method/crema.api.approve_proposals", (route) =>
			route.fulfill({ json: { message: [{ name: names[0], error: XSS }] } })
		);
		await page.evaluate(() => {
			window.__xss = undefined;
		});

		const approve_failed = page.waitForResponse((res) =>
			res.url().includes("/api/method/crema.api.approve_proposals")
		);
		await action_button(page, "Approve").click();
		await approve_failed;

		await expect(crema_modal(page).locator(".modal-title")).toContainText("Failed");
		await expect(crema_modal(page).locator(".modal-body")).toContainText(XSS);
		expect(await page.evaluate(() => window.__xss)).toBeUndefined();
	});

	test("previews every parked write before a bulk approve, and writes only on confirm", async ({
		page,
		admin,
	}) => {
		// Filtered to this file's own task: the assertions below count rows.
		await goto_desk(page, `/desk/crema-proposal?task=${TASK}`);
		// .list-row-container also wraps the header row. .list-row matches data rows only.
		const rows = page.locator(".list-row-container .list-row");
		await expect(rows).toHaveCount(2);
		const boxes = page.locator(".list-row-container .list-row .list-row-checkbox");
		await expect(boxes).toHaveCount(2);
		for (const box of await boxes.all()) await box.check();

		// The Actions menu only appears once something is checked;
		// add_actions_menu_item (crema_proposal_list.js) is what puts the three crema
		// entries in it.
		await page.locator("button", { hasText: "Actions" }).click();
		await page.locator('.es-menu [role="menuitem"]', { hasText: "Approve" }).click();

		// Approve is the only one of the three that writes, so it is the only one that
		// asks first, and it shows each parked write, not just a count of rows.
		const modal = crema_modal(page);
		await expect(modal).toContainText("Approve 2 proposals?");
		await expect(modal).toContainText("Records to write");
		await expect(modal).toContainText("1. Contact");
		await expect(modal).toContainText("2. Contact");
		await expect(modal).toContainText("first_name");
		await expect(modal).toContainText(PROPOSALS[0].who);
		await expect(modal).toContainText(PROPOSALS[1].who);

		// Asking first means nothing is written while the dialog is still up.
		const before = await proposal_rows(admin, ["status"]);
		expect(before.map((row) => row.status)).toEqual(["Pending", "Pending"]);

		// Wait for shown.bs.modal (frappe flips cur_dialog.display there) before
		// clicking: bootstrap ignores modal("hide") while the show transition is
		// still running, so a click inside the fade-in runs onConfirm but leaves
		// the dialog up, and this test then reads a stale list underneath it.
		await dialog_shown(page);
		const confirm = modal.locator(".btn-modal-primary");
		await expect(confirm).toContainText("Approve 2");
		await confirm.click();

		await expect(rows).toContainText(["Approved", "Approved"]);
		const after = await proposal_rows(admin, ["status"]);
		expect(after.map((row) => row.status)).toEqual(["Approved", "Approved"]);
		expect(await contacts_named(admin, PROPOSALS[0].who)).toHaveLength(1);
		expect(await contacts_named(admin, PROPOSALS[1].who)).toHaveLength(1);
	});
});
