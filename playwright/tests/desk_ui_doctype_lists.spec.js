// Covers the Crema Provider list (crema_provider_list.js), the Crema Health Word list and
// the File Query source on the Crema Automation Task form (crema_automation_task.js).
// Nothing here calls a model provider; the backstop fixture fails any billable call.
import { test, expect, goto_desk } from "../support";

test.describe("Crema Provider list", () => {
	test.beforeEach(async ({ page }) => {
		await goto_desk(page, "/desk/crema-provider");
	});

	test("keeps the New from Template action", async ({ page }) => {
		await expect(
			page.locator("button").filter({ hasText: /New from Template/ })
		).toBeVisible();
	});

	// Connection is painted by a listview_settings formatter after one
	// crema_prefetch-then-refresh pass (crema_provider_list.js) — Address is an ordinary
	// list column, so its header must be there whether or not a provider exists.
	test("shows the Address column", async ({ page }) => {
		await expect(
			page.locator(".list-row-head").filter({ hasText: /Address/ })
		).not.toHaveCount(0);
	});

	test("fills the base URL from a preset in the New from Template dialog", async ({ page }) => {
		await page
			.locator("button")
			.filter({ hasText: /New from Template/ })
			.click();
		await expect(
			page.locator(".modal-title").filter({ hasText: /New Provider from Template/ })
		).toBeVisible();
		// preset is a Select (get_presets() supplies the option list, label -> base_url),
		// its change handler fills provider_name and base_url.
		await page
			.locator(".modal .frappe-control[data-fieldname=preset] select")
			.selectOption("OpenAI");
		await expect(
			page.locator(".modal .frappe-control[data-fieldname=provider_name] input")
		).toHaveValue("OpenAI");
		await expect(
			page.locator(".modal .frappe-control[data-fieldname=base_url] input")
		).toHaveValue("https://api.openai.com/v1");
	});
});

test.describe("Crema Health Word list", () => {
	test.beforeEach(async ({ page }) => {
		await goto_desk(page, "/desk/crema-health-word");
	});

	test("offers the Translate Built-in List action", async ({ page }) => {
		await expect(
			page.locator("button").filter({ hasText: /Translate Built-in List/ })
		).toBeVisible();
	});

	test("opens the dialog with a Language field", async ({ page }) => {
		await page
			.locator("button")
			.filter({ hasText: /Translate Built-in List/ })
			.click();
		await expect(
			page.locator(".modal-title").filter({ hasText: /Translate Built-in List/ })
		).toBeVisible();
		await expect(
			page.locator(".modal .frappe-control[data-fieldname=language]")
		).toBeVisible();
	});
});

// Covers the File Query source type and the Match Records On picker
// (crema_automation_task.js): a File Query row pins its own doctype and reuses the
// Which Records filter dialog, and Match Records On is a plain Data field with a
// clickable "Pick fields…" helper once a target doctype is chosen.
test.describe("Crema Automation Task form — File Query source", () => {
	test.beforeEach(async ({ page }) => {
		await goto_desk(page, "/desk/crema-automation-task/new");
	});

	test("offers File Query as a source type and pins it to the File doctype", async ({
		page,
	}) => {
		// crema_render_row_filters only has a field wrapper to paint into once the row
		// has actually been expanded (form_render) — the collapsed grid's own inline
		// select still fires source_type's field trigger, but there is nowhere for
		// "Which Records" to render until the row is open.
		await page.locator('[data-fieldname="sources"] .grid-add-row').click();
		await page
			.locator('[data-fieldname="sources"] .grid-body .rows > .grid-row')
			.last()
			.locator(".btn-open-row")
			.click();
		const open_row = page.locator(".grid-row-open");
		// The "Which Records" label is in the open row until the filter table replaces the
		// field's wrapper, so check it before the select fires that render.
		await expect(open_row).toContainText("Which Records");
		// The row's own collapsed-view select for this field stays in the DOM (just
		// hidden) alongside the expanded form's — scope to the one actually visible.
		await open_row
			.locator("select[data-fieldname='source_type']:visible")
			.selectOption("File Query");

		// source_doctype is hidden (depends_on Document Query only) but stamped to "File"
		// underneath — the filter table is what proves the Which Records field rendered.
		await expect(open_row).toBeVisible();
		await expect(
			open_row.locator("table").filter({ hasText: /Click to set filters/ })
		).toBeVisible();
		await expect
			.poll(() => page.evaluate(() => cur_frm.doc.sources.at(-1).source_doctype))
			.toBe("File");
	});

	test("clears Files (attachments) for a File Query row", async ({ page }) => {
		await page.locator('[data-fieldname="sources"] .grid-add-row').click();
		await page
			.locator("select[data-fieldname='source_type']")
			.last()
			.selectOption("File Query");

		await expect(
			page
				.locator(".grid-row [data-fieldname='read_attachments'] input[type='checkbox']")
				.last()
		).not.toBeChecked();
	});

	test("shows a Pick fields… helper on Match Records On once a target doctype is set", async ({
		page,
	}) => {
		// match_on's own depends_on requires a File Query source row to exist, not merely
		// an action + target_doctype.
		await page.locator('[data-fieldname="sources"] .grid-add-row').click();
		await page
			.locator("select[data-fieldname='source_type']")
			.last()
			.selectOption("File Query");

		await page
			.locator("select[data-fieldname='action']")
			.selectOption("Create or Update Records");
		// set_value fires the exact same target_doctype field trigger a real pick would;
		// this test's own subject is the "Pick fields…" picker, not the Link control's
		// autocomplete UX.
		await page.evaluate(() => cur_frm.set_value("target_doctype", "Contact"));
		// frappe.model.with_doctype fetches Contact's meta before the link renders. The
		// page's own with_doctype callback was queued first, so this one runs after it.
		await page.evaluate(
			() => new Promise((resolve) => frappe.model.with_doctype("Contact", resolve))
		);
		const pick = page.locator(
			".frappe-control[data-fieldname='match_on'] a.crema-match-on-pick"
		);
		await expect(pick).toBeVisible();

		// target_doctype's own handler auto-fills match_on from Contact's unique/autoname
		// fields when it's still empty (crema_default_match_fields) — the picker dialog
		// pre-seeds its pills from whatever match_on already holds, so a leftover default
		// here would land as an extra pill alongside "first_name" below. Start from empty
		// so the picker's own "Set" behavior is what's under test, not the auto-fill.
		await page.evaluate(() => cur_frm.set_value("match_on", ""));

		await pick.click();

		await expect(
			page.locator(".modal-title").filter({ hasText: /Match Records On/ })
		).toBeVisible();
		// Setting the full value in one go fires "input" once (what Awesomplete's own
		// listener reacts to) and leaves no partial state for the debounced change handler
		// to commit as a stray pill, which character-by-character typing would.
		// Awesomplete closes its list on blur, and the dialog moves focus to its first input
		// when it finishes opening — fill only after that focus has landed.
		const match_input = page.locator(".modal .awesomplete input");
		await expect(match_input).toBeFocused();
		await match_input.fill("first_name");
		await page
			.locator("li")
			.filter({ hasText: /first_name/ })
			.first()
			.click();
		// Awesomplete leaves its own suggestion <ul> rendered (just no longer relevant)
		// after a click selection — it visually overlaps the footer below it even though
		// the field's own value already updated, so a click at the button's coordinates lands
		// on that list instead. Dispatch the click on the button itself.
		await page
			.locator(".modal-footer button")
			.filter({ hasText: /Set/ })
			.dispatchEvent("click");

		await expect(page.locator("[data-fieldname='match_on'] input")).toHaveValue("first_name");
	});
});
