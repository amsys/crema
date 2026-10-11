// Covers crema.bundle.js: the robot button (list-family toolbar) and the view specs the
// model answers with. All model calls are stubbed with page.route, the same rule the
// Python suite follows (patch at the boundary, never call a live network).
//
// Drives Contact: it gives a plain Data field to write (first_name), a Select (status),
// and a blank field for the widen-if-empty probe (designation).
import { test, expect, goto_desk, clear_filters, count_requests } from "../support";

const ASK_URL = /\/api\/method\/crema\.api\.ask_api(\?|$)/;
// The widen probe sends or_filters made of "like" conditions. Report view also sends
// or_filters, with "=" conditions, to load its saved List Filter rows: that is not a probe.
const WIDEN_PROBE = /frappe\.desk\.reportview\.get_list\?.*or_filters=\[\["[a-z_]+","like",/;

// Every dialog crema opens is a plain frappe.ui.Dialog; dialog.hide() only calls
// .modal("hide") and leaves the element in the DOM (frappe never removes it), so once a
// test has opened more than one dialog ".modal" matches all of them. Bootstrap
// adds/removes ".show" on the currently-visible one: this is the only reliable target.
function crema_modal(page) {
	return page.locator(".modal.show");
}

const CREATED_CONTACTS = [];

// Setup deletes any leftover row with the same first name, then inserts a fresh one.
async function crema_insert_contact(api, fields) {
	const stale = await api.get_list(
		"Contact",
		["name"],
		[["first_name", "=", fields.first_name]]
	);
	for (const row of stale) await api.remove_doc("Contact", row.name, true);
	const doc = await api.insert_doc("Contact", fields);
	CREATED_CONTACTS.push(doc.name);
	return doc;
}

// Registers the stub and returns the live list of calls it answered.
async function crema_stub_ask(page, spec) {
	const calls = [];
	await page.route(ASK_URL, (route) => {
		if (route.request().method() !== "POST") return route.fallback();
		calls.push(route.request().url());
		return route.fulfill({
			status: 200,
			json: { message: { result: JSON.stringify(spec) } },
		});
	});
	return calls;
}

async function crema_prompt(page, text) {
	// the request wait starts before the click that fires it
	const asked = page.waitForRequest((r) => ASK_URL.test(r.url()) && r.method() === "POST");
	asked.catch(() => {});
	// frappe keeps a hidden page per visited view, each with its own button.
	await page.locator("[data-crema]:visible").click();
	const modal = crema_modal(page);
	await modal.locator(".frappe-control[data-fieldname=instruction] textarea").fill(text);
	await modal.locator(".btn-modal-primary").click();
	await asked;
}

const search = (page) => () => new URL(page.url()).search;
const data_rows = (page) => page.locator(".list-row-container .list-row");
const widen_probes = (page) =>
	count_requests(
		page,
		(r) => r.method() === "GET" && WIDEN_PROBE.test(decodeURIComponent(r.url()))
	);

test.describe("Crema desk UI", () => {
	test.beforeEach(async ({ page }) => {
		await goto_desk(page, "/desk/contact");
		// Saved filters from an earlier test come back with the page, so clear them first.
		await clear_filters(page);
	});

	test.afterAll(async ({ admin }) => {
		// ignore_missing: a test may already have deleted its own rows.
		for (const name of CREATED_CONTACTS) await admin.remove_doc("Contact", name, true);
	});

	test("renders the robot button on a list view", async ({ page }) => {
		// The regression this whole test file exists for: the button used to attach on
		// frappe.router's "change" event, which fires before ListView.setup_page has set
		// frappe.container.page.list_view, so it silently never appeared.
		await expect(page.locator("[data-crema]")).toBeVisible();
	});

	test("places the button between the menu and the primary action", async ({ page }) => {
		const items = page
			.locator(".standard-actions")
			.locator(".menu-btn-group, [data-crema], .primary-action");
		await expect(items).toHaveCount(3);
		const [menu_idx, crema_idx, primary_idx] = await items.evaluateAll((els) => [
			els.findIndex((el) => el.className.includes("menu-btn-group")),
			els.findIndex((el) => "crema" in el.dataset),
			els.findIndex((el) => el.className.includes("primary-action")),
		]);
		expect(menu_idx).toBeGreaterThanOrEqual(0);
		expect(menu_idx).toBeLessThan(crema_idx);
		expect(crema_idx).toBeLessThan(primary_idx);
	});

	test("is the same square size as the native reload button", async ({ page }) => {
		// Regression pin: on v17 this slot renders as an .es-button, and
		// .page-actions .secondary-action carries an unconditional min-width: 40px that
		// outranks the button's own square width, widening it past the reload icon
		// next to it unless min-width is overridden inline.
		const crema_width = (await page.locator("[data-crema]").boundingBox()).width;
		const reload_width = (await page.locator(".page-icon-group .icon-btn").boundingBox())
			.width;
		expect(reload_width).toBe(crema_width);
	});

	test("opens the dialog on click, with an inline file drop zone and no batch checkbox", async ({
		page,
	}) => {
		await page.locator("[data-crema]").click();
		const modal = crema_modal(page);
		await expect(modal).toContainText("Ask Crema about Contact");
		// The upload field hosts frappe.ui.FileUploader inline (wrapper: ...): no
		// second, stacked modal, and no plain Attach control.
		await expect(modal.locator(".file-uploader")).toBeVisible();
		await expect(modal.locator(".frappe-control[data-fieldname=file]")).toHaveCount(0);
		// "Many records in one file" was dropped: extract() decides record count itself.
		await expect(modal.locator(".frappe-control[data-fieldname=batch]")).toHaveCount(0);
		// The drop zone is shrunk from frappe's stock 16rem so it doesn't dominate the dialog.
		const min_height = await modal
			.locator(".file-upload-area")
			.evaluate((el) => Number.parseFloat(getComputedStyle(el).minHeight));
		expect(min_height).toBeLessThan(200);
		await modal.locator(".btn-modal-close").click();
	});

	test("applies a view spec and drops fields the model invented", async ({ page }) => {
		// "not_a_field" isn't a real Contact field: crema_apply_view_spec must drop it,
		// the one piece of real security logic in this file, and warn about it, rather
		// than silently narrowing to a set the model never asked for.
		await crema_stub_ask(page, {
			view: "List",
			filters: { status: ["=", "Open"], not_a_field: ["=", "x"] },
			reason: "showing open contacts",
		});
		await crema_prompt(page, "show open contacts");

		// crema_valid_filters' warning (frappe.show_alert, default 7s) auto-dismisses,
		// so check it before the location assertions below, not after. The message is
		// generic (doesn't name the dropped field).
		await expect(
			page.locator(".es-toast").filter({ hasText: "Crema named a condition" })
		).toContainText("Crema named a condition this list cannot use");
		// list_view.js's get_search_params only JSON.stringifies a non-"=" operator; "="
		// is written as the bare value.
		await expect.poll(search(page)).toContain("status=Open");
		await expect.poll(search(page)).not.toContain("not_a_field");
	});

	test("applies order_by to the sort selector and refreshes the list", async ({ page }) => {
		await crema_stub_ask(page, {
			view: "List",
			order_by: "name desc",
			reason: "sorted by name, descending",
		});
		await crema_prompt(page, "sort by name descending");

		// frappe.meta.get_label(doctype, "name") is "ID", not "Name": assert the sort
		// selector's actual state instead of a label that was never right.
		await expect.poll(() => page.evaluate(() => cur_list.sort_selector.sort_by)).toBe("name");
		await expect(page.locator(".sort-selector .btn-order")).toHaveAttribute(
			"data-value",
			"desc"
		);
	});

	test("makes one list query for a spec with a sort", async ({ page }) => {
		// Pin for the round-trip fix: applying a sort/page_length/columns spec used to
		// refresh the list twice (once via the route change, once via on_sort_change).
		// Already being on the target List should collapse that to one.
		//
		// Sorts by "modified", not "name" (the previous test's own target): frappe
		// persists the list's last sort per user/doctype (frappe.model.utils.user_settings)
		// and reapplies it on the next page load, and base_list.js throttles a refresh
		// whose args exactly repeat the last one for 3 seconds (no_change()), so
		// requesting the very sort the last test just persisted can silently no-op.
		const list_gets = count_requests(
			page,
			(r) =>
				r.method() === "POST" &&
				new URL(r.url()).pathname === "/api/method/frappe.desk.reportview.get"
		);
		// A saved sort from an earlier test or run can already be "modified": pick the
		// other one, so the request is never a repeat that frappe skips.
		const target =
			(await page.evaluate(() => cur_list.sort_selector.sort_by)) === "modified"
				? "creation"
				: "modified";
		await crema_stub_ask(page, {
			view: "List",
			order_by: `${target} desc`,
			reason: "sorted by last modified",
		});
		await crema_prompt(page, "sort by last modified");

		await expect.poll(() => list_gets.length).toBe(1);
		// the sort is applied and shown, then the count must still be one
		await expect.poll(() => page.evaluate(() => cur_list.sort_selector.sort_by)).toBe(target);
		expect(list_gets).toHaveLength(1);
	});

	test("drops an order_by field the model invented and leaves the sort untouched", async ({
		page,
	}) => {
		await crema_stub_ask(page, {
			view: "List",
			order_by: "not_a_field desc",
			reason: "sorted",
		});

		const order = page.locator(".sort-selector .btn-order");
		const before = await order.getAttribute("data-value");
		await crema_prompt(page, "sort somehow");
		await expect(order).toHaveAttribute("data-value", before);
		// the invented field never reaches the sort selector
		expect(await page.evaluate(() => cur_list.sort_selector.sort_by)).not.toBe("not_a_field");
	});

	test("applies page_length as a row limit", async ({ page }) => {
		await crema_stub_ask(page, { view: "List", page_length: 1, reason: "top 1" });
		await crema_prompt(page, "show me the top 1");

		// .list-row-container also wraps the header row (list_view.js wraps both the
		// <header class="list-row-head"> and each data row's .list-row in their own
		// .list-row-container); .list-row alone (the header's own inner element is
		// .list-row-head, a different class) matches data rows only.
		await expect.poll(() => data_rows(page).count()).toBeLessThanOrEqual(1);
	});

	test("round-trips a like filter with a wildcard intact", async ({ page }) => {
		await crema_stub_ask(page, {
			view: "List",
			filters: { first_name: ["like", "t%"] },
			reason: "contacts starting with t",
		});
		await crema_prompt(page, "contacts starting with t");

		await expect.poll(search(page)).toContain(encodeURIComponent('["like","t%"]'));
	});

	test("clears the previous prompt's filters when the next spec has none", async ({ page }) => {
		// The fast path used to only touch filter_area when the spec had a filter to set
		// (mirroring list_view.js's own before_refresh() guard), so "show me everything"
		// silently answered the *previous* question against a still-filtered list.
		await crema_stub_ask(page, {
			view: "List",
			filters: { first_name: ["like", "t%"] },
			reason: "contacts starting with t",
		});
		await crema_prompt(page, "contacts starting with t");
		await expect.poll(search(page)).toContain("first_name");

		await crema_stub_ask(page, { view: "List", reason: "everything" });
		await crema_prompt(page, "show me everything");
		await expect.poll(search(page)).not.toContain("first_name");
	});

	test("drops a group_by with an invented aggregate fieldname", async ({ page }) => {
		await crema_stub_ask(page, {
			view: "List",
			group_by: ["status", "not_a_field", "sum"],
			reason: "grouped by status",
		});
		await crema_prompt(page, "group by status");

		// the list is still the Contact list, so the absence below is not an empty page
		await expect.poll(() => page.evaluate(() => cur_list.doctype)).toBe("Contact");
		await expect.poll(search(page)).not.toContain("_group_by");
	});

	test("widens to the field that holds the term when the filter matches nothing", async ({
		page,
		api,
	}) => {
		// designation is blank on a plain Contact, so the model's own filter matches
		// nothing; the fallback should find "probe" in first_name instead, with no
		// second LLM call.
		await crema_insert_contact(api, { first_name: "crema fallback probe" });
		// frappe.db.get_list sends type: "GET" (frappe/public/js/frappe/db.js), not POST.
		// or_filters is the widen probe's signature (crema_widen_if_empty is the only
		// desk code that sends it): a bare get_list* match also counts unrelated
		// desk traffic on a slow runner and breaks the exact probe-count assertions.
		const probes = widen_probes(page);
		const asks = await crema_stub_ask(page, {
			view: "List",
			filters: { designation: ["like", "%probe%"] },
			reason: "contacts referencing probe",
		});
		await crema_prompt(page, "contacts referencing probe");

		await expect.poll(() => probes.length, { timeout: 20000 }).toBe(1);
		expect(asks).toHaveLength(1);
		await expect.poll(search(page)).toContain(encodeURIComponent('["like","%probe%"]'));
		await expect.poll(() => data_rows(page).count()).toBeGreaterThanOrEqual(1);
		// the visible result is in; no second probe came after it
		expect(probes).toHaveLength(1);
	});

	test("widens to an exact match when the probe finds one, in one query", async ({
		page,
		api,
	}) => {
		// The term is the whole first_name, not a substring of it: the widen probe's
		// own results already prove an exact hit, at no extra query.
		await crema_insert_contact(api, { first_name: "crema exact probe" });
		// or_filters is the widen probe's signature (crema_widen_if_empty is the only
		// desk code that sends it), see the test above.
		const probes = widen_probes(page);
		await crema_stub_ask(page, {
			view: "List",
			filters: { designation: ["like", "%crema exact probe%"] },
			reason: "contacts referencing crema exact probe",
		});
		await crema_prompt(page, "contacts referencing crema exact probe");

		await expect.poll(() => probes.length, { timeout: 20000 }).toBe(1);
		// list_view.js's get_search_params only JSON.stringifies a non-"=" operator;
		// "=" is written as the bare "field=value" pair (see the view-spec test above).
		await expect.poll(search(page)).toContain("first_name=crema+exact+probe");
		await expect(data_rows(page)).toHaveCount(1);
		expect(probes).toHaveLength(1);
	});

	test("widens to a word from the term when the whole term matches nowhere", async ({
		page,
		api,
	}) => {
		// The exact probe-count assertion below needs a clean filter area: beforeEach has
		// cleared it. A widened filter left by the test above makes crema_refresh_live's
		// clear+set arm a spurious debounced refresh, and the widen can then run twice
		// concurrently, which adds one whole-term probe.
		//
		// "Acme Corp One" (the search term) is nowhere in "Acme Corporation One" (the
		// stored value) as one substring: the whole-term probe below must come back
		// empty before a second, per-word probe finds "Acme".
		await crema_insert_contact(api, { first_name: "Acme Corporation One" });
		// or_filters is the widen probe's signature, see the first widen test above.
		const probes = widen_probes(page);
		await crema_stub_ask(page, {
			view: "List",
			filters: { designation: ["like", "%Acme Corp One%"] },
			reason: "contacts about Acme Corp One",
		});
		await crema_prompt(page, "contacts about Acme Corp One");

		// 20s: the probe fires only after the list renders the applied filter, so a slow
		// CI runner needs the room.
		await expect.poll(() => probes.length, { timeout: 20000 }).toBe(2);
		await expect.poll(search(page)).toContain(encodeURIComponent('["like","%Acme%"]'));
		await expect.poll(() => data_rows(page).count()).toBeGreaterThanOrEqual(1);
		expect(probes).toHaveLength(2);
	});

	test("widens after a slow list query, not after a fixed delay", async ({ page, api }) => {
		// Regression for the CI flake. The check used to run once, 300 ms after the route
		// resolved. A list query slower than that left the rows from before the filter in
		// cur_list.data, the check returned, and no probe was sent. Report view makes
		// crema take the router path, which does not wait for the list query itself.
		await crema_insert_contact(api, { first_name: "crema slow probe" });
		await expect.poll(() => data_rows(page).count()).toBeGreaterThan(0);
		await page.evaluate(() => frappe.set_route("List", "Contact", "Report"));
		await expect.poll(() => page.evaluate(() => cur_list.view_name)).toBe("Report");
		const slow_list = async (route) => {
			const response = await route.fetch();
			await page.waitForTimeout(1000);
			await route.fulfill({ response });
		};
		await page.route(/frappe\.desk\.reportview\.get\b/, slow_list);
		try {
			const probes = widen_probes(page);
			await crema_stub_ask(page, {
				view: "List",
				filters: { designation: ["like", "%slow probe%"] },
				reason: "contacts referencing slow probe",
			});
			await crema_prompt(page, "contacts referencing slow probe");

			await expect.poll(() => probes.length, { timeout: 20000 }).toBe(1);
			await expect.poll(search(page)).toContain("first_name");
			expect(probes).toHaveLength(1);
		} finally {
			// a delayed answer still in flight at teardown must not fail the test
			await page.unrouteAll({ behavior: "ignoreErrors" });
		}
	});

	test("leaves an empty list alone when nothing matches anywhere", async ({ page }) => {
		const asks = await crema_stub_ask(page, {
			view: "List",
			filters: { first_name: ["like", "%zzz-no-such-text%"] },
			reason: "contacts about zzz-no-such-text",
		});
		await crema_prompt(page, "contacts about zzz-no-such-text");

		await expect(page.locator(".no-result")).toBeVisible();
		expect(asks).toHaveLength(1);
	});
});
