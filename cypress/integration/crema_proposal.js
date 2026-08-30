// Covers the Crema Proposal review workflow: the form (crema_proposal.js — the
// diff_preview table and the Approve/Discard/Undo buttons) and the list view
// (crema_proposal_list.js — the three bulk actions and the preview dialog Approve asks
// for before it writes anything).
//
// The three crema.api.*_proposals endpoints run for real here rather than stubbed. The
// rule the other specs follow is "never call a live model", and none of these does:
// apply_proposal replays the parked write through the same _upsert an unattended run
// uses, and undo_proposal deletes what that wrote. A stub could never show a row move to
// Approved, which is the whole point of this doctype.
//
// The price is a configured site: apply_proposal and undo_proposal both resolve the
// task's use case through client._resolve, which needs an enabled provider and an
// isolation user. before() seeds those the same way crema.testing.seed_provider seeds
// them for the Python suite — non-overriding, so a bench that is already configured is
// left exactly as it is, and after() clears only what this file set.
//
// Run with: bench --site fcr.local run-ui-tests crema --headless
const TASK = "_cypress_crema_proposal_task";
const PROVIDER = "_cypress_crema_proposal_provider";
const RUNNER = "cypress-crema-proposal-runner@example.com";
const RUNNER_ROLE = "_cypress_crema_proposal_role";

// What each parked row maps onto. Contact for the same reason desk_ui.js drives it: two
// plain Data fields to write (first_name, designation), and read/write/create/delete for
// the built-in "All" role — so the low-privilege account the approval runs as needs no
// permission of its own.
const MAPPING = JSON.stringify({
	doctype: "Contact",
	match_fields: ["first_name"],
	field_map: { who: "first_name", what: "designation" },
});

// A payload key the field_map does not name. crema_proposal_record must drop it: only
// the mapped keys are ever written, so only those may appear in the preview.
const UNMAPPED = "must never be written";

const PROPOSALS = [
	{ fingerprint: "_cypress_crema_proposal_one", who: "_cypress Proposal One", what: "Tester" },
	{ fingerprint: "_cypress_crema_proposal_two", who: "_cypress Proposal Two", what: "Reviewer" },
];

// Filled by seed_proposals() in beforeEach — the rows are hash-named, so the name is
// only known once the insert comes back.
let names = [];

// Whether this file seeded Crema Settings' defaults, i.e. whether after() has anything to
// undo there — plus the values the site held before, so after() restores them instead of
// blanking: a bench can carry a Default Isolation User with no Default Provider, and
// that user is not this file's to clear.
let seeded_defaults = false;
let prior_defaults = { default_model: "", default_isolation_user: "" };

// add_custom_button renders into the page header (.page-actions), so every lookup of an
// Approve/Discard/Undo button is scoped there. An unscoped cy.contains("button",
// "Discard") also matches the form timeline's own comment-box Discard button, which
// frappe keeps in the DOM display:none — that one is first in document order, so it wins
// the match and every assertion then reads the wrong button.
function action_button(label) {
	return cy.get(".page-actions").contains("button", label);
}

// Same helper, same reason, as desk_ui.js's own: frappe never removes a hidden dialog
// from the DOM, so .modal.show is the only reliable handle on the visible one.
function crema_modal() {
	return cy.get(".modal.show");
}

// frappe.xcall resolves to the response's `message`, unlike cy.call which yields the whole
// body — used wherever this file reads a result rather than firing and forgetting.
function xcall(method, args) {
	return cy.window().then((win) => win.frappe.xcall(method, args));
}

function proposal_rows(fields) {
	return xcall("frappe.client.get_list", {
		doctype: "Crema Proposal",
		filters: { task: TASK },
		fields,
		limit_page_length: 0,
	});
}

// Undo before delete: an Approved row wrote a real Contact, and deleting the row on its
// own would strand it. undo_proposals reports a row that is not Approved as a failure and
// changes nothing, which is exactly what a Pending or Discarded row needs.
function clear_proposals() {
	proposal_rows(["name"]).then((rows) => {
		const stale = (rows || []).map((row) => row.name);
		if (!stale.length) return;
		xcall("crema.api.undo_proposals", { names: stale });
		stale.forEach((name) => cy.remove_doc("Crema Proposal", name, true));
	});
}

// An approval that matched an existing Contact updates it instead of creating it, and
// leaves nothing for Undo to delete — so a Contact left behind by an aborted run would
// silently turn the "the record is gone again" assertions below into vacuous ones. Sweep
// them, unconditionally, the same way role_gate.js deletes its users before every run.
function clear_contacts() {
	xcall("frappe.client.get_list", {
		doctype: "Contact",
		filters: { first_name: ["in", PROPOSALS.map((row) => row.who)] },
		fields: ["name"],
		limit_page_length: 0,
	}).then((rows) => (rows || []).forEach((row) => cy.remove_doc("Contact", row.name, true)));
}

function contacts_named(first_name) {
	return xcall("frappe.client.get_list", {
		doctype: "Contact",
		filters: { first_name },
		fields: ["name"],
		limit_page_length: 0,
	});
}

function seed_proposals() {
	names = [];
	PROPOSALS.forEach((row) =>
		cy
			.insert_doc("Crema Proposal", {
				task: TASK,
				target_doctype: "Contact",
				fingerprint: row.fingerprint,
				payload_json: JSON.stringify({
					who: row.who,
					what: row.what,
					unmapped_key: UNMAPPED,
				}),
				mapping_json: MAPPING,
			})
			.then((doc) => names.push(doc.name))
	);
}

context("Crema Proposal review", () => {
	before(() => {
		cy.visit("/login");
		cy.login();
		// A desk page, not /login: every xcall below reads window.frappe.
		cy.visit("/app/crema-proposal");

		// Self-healing, same reasoning as role_gate.js: an aborted earlier run leaves
		// these behind, and reusing them would reuse whatever state it left on them. The
		// proposals go first — they link to the task, which links to the user.
		clear_proposals();
		clear_contacts();
		cy.remove_doc("Crema Automation Task", TASK, true);
		cy.remove_doc("User", RUNNER, true);
		cy.remove_doc("Role", RUNNER_ROLE, true);

		cy.insert_doc("Role", { role_name: RUNNER_ROLE, desk_access: 1 }, true);
		cy.insert_doc(
			"User",
			{
				email: RUNNER,
				first_name: "Cypress Proposal Runner",
				send_welcome_email: 0,
				roles: [{ role: RUNNER_ROLE }],
			},
			true
		);

		// seed_provider's rule, in cypress: fill a blank site, never overwrite a
		// configured one. A bench that already has a Default Provider resolves
		// apply_proposal on its own, and its admin's settings are not this file's to
		// rewrite.
		xcall("frappe.client.get", {
			doctype: "Crema Settings",
			name: "Crema Settings",
		}).then((settings) => {
			if (settings.default_provider) return;
			seeded_defaults = true;
			prior_defaults = {
				default_model: settings.default_model || "",
				default_isolation_user: settings.default_isolation_user || "",
			};
			cy.insert_doc(
				"Crema Provider",
				{
					provider_name: PROVIDER,
					// A loopback base URL is the one case CremaProvider.validate enables
					// without an api_key. Nothing here ever calls it — no test in this
					// file reaches a model.
					base_url: "http://localhost:11434/v1",
					enabled: 1,
					timeout_seconds: 5,
				},
				true
			);
			// One call, not three: CremaSettings._validate_provider_isolation_pairing
			// refuses a Default Provider with no Default Isolation User, so the pair has
			// to arrive in the same save. default_model spares that save
			// _warn_missing_models' dialog.
			cy.call("frappe.client.set_value", {
				doctype: "Crema Settings",
				name: "Crema Settings",
				fieldname: {
					default_provider: PROVIDER,
					default_model: "test-model",
					default_isolation_user: RUNNER,
				},
			});
		});

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
				instruction: "Park every contact for review.",
				action: "Propose Only",
				target_doctype: "Contact",
				// Neither Administrator nor a System Manager — validate_isolation_user
				// refuses both. This is the account apply_proposal writes the Contact as.
				run_as: RUNNER,
			},
			true
		);
	});

	beforeEach(() => {
		// Every test starts from two fresh Pending rows and no leftover Contact: the
		// tests below approve and discard for real, so nothing here may inherit the
		// previous test's outcome.
		clear_proposals();
		clear_contacts();
		seed_proposals();
	});

	after(() => {
		cy.visit("/app/crema-proposal");
		clear_proposals();
		clear_contacts();
		cy.remove_doc("Crema Automation Task", TASK, true);
		if (seeded_defaults) {
			// CremaProvider.on_trash (_release_from_settings) clears Default Provider and
			// Default Model itself — the model and isolation user are restored to what
			// the site held before this file seeded, and the restore has to land before
			// the User the seed named can be deleted.
			cy.remove_doc("Crema Provider", PROVIDER, true);
			cy.call("frappe.client.set_value", {
				doctype: "Crema Settings",
				name: "Crema Settings",
				fieldname: {
					default_model: prior_defaults.default_model,
					default_isolation_user: prior_defaults.default_isolation_user,
				},
			});
		}
		cy.remove_doc("User", RUNNER, true);
		cy.remove_doc("Role", RUNNER_ROLE, true);
	});

	it("renders the parked write as a table of the fields it would write", () => {
		cy.visit(`/app/crema-proposal/${names[0]}`);

		// The point of the doctype is a human reading the write before it happens, so
		// diff_preview shows it as a table — the raw payload and mapping stay in the
		// collapsed Detail section below.
		cy.get('[data-fieldname="diff_preview"] table').as("preview");
		cy.get("@preview").should("contain.text", "first_name");
		cy.get("@preview").should("contain.text", PROPOSALS[0].who);
		cy.get("@preview").should("contain.text", "designation");
		cy.get("@preview").should("contain.text", PROPOSALS[0].what);
		// crema_proposal_record maps the payload through mapping_json's field_map: a
		// payload key the map does not name is not part of the write, so it may not
		// appear here either.
		cy.get("@preview").should("not.contain.text", "unmapped_key");
		cy.get("@preview").should("not.contain.text", UNMAPPED);

		action_button("Approve").should("be.visible");
		action_button("Discard").should("be.visible");
		// Undo is for Approved rows only.
		action_button("Undo").should("not.exist");
	});

	it("approves a pending row from the form, and Undo puts the record back", () => {
		cy.visit(`/app/crema-proposal/${names[0]}`);
		action_button("Approve").click();

		// A real write, made as the task's Runs As account — not a status flip.
		cy.window().its("cur_frm.doc.status").should("eq", "Approved");
		contacts_named(PROPOSALS[0].who).should("have.length", 1);

		// reload_doc re-runs refresh, so the buttons follow the new status.
		action_button("Approve").should("not.exist");
		action_button("Discard").should("not.exist");

		action_button("Undo").click();
		cy.window().its("cur_frm.doc.status").should("eq", "Pending");
		contacts_named(PROPOSALS[0].who).should("have.length", 0);
		action_button("Undo").should("not.exist");
		action_button("Approve").should("be.visible");
	});

	it("discards a pending row from the form, and writes nothing", () => {
		cy.visit(`/app/crema-proposal/${names[1]}`);
		action_button("Discard").click();

		cy.window().its("cur_frm.doc.status").should("eq", "Discarded");
		contacts_named(PROPOSALS[1].who).should("have.length", 0);
		action_button("Approve").should("not.exist");
		action_button("Discard").should("not.exist");
		// Nothing was written, so there is nothing to reverse.
		action_button("Undo").should("not.exist");
	});

	it("previews every parked write before a bulk approve, and writes only on confirm", () => {
		// Filtered to this file's own task: the assertions below count rows.
		cy.visit(`/app/crema-proposal?task=${TASK}`);
		// .list-row-container also wraps the header row — .list-row matches data rows
		// only (see desk_ui.js's own note on this pair).
		cy.get(".list-row-container .list-row").should("have.length", 2);
		cy.get(".list-row-container .list-row .list-row-checkbox").check();

		// The Actions menu only appears once something is checked;
		// add_actions_menu_item (crema_proposal_list.js) is what puts the three crema
		// entries in it.
		cy.contains("button", "Actions").click();
		// The open menu, not the hidden item store: on frappe develop the Actions
		// menu is an espresso panel (.es-menu__item) and the old ul.dropdown-menu
		// stays in the DOM only as a hidden store; on version-16 the bootstrap
		// menu itself opens (.dropdown-menu.show). Match either, so this spec runs
		// unchanged on both branches.
		cy.contains(".es-menu__item, .dropdown-menu.show .dropdown-item", "Approve").click();

		// Approve is the only one of the three that writes, so it is the only one that
		// asks first — and it shows each parked write, not just a count of rows.
		crema_modal().should("contain.text", "Approve 2 proposals?");
		crema_modal().should("contain.text", "Records to write");
		crema_modal().should("contain.text", "1. Contact");
		crema_modal().should("contain.text", "2. Contact");
		crema_modal().should("contain.text", "first_name");
		crema_modal().should("contain.text", PROPOSALS[0].who);
		crema_modal().should("contain.text", PROPOSALS[1].who);

		// Asking first means nothing is written while the dialog is still up.
		proposal_rows(["status"]).then((rows) => {
			expect(rows.map((row) => row.status)).to.deep.equal(["Pending", "Pending"]);
		});

		// Wait for shown.bs.modal (frappe flips cur_dialog.display there) before
		// clicking: bootstrap ignores modal("hide") while the show transition is
		// still running, so a click inside the fade-in runs onConfirm but leaves
		// the dialog up — and this test then reads a stale list underneath it.
		cy.window().its("cur_dialog.display").should("eq", true);
		crema_modal().find(".btn-modal-primary").should("contain.text", "Approve 2").click();

		cy.get(".list-row-container .list-row").should("contain.text", "Approved");
		proposal_rows(["status"]).then((rows) => {
			expect(rows.map((row) => row.status)).to.deep.equal(["Approved", "Approved"]);
		});
		contacts_named(PROPOSALS[0].who).should("have.length", 1);
		contacts_named(PROPOSALS[1].who).should("have.length", 1);
	});
});
