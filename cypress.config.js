// bench run-ui-tests runs frappe's cypress binary with cwd = apps/crema (this repo), so this
// is the project root. Plain object, not defineConfig() — `cypress` itself only resolves
// from apps/frappe/node_modules, not from here. No baseUrl here on purpose: run-ui-tests
// always exports CYPRESS_baseUrl from the site it was given, overriding anything set here.
module.exports = {
	e2e: {
		specPattern: "cypress/integration/*.js",
		supportFile: "cypress/support/e2e.js",
		testIsolation: false,
		defaultCommandTimeout: 20000,
		video: false,
	},
	viewportWidth: 1400,
	viewportHeight: 960,
};
