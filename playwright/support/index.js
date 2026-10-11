import { test as base, expect } from "@playwright/test";
import { ADMIN, ADMIN_PASSWORD, Api, login } from "./api";

// Calls that reach a model provider and cost money.
export const BILLABLE_METHODS = [
	"ask_api",
	"extract_api",
	"extract_async",
	"transform_api",
	"ocr_api",
	"dry_run_automation",
	"run_automation_now",
];

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
				`/api/method/crema\\.api\\.(${BILLABLE_METHODS.join("|")})(\\?|$)`
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
