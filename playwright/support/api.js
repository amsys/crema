import { expect } from "@playwright/test";

export const ADMIN = "Administrator";
export const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "admin";

const enc = encodeURIComponent;

export async function login(request, usr, pwd) {
	const res = await request.post("/api/method/login", { data: { usr, pwd } });
	expect(res.status(), `login as ${usr}: ${await res.text()}`).toBe(200);
	const body = await res.json();
	expect(body.message, `login as ${usr}`).toBeTruthy();
}

export class Api {
	constructor(request, page = null) {
		this.request = request;
		this.page = page;
	}

	async headers() {
		const headers = { Accept: "application/json" };
		if (this.page) {
			const token = await this.page
				.evaluate(() => window.frappe?.csrf_token)
				.catch(() => null);
			if (token && token !== "None") headers["X-Frappe-CSRF-Token"] = token;
		}
		return headers;
	}

	async call(method, args = {}) {
		const res = await this.request.post(`/api/method/${method}`, {
			data: args,
			headers: await this.headers(),
		});
		expect(res.status(), `${method}: ${await res.text()}`).toBe(200);
		return res.json();
	}

	async get_list(doctype, fields = ["name"], filters = [], limit = 0) {
		const res = await this.request.get(`/api/resource/${enc(doctype)}`, {
			params: {
				fields: JSON.stringify(fields),
				filters: JSON.stringify(filters),
				limit_page_length: limit,
			},
			headers: await this.headers(),
		});
		expect(res.status(), `get_list ${doctype}: ${await res.text()}`).toBe(200);
		return (await res.json()).data;
	}

	async insert_doc(doctype, args, ignore_duplicate = false) {
		const res = await this.request.post(`/api/resource/${enc(doctype)}`, {
			data: { doctype, ...args },
			headers: await this.headers(),
		});
		if (ignore_duplicate && res.status() === 409) return null;
		expect(res.status(), `insert ${doctype}: ${await res.text()}`).toBe(200);
		return (await res.json()).data;
	}

	async remove_doc(doctype, name, ignore_missing = false) {
		const res = await this.request.delete(`/api/resource/${enc(doctype)}/${enc(name)}`, {
			headers: await this.headers(),
		});
		if (ignore_missing && res.status() === 404) return;
		expect([200, 202], `remove ${doctype} ${name}: ${await res.text()}`).toContain(
			res.status()
		);
	}

	async set_value(doctype, name, values) {
		return this.call("frappe.client.set_value", { doctype, name, fieldname: values });
	}
}
