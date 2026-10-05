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
	// A third task carrying a malicious record name in last_written_json, for the Undo
	// Last Run confirm dialog test below — crema_write_list_html must escape it.
	const XSS_TASK = "_cypress_crema_xss_task";
	const XSS_NAME = "<img src=x onerror=window.__xss=1>";
	// A File Query task, for the "Plan and Extract are not used" strip test.
	const FILE_QUERY_TASK = "_cypress_crema_file_query_task";
	// A task seeded already Failed at Plan, for the strip's mark test — set directly, the
	// same way XSS_TASK seeds last_written_json: both are read-only fields, but nothing
	// stops a direct insert from setting them, and a real run is not worth driving here.
	const FAILED_TASK = "_cypress_crema_failed_task";
	// A task seeded already Failed at Setup — last_failed_stage is "Setup" itself here,
	// not blank, since run_task tags that failure before _run_inside is ever reached.
	const SETUP_FAILED_TASK = "_cypress_crema_setup_failed_task";
	// A Propose Only task with two Pending rows parked against it, for the Write card's
	// pending-proposals link.
	const PROPOSE_TASK = "_cypress_crema_propose_task";
	const PROPOSE_NAMES = [];

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
		cy.insert_doc(
			"Crema Automation Task",
			{
				task_name: XSS_TASK,
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
				instruction: "Create a record per contact.",
				action: "Create or Update Records",
				target_doctype: "Contact",
				// A past run's write summary — never a real record, this doctype/name pair
				// only has to render in the confirm dialog below.
				last_written_json: JSON.stringify({ created: [["Contact", XSS_NAME]] }),
			},
			true
		);
		cy.insert_doc(
			"Crema Automation Task",
			{
				task_name: FILE_QUERY_TASK,
				enabled: 0,
				trigger: "Schedule",
				schedule_preset: "Daily 03:00",
				sources: [
					{ source_type: "File Query", source_doctype: "File", source_filters: "[]" },
				],
				interface: "complex",
				instruction: "Read each uploaded invoice into a record.",
				action: "Create or Update Records",
				target_doctype: "Contact",
				match_on: "email_id",
			},
			true
		);
		cy.insert_doc(
			"Crema Automation Task",
			{
				task_name: FAILED_TASK,
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
				action: "Create or Update Records",
				target_doctype: "Contact",
				last_status: "Failed",
				last_failed_stage: "Plan",
				last_run: new Date().toISOString().slice(0, 19).replace("T", " "),
				last_error: "the model returned an unparsable plan",
			},
			true
		);
		cy.insert_doc(
			"Crema Automation Task",
			{
				task_name: SETUP_FAILED_TASK,
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
				action: "Create or Update Records",
				target_doctype: "Contact",
				last_status: "Failed",
				last_failed_stage: "Setup",
				last_run: new Date().toISOString().slice(0, 19).replace("T", " "),
				last_error: "interface unresolvable — the provider is disabled",
			},
			true
		);
		cy.insert_doc(
			"Crema Automation Task",
			{
				task_name: PROPOSE_TASK,
				// Disabled on purpose: the Task filter on the Proposal list must keep a
				// disabled task (crema_proposal_list.js onload, include_disabled).
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
				instruction: "Park a proposal per open item.",
				action: "Propose Only",
				target_doctype: "Contact",
			},
			true
		).then(() => {
			[1, 2].forEach(() => {
				cy.insert_doc(
					"Crema Proposal",
					{
						task: PROPOSE_TASK,
						status: "Pending",
						target_doctype: "Contact",
						payload_json: "{}",
						mapping_json: "{}",
					},
					true
				).then((doc) => PROPOSE_NAMES.push(doc.name));
			});
		});
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

	// crema_write_list_html must escape a record name before handing it to
	// get_form_link, or a name holding markup runs as HTML inside the confirm dialog.
	// Undo Last Run now lives inside the "More" group (the button weight fix), so it
	// opens that dropdown first rather than finding the button directly on the page.
	it("shows a malicious record name as text in the Undo Last Run confirm dialog", () => {
		cy.visit(`/app/crema-automation-task/${XSS_TASK}`);
		cy.wait("@interfaces");
		cy.window().then((win) => {
			win.__xss = undefined;
		});

		// Opens the group, same as a person would. The group's own dropdown-item anchor
		// is frappe's "hidden item store" (page.js get_or_add_inner_group_button) — it
		// stays display:none even once open (an espresso panel renders the visible menu
		// from a snapshot of it elsewhere in the DOM), but it still carries both the
		// text-danger class this test checks and the real click handler, so a forced
		// click on it exercises the same handler a click through the open menu would.
		cy.get('.inner-group-button[data-label="More"]').find("button").first().click();
		cy.get('.inner-group-button[data-label="More"] .dropdown-item')
			.contains("Undo Last Run")
			.should("have.class", "text-danger")
			.click({ force: true });
		cy.get(".modal-body").should("contain.text", XSS_NAME);
		cy.window().its("__xss").should("eq", undefined);
	});

	// --- the pipeline strip ---------------------------------------------------------

	it("renders a card for each pipeline step", () => {
		cy.visit(`/app/crema-automation-task/${TASK}`);
		cy.wait("@interfaces");

		cy.get(".crema-pipeline-cards .crema-pipeline-card").should("have.length", 5);
		cy.get(".crema-pipeline-cards").should("contain.text", "Trigger");
		cy.get(".crema-pipeline-cards").should("contain.text", "Schedule");
		cy.get(".crema-pipeline-cards").should("contain.text", "Source");
		cy.get(".crema-pipeline-cards").should("contain.text", "1 source: Document Query");
		cy.get(".crema-pipeline-cards").should("contain.text", "Write");
		cy.get(".crema-pipeline-cards").should("contain.text", "Update the Records It Read");
	});

	it("marks the failed step and the steps before and after it", () => {
		cy.visit(`/app/crema-automation-task/${FAILED_TASK}`);
		cy.wait("@interfaces");

		cy.get('.crema-pipeline-card[data-crema-field="trigger"]').should("contain.text", "Done");
		cy.get('.crema-pipeline-card[data-crema-field="sources"]').should("contain.text", "Done");
		cy.get('.crema-pipeline-card[data-crema-field="plan_json"]').should(
			"contain.text",
			"Failed"
		);
		cy.get('.crema-pipeline-card[data-crema-field="plan_prompt"]').should(
			"contain.text",
			"Not reached"
		);
		cy.get('.crema-pipeline-card[data-crema-field="action"]').should(
			"contain.text",
			"Not reached"
		);
		cy.get(".crema-pipeline-cards")
			.parent()
			.should("contain.text", "the model returned an unparsable plan");
	});

	// Regression pin: last_failed_stage is "Setup" itself on a Setup failure (run_task's
	// own Setup guard, outside _run_inside), not blank — the line under the strip has to
	// match that exact value, not "falsy", or it never shows for a real Setup failure.
	it("shows a line under the strip, and no card marked failed, on a Setup failure", () => {
		cy.visit(`/app/crema-automation-task/${SETUP_FAILED_TASK}`);
		cy.wait("@interfaces");

		cy.get(".crema-pipeline-card").should("not.contain.text", "Failed");
		cy.get(".crema-pipeline-cards")
			.parent()
			.should("contain.text", "The task setup failed before any step ran.");
	});

	it("greys the Plan and Extract cards on a File Query task", () => {
		cy.visit(`/app/crema-automation-task/${FILE_QUERY_TASK}`);
		cy.wait("@interfaces");

		cy.get('.crema-pipeline-card[data-crema-field="plan_json"]')
			.should("contain.text", "Not used: each file is read directly into records")
			.should("have.css", "opacity", "0.55");
		cy.get('.crema-pipeline-card[data-crema-field="plan_prompt"]').should(
			"contain.text",
			"Not used: each file is read directly into records"
		);
		cy.get('.crema-pipeline-card[data-crema-field="action"]').should(
			"contain.text",
			"Create or Update Records"
		);
	});

	it("shows the pending proposal count as a link to the filtered Crema Proposal list", () => {
		cy.visit(`/app/crema-automation-task/${PROPOSE_TASK}`);
		cy.wait("@interfaces");

		cy.get('.crema-pipeline-card[data-crema-field="action"] [data-crema-pending]')
			.should("contain.text", "2 pending")
			.click();
		cy.location("pathname").should("contain", "/crema-proposal");
		cy.location("search").should("contain", `task=${PROPOSE_TASK}`);
		cy.location("search").should("contain", "status=Pending");
	});

	it("scrolls to a step's field when its card is clicked", () => {
		cy.visit(`/app/crema-automation-task/${TASK}`);
		cy.wait("@interfaces");
		cy.window()
			.its("cur_frm")
			.then((frm) => cy.spy(frm, "scroll_to_field").as("scroll"));

		cy.get('.crema-pipeline-card[data-crema-field="sources"]').click();
		cy.get("@scroll").should("have.been.calledWith", "sources");
	});

	it("warns, without blocking, when a saved task with no Dry Run is enabled", () => {
		cy.visit(`/app/crema-automation-task/${TASK}`);
		cy.wait("@interfaces");

		cy.get(
			'.frappe-control[data-fieldname="enabled"] .input-area input[type="checkbox"]'
		).check({
			force: true,
		});
		cy.get(".es-toast, .desk-alert").should("contain.text", "Do a Dry Run");
		// Not blocked — the checkbox stays ticked and the form is left dirty, not reset.
		cy.get(
			'.frappe-control[data-fieldname="enabled"] .input-area input[type="checkbox"]'
		).should("be.checked");
	});

	context("while Crema is off", () => {
		// One test in this group — after() clears the switch exactly the way afterEach()
		// would, and .eslintrc's globals list (shared across the app) does not carry
		// afterEach.
		after(() => {
			// The kill switch stops every LLM call site-wide — it must not survive a test
			// that failed halfway through toggling it, or every later spec starts broken.
			cy.call("frappe.client.set_value", {
				doctype: "Crema Settings",
				name: "Crema Settings",
				fieldname: { disabled: 0 },
			});
		});

		it("hides Dry Run, Run Now and every Ask Crema affordance", () => {
			cy.call("frappe.client.set_value", {
				doctype: "Crema Settings",
				name: "Crema Settings",
				fieldname: { disabled: 1 },
			});

			cy.visit(`/app/crema-automation-task/${TASK}`);
			cy.wait("@interfaces");

			cy.contains("Crema is off. No task runs.").should("be.visible");
			// The cards still show the configuration, grey and with no run marks.
			cy.get(".crema-pipeline-cards").should("have.attr", "style").and("contain", "opacity");
			cy.get(".crema-pipeline-cards [data-crema-mark]").should("not.exist");
			cy.get('.frappe-control[data-fieldname="pipeline_html"]').should(
				"contain.text",
				"Crema is off. No task runs."
			);
			cy.findByRole("button", { name: "Dry Run" }).should("not.exist");
			cy.findByRole("button", { name: "Run Now" }).should("not.exist");

			// The robot button crema.bundle.js adds to every list view toolbar — gone too,
			// since crema_allowed() reads the same frappe.boot.crema_disabled flag.
			cy.visit("/app/crema-automation-task");
			cy.get(".list-row-container, .no-result");
			cy.get("[data-crema]").should("not.exist");
		});
	});

	after(() => {
		PROPOSE_NAMES.forEach((name) => cy.remove_doc("Crema Proposal", name, true));
		cy.remove_doc("Crema Automation Task", TASK);
		cy.remove_doc("Crema Automation Task", FILTERED_TASK);
		cy.remove_doc("Crema Automation Task", XSS_TASK);
		cy.remove_doc("Crema Automation Task", FILE_QUERY_TASK);
		cy.remove_doc("Crema Automation Task", FAILED_TASK);
		cy.remove_doc("Crema Automation Task", SETUP_FAILED_TASK);
		cy.remove_doc("Crema Automation Task", PROPOSE_TASK);
	});
});
