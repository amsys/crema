// Reuse frappe's cy.login / cy.insert_doc / cy.new_form etc. Node resolution walks up from
// the imported file, so its own package imports (@testing-library/cypress, etc.) resolve out
// of apps/frappe/node_modules — the same node_modules bench run-ui-tests installs cypress into.
import "../../../frappe/cypress/support/commands";

Cypress.on("uncaught:exception", () => false);

// frappe's own cy.login (apps/frappe/cypress/support/commands.js) calls cy.session()
// with no `validate`, so a session the server has since dropped (CI ran Administrator
// through this login POST dozens of times across three spec files, and
// simultaneous_sessions defaults to 1 — frappe/sessions.py's clear_sessions logs out
// every older session on each new login) gets restored as a dead cookie: cy.session
// only re-runs the setup callback when validate fails or the cache is empty. Every
// should("not.exist") assertion then passes vacuously against the login page instead of
// failing loudly. `validate` re-checks against a whitelisted, non-guest endpoint on every
// restore, so a dead session is repaired before the test body runs.
let logged_in_as = null;
Cypress.Commands.overwrite("login", (login, email, password) => {
	if (!email) email = Cypress.config("testUser") || "Administrator";
	if (!password) password = Cypress.env("adminPassword");
	logged_in_as = [email, password];

	const session_last_route = window.localStorage.getItem("session_last_route");
	return cy
		.session(
			[email, password],
			() => {
				return cy.request({
					url: "/api/method/login",
					method: "POST",
					body: { usr: email, pwd: password },
				});
			},
			{
				validate: () =>
					cy
						.request({
							url: "/api/method/frappe.auth.get_logged_user",
							failOnStatusCode: false,
						})
						.its("body.message")
						.should("eq", email),
			}
		)
		.then(() => {
			if (session_last_route) {
				window.localStorage.setItem("session_last_route", session_last_route);
			}
		});
});

// Nothing in this suite may reach a real model. A spec's own cy.intercept is
// registered after this one and so wins; anything left over lands here and fails
// loudly rather than billing whatever provider the site happens to have configured.
const BILLABLE_METHODS = [
	"ask_api",
	"extract_api",
	"transform_api",
	"dry_run_automation",
	"run_automation_now",
];

beforeEach(() => {
	// A spec's own before()/beforeEach() only calls cy.login() once per context, so a
	// session dropped mid-context (see above) would otherwise not be caught until the
	// next context's own login. Re-probe before every test and only pay for a real
	// cy.login() (which cy.session then usually serves from cache anyway) on a mismatch.
	if (logged_in_as) {
		cy.request({ url: "/api/method/frappe.auth.get_logged_user", failOnStatusCode: false })
			.its("body.message")
			.then((user) => {
				if (user !== logged_in_as[0]) cy.login(...logged_in_as);
			});
	}

	for (const method of BILLABLE_METHODS) {
		cy.intercept("POST", `/api/method/crema.api.${method}`, {
			statusCode: 599,
			body: { exc_type: "CremaUnstubbedCallError", _server_messages: JSON.stringify([]) },
		});
	}
});
