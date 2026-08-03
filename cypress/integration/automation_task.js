// Covers crema_automation_task.js: the status panel, the Dry Run preview, and the
// source-filter builder. Every server call is stubbed with cy.intercept — same rule the
// Python suite follows (patch at the boundary, never call a live model).
//
// Run with: bench --site fcr.local run-ui-tests crema --headless
context("Crema Automation Task form", () => {
	const TASK = "_cypress_crema_task";

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
						source_doctype: "ToDo",
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
		cy.get(".page-head .indicator-pill").should("contain.text", "Never run");
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
						{ id: "TODO-0001", prio: "High" },
						{ id: "TODO-0002", prio: "Low" },
					],
					plan: {
						extract: { prompt: "Copy each name through." },
						map: {
							doctype: "ToDo",
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
		cy.get(".modal-body").should("contain.text", "TODO-0001");
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
		// What the row reads and how, without opening it.
		cy.get('[data-fieldname="sources"] .grid-row').first().as("row");
		cy.get("@row").should("contain.text", "Document Query");
		// What carries the record type and, when there is one, the filter count.
		cy.get("@row").should("contain.text", "ToDo");
		// The limit is its own cell now, not part of a "· 50/run ·" summary string.
		cy.get("@row").find('[data-fieldname="source_limit"]').should("contain.text", "50");
	});

	it("opens a filter builder from the source row's filters table", () => {
		cy.visit(`/app/crema-automation-task/${TASK}`);
		cy.wait("@interfaces");
		// The widget is rendered by form_render, so the row has to be expanded first.
		cy.get('[data-fieldname="sources"] .grid-row').first().find(".btn-open-row").click();
		cy.get('[data-fieldname="source_filters"] table').should(
			"contain.text",
			"Click to set filters"
		);
		cy.get('[data-fieldname="source_filters"] table').click();
		cy.get(".modal-title").should("contain.text", "Which Records");
		cy.get(".modal .filter-area, .modal .fieldname-select-area").should("exist");
	});

	after(() => {
		cy.remove_doc("Crema Automation Task", TASK);
	});
});
