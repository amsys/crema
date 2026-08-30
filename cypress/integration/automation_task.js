// Covers crema_automation_task.js: the status panel, the Dry Run preview, and the
// source-filter builder. Every server call is stubbed with cy.intercept — same rule the
// Python suite follows (patch at the boundary, never call a live model).
//
// Run with: bench --site fcr.local run-ui-tests crema --headless
context("Crema Automation Task form", () => {
	const TASK = "_cypress_crema_task";
	// A second task rather than a second source row on TASK: "Update the Records It Read"
	// is refused unless the task has exactly one Document Query source.
	const FILTERED_TASK = "_cypress_crema_filtered_task";

	before(() => {
		cy.visit("/login");
		cy.login();
		cy.insert_doc(
			"Crema Automation Task",
			{
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
				instruction: "Set a priority on every open item.",
				action: "Update the Records It Read",
			},
			true
		);
		cy.insert_doc(
			"Crema Automation Task",
			{
				task_name: FILTERED_TASK,
				enabled: 0,
				trigger: "Schedule",
				schedule_preset: "Daily 03:00",
				sources: [
					{
						source_type: "Document Query",
						source_doctype: "Contact",
						// The 3-element shape the server itself writes — no leading doctype.
						source_filters: JSON.stringify([["status", "=", "Open"]]),
						source_limit: 50,
					},
				],
				interface: "complex",
				instruction: "Summarise the open items.",
				action: "No Changes",
			},
			true
		);
	});

	beforeEach(() => {
		cy.intercept("POST", "/api/method/crema.api.get_interfaces", {
			body: {
				message: [
					{ value: "simple", label: "Default" },
					{ value: "complex", label: "Complex" },
					{ value: "extraction", label: "Extraction" },
				],
			},
		}).as("interfaces");
	});

	it("shows a status indicator and the buttons instead of a bare form", () => {
		cy.visit(`/app/crema-automation-task/${TASK}`);
		cy.wait("@interfaces");
		// Never run yet, and disabled. The status lives in the page header indicator now
		// rather than in an HTML field, so that is what has to say so.
		cy.get(".page-head .page-indicator-pill").should("contain.text", "Never run");
		cy.findByRole("button", { name: "Dry Run" }).should("exist");
		cy.findByRole("button", { name: "Run Now" }).should("exist");
	});

	it("previews extracted rows without writing anything", () => {
		cy.intercept("POST", "/api/method/crema.api.dry_run_automation", {
			body: {
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
		}).as("dry_run");

		cy.visit(`/app/crema-automation-task/${TASK}`);
		cy.wait("@interfaces");
		cy.findByRole("button", { name: "Dry Run" }).click();
		cy.wait("@dry_run");

		cy.get(".modal-body").should("contain.text", "would be updated");
		cy.get(".modal-body").should("contain.text", "Nothing was saved");
		cy.get(".modal-body").should("contain.text", "CONT-0001");
	});

	it("routes a 417 from the dry run through crema_show_error", () => {
		cy.intercept("POST", "/api/method/crema.api.dry_run_automation", {
			statusCode: 417,
			body: { message: { blocked: true, reason: "blocked: prompt injection" } },
		}).as("blocked");

		cy.visit(`/app/crema-automation-task/${TASK}`);
		cy.wait("@interfaces");
		cy.findByRole("button", { name: "Dry Run" }).click();
		cy.wait("@blocked");

		cy.get(".modal-title").should("contain.text", "Blocked");
		cy.get(".modal-body").should("contain.text", "prompt injection");
	});

	it("shows each source's settings as separate grid columns", () => {
		cy.visit(`/app/crema-automation-task/${TASK}`);
		cy.wait("@interfaces");

		// The old single "Details" column became several, so the limit and the two checks
		// can be read — and changed — without opening the row. The headers are kept short
		// on purpose: at one grid column wide, a longer one is truncated to "Attach…".
		cy.get('[data-fieldname="sources"] .grid-heading-row').as("head");
		cy.get("@head").should("contain.text", "What");
		cy.get("@head").should("contain.text", "Per Run");
		cy.get("@head").should("contain.text", "Changed");
		cy.get("@head").should("contain.text", "Files");
	});

	it("reveals 'Stop if a Source Fails' as soon as a second source row exists", () => {
		// depends_on is not re-evaluated when a grid row is added, so without the
		// sources_add handler this only appears after the next save.
		cy.visit(`/app/crema-automation-task/${TASK}`);
		cy.wait("@interfaces");

		cy.get('[data-fieldname="on_source_error"]').should("not.be.visible");
		cy.get('[data-fieldname="sources"] .grid-add-row').click();
		cy.get('[data-fieldname="on_source_error"]').should("be.visible");
	});

	it("summarises each source in the grid", () => {
		cy.visit(`/app/crema-automation-task/${TASK}`);
		cy.wait("@interfaces");
		// What the row reads and how, without opening it. .grid-row also matches the
		// heading row nested inside .grid-heading-row (grid.js) — data rows live in
		// .grid-body .rows.
		cy.get('[data-fieldname="sources"] .grid-body .rows > .grid-row').first().as("row");
		cy.get("@row").should("contain.text", "Document Query");
		// What carries the record type and, when there is one, the filter count.
		cy.get("@row").should("contain.text", "Contact");
		// The limit is its own cell now, not part of a "· 50/run ·" summary string.
		cy.get("@row").find('[data-fieldname="source_limit"]').should("contain.text", "50");
	});

	it("opens a filter builder from the source row's filters table", () => {
		cy.visit(`/app/crema-automation-task/${TASK}`);
		cy.wait("@interfaces");
		// The widget is rendered by form_render, so the row has to be expanded first.
		cy.get('[data-fieldname="sources"] .grid-body .rows > .grid-row')
			.first()
			.find(".btn-open-row")
			.click();
		cy.get('[data-fieldname="source_filters"] table').should(
			"contain.text",
			"Click to set filters"
		);
		cy.get('[data-fieldname="source_filters"] table').click();
		cy.get(".modal-title").should("contain.text", "Which Records");
		// An unfiltered source seeds one blank row, so the field-name autocomplete is on
		// screen rather than behind "Add a Filter" — what the list view's popover does.
		cy.get(".modal .fieldname-select-area input").should("exist");
		// toggle_empty_filters(false) only CSS-hides .empty-filters (jQuery .toggle()) —
		// the "No filters selected" text node is still in the DOM either way, and
		// jQuery's .text() (what "contain.text" reads) doesn't care about visibility.
		cy.get(".modal .empty-filters").should("not.be.visible");
	});

	// The regression the 3-element shape caused: source_filters written by the server
	// (_apply_email_trigger, the seeded example task) omits the leading doctype, and
	// FilterGroup only reads the 4-element form — it rejected the row, opened empty, and
	// Set then wrote [] over the user's filters.
	it("round-trips a filter stored without a leading doctype", () => {
		cy.visit(`/app/crema-automation-task/${FILTERED_TASK}`);
		cy.wait("@interfaces");
		cy.get('[data-fieldname="sources"] .grid-body .rows > .grid-row')
			.first()
			.find(".btn-open-row")
			.click();

		// The table reads the same shape the dialog does.
		cy.get('[data-fieldname="source_filters"] table').as("table");
		cy.get("@table").should("contain.text", "status");
		cy.get("@table").should("contain.text", "Open");

		cy.get("@table").click();
		cy.get(".modal-title").should("contain.text", "Which Records");
		// The filter arrived instead of being refused as a doctype named "status".
		cy.get(".modal .fieldname-select-area input").should("have.value", "Status");
		cy.get(".msgprint, .modal-body").should("not.contain.text", "Invalid filter");

		// Set without touching anything keeps the filter rather than clearing it.
		cy.get(".modal .btn-modal-primary").contains("Set").click();
		cy.get('[data-fieldname="source_filters"] table').should("contain.text", "Open");
	});

	// Covers crema.bundle.js's per-field draft button (Path D) — notify_to (Small Text)
	// rather than instruction (also on this form) so the dialog's own "instruction"
	// prompt field never shares a data-fieldname with the field under test.
	it("drafts one field from a per-field button, without touching the rest of the form", () => {
		cy.visit(`/app/crema-automation-task/${TASK}`);
		cy.wait("@interfaces");

		// The stub also proposes schedule_preset — client-side narrowing to the one field
		// this button is scoped to must drop it, same as the server's own _filter_diff
		// would if this reply had come back unfiltered.
		cy.intercept("POST", "/api/method/crema.api.transform_api", {
			statusCode: 200,
			body: {
				message: {
					set: { notify_to: "ops@example.com", schedule_preset: "Weekly" },
					child_set: {},
					reason: "drafted",
				},
			},
		}).as("draft");

		cy.get(".frappe-control[data-fieldname=notify_to] .btn-crema-draft").click();
		cy.get(".modal.show").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
				"the person who should see failures"
			);
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@draft");
		cy.get(".modal.show").within(() => cy.get(".btn-modal-primary").contains("Apply").click());

		cy.get(".frappe-control[data-fieldname=notify_to] textarea").should(
			"have.value",
			"ops@example.com"
		);
		cy.get(".frappe-control[data-fieldname=schedule_preset] select").should(
			"not.have.value",
			"Weekly"
		);
	});

	after(() => {
		cy.remove_doc("Crema Automation Task", TASK);
		cy.remove_doc("Crema Automation Task", FILTERED_TASK);
	});
});
