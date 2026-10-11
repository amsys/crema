import { expect } from "@playwright/test";
import { ADMIN, login } from "./api";

const PROBE_EVENT = "crema_room_probe";

export async function goto_desk(page, path, user = ADMIN) {
	await page.goto(path);
	await page.waitForFunction((u) => window.frappe?.session?.user === u, user);
}

export async function clear_filters(page) {
	// saved filters are applied before the first refresh, so let that one render first
	await expect(
		page.locator(".list-paging-area:visible, .no-result:visible").first()
	).toBeVisible();
	if (!(await page.evaluate(() => cur_list.filter_area.get().length))) return;
	const filtered_data = await page.evaluateHandle(() => cur_list.data);
	await page.locator(".filter-x-button").dispatchEvent("click");
	await page.waitForFunction((data) => cur_list.data !== data, filtered_data);
}

// The server joins a room only after an async permission check and never acknowledges it,
// so publish to the room until an event comes back through it.
export async function user_room_joined(page, api, user = ADMIN) {
	await page.evaluate((event) => {
		window.realtime_room_joined = false;
		frappe.realtime.on(event, () => {
			window.realtime_room_joined = true;
		});
	}, PROBE_EVENT);
	await expect(async () => {
		await api.call("frappe.tests.ui_test_helpers.publish_realtime", {
			event: PROBE_EVENT,
			user,
		});
		await expect
			.poll(() => page.evaluate(() => window.realtime_room_joined), { timeout: 1000 })
			.toBe(true);
	}).toPass({ timeout: 20000 });
}

export async function drop_file(target, name, bytes, mime_type) {
	const data_transfer = await target.page().evaluateHandle(
		([content, file_name, type]) => {
			const dt = new DataTransfer();
			const data = Uint8Array.from(atob(content), (char) => char.charCodeAt(0));
			dt.items.add(new File([data], file_name, { type }));
			return dt;
		},
		[Buffer.from(bytes).toString("base64"), name, mime_type]
	);
	await target.dispatchEvent("drop", { dataTransfer: data_transfer });
}

export async function dialog_shown(page) {
	await expect.poll(() => page.evaluate(() => window.cur_dialog?.display)).toBe(true);
}

export function count_requests(page, predicate) {
	const requests = [];
	page.on("request", (request) => {
		if (predicate(request)) requests.push(request);
	});
	return requests;
}

export async function login_as(page, usr, pwd) {
	await login(page.request, usr, pwd);
}
