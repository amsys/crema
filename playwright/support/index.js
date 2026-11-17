import { test as base, expect } from "@playwright/test";
import { ADMIN, ADMIN_PASSWORD, Api, login } from "./api";

// Calls that reach a model provider and cost money.
export const BILLABLE_METHODS = [
	"crema.api.ask_api",
	"crema.api.extract_api",
	"crema.api.extract_async",
	"crema.api.transform_api",
	"crema.api.ocr_api",
	"crema.api.dry_run_automation",
	"crema.api.run_automation_now",
	// The Translate Built-in List action on the Crema Health Word list asks a model too.
	"crema.crema.doctype.crema_health_word.crema_health_word.translate_words",
];

const escape_regex = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const site_url = () => process.env.BASE_URL || "http://localhost:8000";

export const test = base.extend({
	storageState: async ({ playwright, baseURL }, use) => {
		const request = await playwright.request.newContext({ baseURL });
		await login(request, ADMIN, ADMIN_PASSWORD);
		const state = await request.storageState();
		await request.dispose();
		await use(state);
	},
	api: async ({ page }, use) => {
		await use(new Api(page.request, page));
	},
	admin: [
		async ({ playwright }, use) => {
			const request = await playwright.request.newContext({ baseURL: site_url() });
			await login(request, ADMIN, ADMIN_PASSWORD);
			await use(new Api(request));
			await request.dispose();
		},
		{ scope: "worker" },
	],
	// Answers every billable call no spec stubbed with status 599 and fails the test on a hit.
	backstop: [
		async ({ context }, use) => {
			const hits = [];
			const pattern = new RegExp(
				`/api/method/(${BILLABLE_METHODS.map(escape_regex).join("|")})(\\?|$)`
			);
			await context.route(pattern, (route) => {
				hits.push(route.request().url());
				return route.fulfill({
					status: 599,
					json: { exc_type: "CremaUnstubbedCallError", _server_messages: "[]" },
				});
			});
			const backstop = { hits, allowed: 0 };
			await use(backstop);
			expect(hits.length, `unstubbed billable calls: ${hits.join(", ")}`).toBe(
				backstop.allowed
			);
		},
		{ auto: true },
	],
});

export { expect, Api, ADMIN, ADMIN_PASSWORD, login };
export * from "./desk";
