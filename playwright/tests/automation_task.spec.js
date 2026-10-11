// Covers crema_automation_task.js: the status panel, the Dry Run preview, and the
// source-filter builder. Every server call is stubbed with page.route — same rule the
// Python suite follows (patch at the boundary, never call a live model). The backstop
// fixture answers any billable call that no stub caught.
//
// Run with: bench --site <site> run-ui-tests crema --headless --spec automation_task
import { test, expect, goto_desk } from "../support";

const TASK = "_playwright_crema_task";
// A second task rather than a second source row on TASK: "Update the Records It Read"
// is refused unless the task has exactly one Document Query source.
const FILTERED_TASK = "_playwright_crema_filtered_task";
// A third task carrying a malicious record name in last_written_json, for the Undo
// Last Run confirm dialog test below — crema_write_list_html must escape it.
const XSS_TASK = "_playwright_crema_xss_task";
const XSS_NAME = "<img src=x onerror=window.__xss=1>";
// A File Query task, for the "Plan and Extract are not used" strip test.
const FILE_QUERY_TASK = "_playwright_crema_file_query_task";
// A task seeded already Failed at Plan, for the strip's mark test — set directly, the
// same way XSS_TASK seeds last_written_json: both are read-only fields, but nothing
// stops a direct insert from setting them, and a real run is not worth driving here.
const FAILED_TASK = "_playwright_crema_failed_task";
// A task seeded already Failed at Setup — last_failed_stage is "Setup" itself here,
// not blank, since run_task tags that failure before _run_inside is ever reached.
const SETUP_FAILED_TASK = "_playwright_crema_setup_failed_task";
// A Propose Only task with two Pending rows parked against it, for the Write card's
// pending-proposals link.
const PROPOSE_TASK = "_playwright_crema_propose_task";

const ALL_TASKS = [
	TASK,
	FILTERED_TASK,
	XSS_TASK,
	FILE_QUERY_TASK,
	FAILED_TASK,
	SETUP_FAILED_TASK,
	PROPOSE_TASK,
];

const method_url = (method) => (url) => url.pathname === `/api/method/crema.api.${method}`;

const document_query_source = (source_filters = "[]") => ({
	source_type: "Document Query",
	source_doctype: "Contact",
	source_filters,
	source_limit: 50,
});

const now_string = () => new Date().toISOString().slice(0, 19).replace("T", " ");

// A task row shares most of its fields; each seed names only what differs.
const seed_task = (task_name, fields) => ({
	task_name,
	enabled: 0,
	trigger: "Schedule",
	schedule_preset: "Daily 03:00",
	sources: [document_query_source()],
	interface: "complex",
	...fields,
});

// Visits the task form and waits for the stubbed interface list, as the form does on load.
async function open_task(page, name) {
	await Promise.all([
		page.waitForResponse((res) => method_url("get_interfaces")(new URL(res.url()))),
		goto_desk(page, `/desk/crema-automation-task/${name}`),
	]);
}

async function remove_seed_rows(admin) {
	// Proposals link to the task, so they go first.
	const proposals = await admin.get_list(
		"Crema Proposal",
		["name"],
		[["task", "=", PROPOSE_TASK]]
	);
	for (const { name } of proposals) await admin.remove_doc("Crema Proposal", name, true);
	for (const name of ALL_TASKS) await admin.remove_doc("Crema Automation Task", name, true);
}

test.describe("Crema Automation Task form", () => {
	test.beforeAll(async ({ admin }) => {
		await remove_seed_rows(admin);
		await admin.insert_doc(
			"Crema Automation Task",
			seed_task(TASK, {
				instruction: "Set a priority on every open item.",
				action: "Update the Records It Read",
			})
		);
		await admin.insert_doc(
			"Crema Automation Task",
			seed_task(FILTERED_TASK, {
				sources: [
					// The 3-element shape the server itself writes — no leading doctype.
					document_query_source(JSON.stringify([["status", "=", "Open"]])),
				],
				instruction: "Summarise the open items.",
				action: "No Changes",
			})
		);
		await admin.insert_doc(
			"Crema Automation Task",
			seed_task(XSS_TASK, {
				instruction: "Create a record per contact.",
				action: "Create or Update Records",
				target_doctype: "Contact",
				// A past run's write summary — never a real record, this doctype/name pair
				// only has to render in the confirm dialog below.
				last_written_json: JSON.stringify({ created: [["Contact", XSS_NAME]] }),
			})
		);
		await admin.insert_doc(
			"Crema Automation Task",
			seed_task(FILE_QUERY_TASK, {
				sources: [
					{ source_type: "File Query", source_doctype: "File", source_filters: "[]" },
				],
				instruction: "Read each uploaded invoice into a record.",
				action: "Create or Update Records",
				target_doctype: "Contact",
				match_on: "email_id",
			})
		);
		await admin.insert_doc(
			"Crema Automation Task",
			seed_task(FAILED_TASK, {
				instruction: "Set a priority on every open item.",
				action: "Create or Update Records",
				target_doctype: "Contact",
				last_status: "Failed",
				last_failed_stage: "Plan",
				last_run: now_string(),
				last_error: "the model returned an unparsable plan",
			})
		);
		await admin.insert_doc(
			"Crema Automation Task",
			seed_task(SETUP_FAILED_TASK, {
				instruction: "Set a priority on every open item.",
				action: "Create or Update Records",
				target_doctype: "Contact",
				last_status: "Failed",
				last_failed_stage: "Setup",
				last_run: now_string(),
				last_error: "interface unresolvable — the provider is disabled",
			})
		);
		await admin.insert_doc(
			"Crema Automation Task",
			seed_task(PROPOSE_TASK, {
				// Disabled on purpose: the Task filter on the Proposal list must keep a
				// disabled task (crema_proposal_list.js onload, include_disabled).
				instruction: "Park a proposal per open item.",
				action: "Propose Only",
				target_doctype: "Contact",
			})
		);
		for (let i = 0; i < 2; i++) {
			await admin.insert_doc("Crema Proposal", {
				task: PROPOSE_TASK,
				status: "Pending",
				target_doctype: "Contact",
				payload_json: "{}",
				mapping_json: "{}",
			});
		}
	});

	test.afterAll(async ({ admin }) => {
		await remove_seed_rows(admin);
	});

	test.beforeEach(async ({ page }) => {
		await page.route(method_url("get_interfaces"), (route) =>
			route.request().method() === "POST"
				? route.fulfill({
						json: {
							message: [
								{ value: "simple", label: "Default" },
								{ value: "complex", label: "Complex" },
								{ value: "extraction", label: "Extraction" },
							],
						},
				  })
				: route.fallback()
		);
	});

	test("shows a status indicator and the buttons instead of a bare form", async ({ page }) => {
		await open_task(page, TASK);
		// Never run yet, and disabled. The status lives in the page header indicator now
		// rather than in an HTML field, so that is what has to say so.
		await expect(page.locator(".page-head .page-indicator-pill")).toContainText("Never run");
		await expect(page.getByRole("button", { name: "Dry Run", exact: true })).toBeVisible();
		await expect(page.getByRole("button", { name: "Run Now", exact: true })).toBeVisible();
	});

	test("previews extracted rows without writing anything", async ({ page }) => {
		await page.route(method_url("dry_run_automation"), (route) =>
			route.fulfill({
				json: {
					message: {
						action: "Update the Records It Read",
						used_stored_plan: false,
						row_count: 2,
						rows: [
							{ id: "CONT-0001", prio: "High" },
							{ id: "CONT-0002", prio: "Low" },
						],
						plan: {
							extract: { prompt: "Copy each name through." },
							map: {
								doctype: "Contact",
								match_fields: ["name"],
								field_map: { id: "name" },
							},
						},
					},
				},
			})
		);

		await open_task(page, TASK);
		await Promise.all([
			page.waitForResponse((res) => method_url("dry_run_automation")(new URL(res.url()))),
			page.getByRole("button", { name: "Dry Run", exact: true }).click(),
		]);

		const body = page.locator(".modal-body");
		await expect(body).toContainText("would be updated");
		await expect(body).toContainText("Nothing was saved");
		await expect(body).toContainText("CONT-0001");
	});

	test("routes a 417 from the dry run through crema_show_error", async ({ page }) => {
		await page.route(method_url("dry_run_automation"), (route) =>
			route.fulfill({
				status: 417,
				json: { message: { blocked: true, reason: "blocked: prompt injection" } },
			})
		);

		await open_task(page, TASK);
		await Promise.all([
			page.waitForResponse((res) => method_url("dry_run_automation")(new URL(res.url()))),
			page.getByRole("button", { name: "Dry Run", exact: true }).click(),
		]);

		await expect(page.locator(".modal-title")).toContainText("Blocked");
		await expect(page.locator(".modal-body")).toContainText("prompt injection");
	});

	test("shows each source's settings as separate grid columns", async ({ page }) => {
		await open_task(page, TASK);

		// The old single "Details" column became several, so the limit and the two checks
		// can be read — and changed — without opening the row. The headers are kept short
		// on purpose: at one grid column wide, a longer one is truncated to "Attach…".
		const head = page.locator('[data-fieldname="sources"] .grid-heading-row');
		await expect(head).toContainText("What");
		await expect(head).toContainText("Per Run");
		await expect(head).toContainText("Changed");
		await expect(head).toContainText("Files");
	});

	test("reveals 'Stop if a Source Fails' as soon as a second source row exists", async ({
		page,
	}) => {
		// depends_on is not re-evaluated when a grid row is added, so without the
		// sources_add handler this only appears after the next save.
		await open_task(page, TASK);

		const on_source_error = page.locator('.frappe-control[data-fieldname="on_source_error"]');
		await expect(on_source_error).toBeAttached();
		await expect(on_source_error).not.toBeVisible();
		await page.locator('[data-fieldname="sources"] .grid-add-row').click();
		await expect(on_source_error).toBeVisible();
	});

	test("summarises each source in the grid", async ({ page }) => {
		await open_task(page, TASK);
		// What the row reads and how, without opening it. .grid-row also matches the
		// heading row nested inside .grid-heading-row (grid.js) — data rows live in
		// .grid-body .rows. TASK has exactly one source, so the row is unique.
		const row = page.locator('[data-fieldname="sources"] .grid-body .rows > .grid-row');
		await expect(row).toContainText("Document Query");
		// What carries the record type and, when there is one, the filter count.
		await expect(row).toContainText("Contact");
		// The limit is its own cell now, not part of a "· 50/run ·" summary string.
		await expect(row.locator('[data-fieldname="source_limit"]')).toContainText("50");
	});

	test("opens a filter builder from the source row's filters table", async ({ page }) => {
		await open_task(page, TASK);
		// The widget is rendered by form_render, so the row has to be expanded first.
		await page
			.locator('[data-fieldname="sources"] .grid-body .rows > .grid-row')
			.locator(".btn-open-row")
			.click();
		const table = page.locator('[data-fieldname="source_filters"] table');
		await expect(table).toContainText("Click to set filters");
		await table.click();
		await expect(page.locator(".modal-title")).toContainText("Which Records");
		// An unfiltered source seeds one blank row, so the field-name autocomplete is on
		// screen rather than behind "Add a Filter" — what the list view's popover does.
		await expect(page.locator(".modal .fieldname-select-area input")).toBeAttached();
		// toggle_empty_filters(false) only CSS-hides .empty-filters (jQuery .toggle()) —
		// the "No filters selected" text node is still in the DOM either way, and
		// a text assertion does not care about visibility. The node must exist, hidden.
		const empty = page.locator(".modal .empty-filters");
		await expect(empty).toBeAttached();
		await expect(empty).not.toBeVisible();
	});

	// The regression the 3-element shape caused: source_filters written by the server
	// (_apply_email_trigger, the seeded example task) omits the leading doctype, and
	// FilterGroup only reads the 4-element form — it rejected the row, opened empty, and
	// Set then wrote [] over the user's filters.
	test("round-trips a filter stored without a leading doctype", async ({ page }) => {
		await open_task(page, FILTERED_TASK);
		await page
			.locator('[data-fieldname="sources"] .grid-body .rows > .grid-row')
			.locator(".btn-open-row")
			.click();

		// The table reads the same shape the dialog does.
		const table = page.locator('[data-fieldname="source_filters"] table');
		await expect(table).toContainText("status");
		await expect(table).toContainText("Open");

		await table.click();
		await expect(page.locator(".modal-title")).toContainText("Which Records");
		// The filter arrived instead of being refused as a doctype named "status".
		await expect(page.locator(".modal .fieldname-select-area input")).toHaveValue("Status");
		await expect(
			page.locator(".msgprint, .modal-body").filter({ hasText: "Invalid filter" })
		).toHaveCount(0);

		// Set without touching anything keeps the filter rather than clearing it.
		await page.locator(".modal .btn-modal-primary", { hasText: "Set" }).click();
		await expect(table).toContainText("Open");
	});

	// Covers crema.bundle.js's per-field draft button (Path D) — notify_to (Small Text)
	// rather than instruction (also on this form) so the dialog's own "instruction"
	// prompt field never shares a data-fieldname with the field under test.
	test("drafts one field from a per-field button, without touching the rest of the form", async ({
		page,
	}) => {
		await open_task(page, TASK);

		// The stub also proposes schedule_preset — client-side narrowing to the one field
		// this button is scoped to must drop it, same as the server's own _filter_diff
		// would if this reply had come back unfiltered.
		await page.route(method_url("transform_api"), (route) =>
			route.fulfill({
				status: 200,
				json: {
					message: {
						set: { notify_to: "ops@example.com", schedule_preset: "Weekly" },
						child_set: {},
						reason: "drafted",
					},
				},
			})
		);

		await page.locator(".frappe-control[data-fieldname=notify_to] .btn-crema-draft").click();
		const modal = page.locator(".modal.show");
		await modal
			.locator(".frappe-control[data-fieldname=instruction] textarea")
			.fill("the person who should see failures");
		await Promise.all([
			page.waitForResponse((res) => method_url("transform_api")(new URL(res.url()))),
			modal.locator(".btn-modal-primary").click(),
		]);
		await modal.locator(".btn-modal-primary", { hasText: "Apply" }).click();

		await expect(
			page.locator(".frappe-control[data-fieldname=notify_to] textarea")
		).toHaveValue("ops@example.com");
		await expect(
			page.locator(".frappe-control[data-fieldname=schedule_preset] select")
		).not.toHaveValue("Weekly");
	});

	// crema_write_list_html must escape a record name before handing it to
	// get_form_link, or a name holding markup runs as HTML inside the confirm dialog.
	// Undo Last Run now lives inside the "More" group (the button weight fix), so it
	// opens that dropdown first rather than finding the button directly on the page.
	test("shows a malicious record name as text in the Undo Last Run confirm dialog", async ({
		page,
	}) => {
		await open_task(page, XSS_TASK);
		await page.evaluate(() => {
			window.__xss = undefined;
		});

		// Opens the group, same as a person would. The group's own dropdown-item anchor
		// is frappe's "hidden item store" (page.js get_or_add_inner_group_button) — it
		// stays display:none even once open (an espresso panel renders the visible menu
		// from a snapshot of it elsewhere in the DOM), but it still carries both the
		// text-danger class this test checks and the real click handler, so a click event
		// dispatched on it exercises the same handler a click through the open menu would.
		const group = page.locator('.inner-group-button[data-label="More"]');
		await group.getByRole("button", { name: "More", exact: true }).click();
		const undo = group.locator(".dropdown-item", { hasText: "Undo Last Run" });
		await expect(undo).toHaveClass(/text-danger/);
		await undo.dispatchEvent("click");
		await expect(page.locator(".modal-body")).toContainText(XSS_NAME);
		// The markup stayed text: no element was built from it, and its handler never ran.
		await expect(page.locator(".modal-body img")).toHaveCount(0);
		expect(await page.evaluate(() => window.__xss)).toBeUndefined();
	});

	// --- the pipeline strip ---------------------------------------------------------

	test("renders a card for each pipeline step", async ({ page }) => {
		await open_task(page, TASK);

		await expect(page.locator(".crema-pipeline-cards .crema-pipeline-card")).toHaveCount(5);
		const cards = page.locator(".crema-pipeline-cards");
		await expect(cards).toContainText("Trigger");
		await expect(cards).toContainText("Schedule");
		await expect(cards).toContainText("Source");
		await expect(cards).toContainText("1 source: Document Query");
		await expect(cards).toContainText("Write");
		await expect(cards).toContainText("Update the Records It Read");
	});

	test("marks the failed step and the steps before and after it", async ({ page }) => {
		await open_task(page, FAILED_TASK);

		const card = (field) => page.locator(`.crema-pipeline-card[data-crema-field="${field}"]`);
		await expect(card("trigger")).toContainText("Done");
		await expect(card("sources")).toContainText("Done");
		await expect(card("plan_json")).toContainText("Failed");
		await expect(card("plan_prompt")).toContainText("Not reached");
		await expect(card("action")).toContainText("Not reached");
		await expect(page.locator(".crema-pipeline-cards").locator("xpath=..")).toContainText(
			"the model returned an unparsable plan"
		);
	});

	// Regression pin: last_failed_stage is "Setup" itself on a Setup failure (run_task's
	// own Setup guard, outside _run_inside), not blank — the line under the strip has to
	// match that exact value, not "falsy", or it never shows for a real Setup failure.
	test("shows a line under the strip, and no card marked failed, on a Setup failure", async ({
		page,
	}) => {
		await open_task(page, SETUP_FAILED_TASK);

		// The strip has rendered before an absence counts.
		await expect(page.locator(".crema-pipeline-card")).toHaveCount(5);
		await expect(
			page.locator(".crema-pipeline-card").filter({ hasText: "Failed" })
		).toHaveCount(0);
		await expect(page.locator(".crema-pipeline-cards").locator("xpath=..")).toContainText(
			"The task setup failed before any step ran."
		);
	});

	test("greys the Plan and Extract cards on a File Query task", async ({ page }) => {
		await open_task(page, FILE_QUERY_TASK);

		const card = (field) => page.locator(`.crema-pipeline-card[data-crema-field="${field}"]`);
		await expect(card("plan_json")).toContainText(
			"Not used: each file is read directly into records"
		);
		await expect(card("plan_json")).toHaveCSS("opacity", "0.55");
		await expect(card("plan_prompt")).toContainText(
			"Not used: each file is read directly into records"
		);
		await expect(card("action")).toContainText("Create or Update Records");
	});

	test("shows the pending proposal count as a link to the filtered Crema Proposal list", async ({
		page,
	}) => {
		await open_task(page, PROPOSE_TASK);

		const pending = page.locator(
			'.crema-pipeline-card[data-crema-field="action"] [data-crema-pending]'
		);
		await expect(pending).toContainText("2 pending");
		await pending.click();
		await expect.poll(() => new URL(page.url()).pathname).toContain("/crema-proposal");
		await expect.poll(() => new URL(page.url()).search).toContain(`task=${PROPOSE_TASK}`);
		await expect.poll(() => new URL(page.url()).search).toContain("status=Pending");
	});

	test("scrolls to a step's field when its card is clicked", async ({ page }) => {
		await open_task(page, TASK);
		const card = page.locator('.crema-pipeline-card[data-crema-field="sources"]');
		await expect(card).toBeVisible();
		// No built-in spy: wrap the method, keep the original behaviour, record the arguments.
		await page.evaluate(() => {
			window.__scroll_calls = [];
			const frm = window.cur_frm;
			const original = frm.scroll_to_field;
			frm.scroll_to_field = function (...args) {
				window.__scroll_calls.push(args);
				return original.apply(this, args);
			};
		});

		await card.click();
		await expect
			.poll(() =>
				page.evaluate(() => window.__scroll_calls.some((args) => args[0] === "sources"))
			)
			.toBe(true);
	});

	test("warns, without blocking, when a saved task with no Dry Run is enabled", async ({
		page,
	}) => {
		await open_task(page, TASK);

		const enabled = page.locator(
			'.frappe-control[data-fieldname="enabled"] .input-area input[type="checkbox"]'
		);
		await enabled.check({ force: true });
		await expect(
			page.locator(".es-toast, .desk-alert").filter({ hasText: "Do a Dry Run" })
		).not.toHaveCount(0);
		// Not blocked — the checkbox stays ticked and the form is left dirty, not reset.
		await expect(enabled).toBeChecked();
	});

	test.describe("while Crema is off", () => {
		// One test in this group — afterAll clears the switch, so a test that failed
		// halfway through toggling it cannot leave every later spec broken.
		test.afterAll(async ({ admin }) => {
			// The kill switch stops every LLM call site-wide — it must not survive a test
			// that failed halfway through toggling it, or every later spec starts broken.
			await admin.set_value("Crema Settings", "Crema Settings", { disabled: 0 });
		});

		test("hides Dry Run, Run Now and every Ask Crema affordance", async ({ page, admin }) => {
			await admin.set_value("Crema Settings", "Crema Settings", { disabled: 1 });

			await open_task(page, TASK);

			// The same text sits in the pipeline panel too, so the first match is the notice.
			await expect(page.getByText("Crema is off. No task runs.").first()).toBeVisible();
			// The cards still show the configuration, grey and with no run marks.
			await expect(page.locator(".crema-pipeline-cards")).toHaveAttribute(
				"style",
				/opacity/
			);
			await expect(page.locator(".crema-pipeline-cards [data-crema-mark]")).toHaveCount(0);
			await expect(
				page.locator('.frappe-control[data-fieldname="pipeline_html"]')
			).toContainText("Crema is off. No task runs.");
			await expect(page.getByRole("button", { name: "Dry Run", exact: true })).toHaveCount(
				0
			);
			await expect(page.getByRole("button", { name: "Run Now", exact: true })).toHaveCount(
				0
			);

			// The robot button crema.bundle.js adds to every list view toolbar — gone too,
			// since crema_allowed() reads the same frappe.boot.crema_disabled flag.
			await goto_desk(page, "/desk/crema-automation-task");
			await expect(
				page.locator(".list-row-container:visible, .no-result:visible")
			).not.toHaveCount(0);
			await expect(page.locator("[data-crema]")).toHaveCount(0);
		});
	});
});
