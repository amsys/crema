// bench run-ui-tests runs frappe's cypress binary with cwd = apps/crema (this repo), so this
// is the project root. Plain object, not defineConfig() — `cypress` itself only resolves
// from apps/frappe/node_modules, not from here.
module.exports = {
	e2e: {
		baseUrl: "http://fcr.local:8000",
		specPattern: "cypress/integration/*.js",
		supportFile: "cypress/support/e2e.js",
		testIsolation: false,
		defaultCommandTimeout: 20000,
		video: false,
	},
	viewportWidth: 1400,
	viewportHeight: 960,
};
