// Reuse frappe's cy.login / cy.insert_doc / cy.new_form etc. Node resolution walks up from
// the imported file, so its own package imports (@testing-library/cypress, etc.) resolve out
// of apps/frappe/node_modules — the same node_modules bench run-ui-tests installs cypress into.
import "../../../frappe/cypress/support/commands";

Cypress.on("uncaught:exception", () => false);

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
	for (const method of BILLABLE_METHODS) {
		cy.intercept("POST", `/api/method/crema.api.${method}`, {
			statusCode: 599,
			body: { exc_type: "CremaUnstubbedCallError", _server_messages: JSON.stringify([]) },
		});
	}
});
