// Reuse frappe's cy.login / cy.insert_doc / cy.new_form etc. Node resolution walks up from
// the imported file, so its own package imports (@testing-library/cypress, etc.) resolve out
// of apps/frappe/node_modules — the same node_modules bench run-ui-tests installs cypress into.
import "../../../frappe/cypress/support/commands";

Cypress.on("uncaught:exception", () => false);
