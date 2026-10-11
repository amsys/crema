// Covers the robot button on a saved form (crema.bundle.js) and the list prompt that
// builds a create spec with child rows. All model calls are stubbed with page.route; the
// backstop fixture fails any billable call that no stub answered.
//
// Drives Contact: it gives a plain Data field to write (first_name) and a child table
// (phone_nos).
import { test, expect, goto_desk, clear_filters } from "../support";

const ASK_API = /\/api\/method\/crema\.api\.ask_api(\?|$)/;
const TRANSFORM_API = /\/api\/method\/crema\.api\.transform_api(\?|$)/;

// Every dialog crema opens is a plain frappe.ui.Dialog; dialog.hide() only calls
// .modal("hide") and leaves the element in the DOM (frappe never removes it), so once a
// test has opened more than one dialog ".modal" matches all of them. Bootstrap
// adds/removes ".show" on the currently-visible one — this is the only reliable target.
function crema_modal(page) {
	return page.locator(".modal.show");
}

function crema_stub_ask(page, spec) {
	return page.route(ASK_API, (route) =>
		route.fulfill({ status: 200, json: { message: { result: JSON.stringify(spec) } } })
	);
}

async function crema_prompt(page, text) {
	// frappe keeps a hidden page per visited view, each with its own button.
	const asked = page.waitForResponse((res) => ASK_API.test(res.url()));
	await page.locator("[data-crema]:visible").click();
	const modal = crema_modal(page);
	await modal.locator(".frappe-control[data-fieldname=instruction] textarea").fill(text);
	await modal.locator(".btn-modal-primary").click();
	await asked;
}

test.describe("Crema desk UI — form view", () => {
	let saved_contact = null;

	test.afterEach(async ({ admin }) => {
		if (saved_contact) await admin.remove_doc("Contact", saved_contact, true);
		saved_contact = null;
	});

	test("renders a robot button on a saved form and applies a diff without saving", async ({
		page,
	}) => {
		await goto_desk(page, "/desk/contact/new");
		const first_name = page.locator(".frappe-control[data-fieldname=first_name] input");
		await first_name.fill("a contact to transform");
		// frappe copies the input into the doc on change; fill() fires only input, so leave the field.
		await first_name.blur();
		await expect
			.poll(() => page.evaluate(() => cur_frm.doc.first_name))
			.toBe("a contact to transform");
		const saved = page.waitForResponse((res) =>
			res.url().includes("frappe.desk.form.save.savedocs")
		);
		await page.locator(".primary-action:visible", { hasText: /Save/ }).click();
		await saved;
		saved_contact = await page.evaluate(() => cur_frm.doc.name);

		await expect(page.locator("[data-crema-form]")).toBeVisible();

		await page.route(TRANSFORM_API, (route) =>
			route.fulfill({
				status: 200,
				json: {
					message: {
						set: { first_name: "Updated by Crema" },
						child_set: {},
						reason: "updated",
					},
				},
			})
		);

		const transformed = page.waitForResponse((res) => TRANSFORM_API.test(res.url()));
		await page.locator("[data-crema-form]").click();
		const modal = crema_modal(page);
		await modal
			.locator(".frappe-control[data-fieldname=instruction] textarea")
			.fill("mark this urgent");
		await modal.locator(".btn-modal-primary").click();
		await transformed;
		await crema_modal(page).locator(".btn-modal-primary", { hasText: /Apply/ }).click();

		await expect(page.locator(".page-indicator-pill")).toContainText("Not Saved");
	});
});

test.describe("Crema desk UI — create spec child rows", () => {
	test.beforeEach(async ({ page }) => {
		await goto_desk(page, "/desk/contact");
		await clear_filters(page);
	});

	test("keeps child rows from a create spec", async ({ page }) => {
		// The perm.js child-doctype regression: frappe.perm.get_perm(child_doctype) returns
		// read:0 for every field (a child doctype carries no DocPerm rows of its own), so a
		// naive write-fence would silently drop every child_set row. crema_writable_fields
		// takes the PARENT's perm array instead — this is the test that fails if that ever
		// changes back.
		await crema_stub_ask(page, {
			action: "create",
			records: [
				{
					set: { first_name: "Crema Child Row Test" },
					child_set: { phone_nos: [{ phone: "555-0100" }] },
				},
			],
			reason: "creating a contact",
		});
		await crema_prompt(page, "create a contact named Crema Child Row Test");

		await expect.poll(() => new URL(page.url()).pathname).toContain("/contact/new-contact-");
		await expect.poll(() => page.evaluate(() => cur_frm?.doc?.phone_nos?.length)).toBe(1);
		await expect
			.poll(() => page.evaluate(() => cur_frm?.doc?.phone_nos?.[0]?.phone))
			.toBe("555-0100");
	});

	test("lists the child table's own writable fieldnames in the prompt, not [read-only]", async ({
		page,
	}) => {
		// Guards the crema_writable_table branch in crema_view_prompt: a Table field is
		// never in crema_writable_fields' set (frappe.model.is_value_type excludes it), so
		// without this branch the prompt would print "[read-only]" for phone_nos while the
		// same prompt's create/edit shapes advertise child_set — a self-contradiction the
		// model has no way to resolve.
		//
		// frappe.call posts form-encoded, not JSON. The handler only records the prompt; the
		// test body asserts on it.
		const prompts = [];
		await page.route(ASK_API, (route) => {
			prompts.push(new URLSearchParams(route.request().postData()).get("prompt"));
			return route.fulfill({
				status: 200,
				json: { message: { result: JSON.stringify({ action: "none", reason: "n/a" }) } },
			});
		});

		await crema_prompt(page, "anything");

		expect(prompts).toHaveLength(1);
		expect(prompts[0]).toMatch(/phone_nos \(Table\)[^\n]*\[rows: [^\]]*phone[^\]]*\]/);
	});
});
