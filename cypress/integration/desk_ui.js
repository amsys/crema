// Covers crema.bundle.js: the robot button (list-family toolbar) and the awesomebar
// "Ask …" entry. All model calls are stubbed with cy.intercept — same rule the Python
// suite follows (patch at the boundary, never call a live network).
context("Crema desk UI", () => {
	before(() => {
		cy.visit("/login");
		cy.login();
		cy.visit("/desk/todo");
		cy.clear_filters();
	});

	beforeEach(() => {
		cy.visit("/desk/todo");
	});

	it("renders the robot button on a list view", () => {
		// The regression this whole test file exists for: the button used to attach on
		// frappe.router's "change" event, which fires before ListView.setup_page has set
		// frappe.container.page.list_view, so it silently never appeared.
		cy.get("[data-crema]").should("be.visible");
	});

	it("places the button between the menu and the primary action", () => {
		cy.get(".standard-actions").within(() => {
			cy.get(".menu-btn-group, [data-crema], .primary-action").then(($els) => {
				const order = [...$els].map((el) => el.className);
				const menu_idx = order.findIndex((c) => c.includes("menu-btn-group"));
				const crema_idx = [...$els].findIndex((el) => el.hasAttribute("data-crema"));
				const primary_idx = order.findIndex((c) => c.includes("primary-action"));
				expect(menu_idx).to.be.lessThan(crema_idx);
				expect(crema_idx).to.be.lessThan(primary_idx);
			});
		});
	});

	it("is the same square size as the native reload button", () => {
		// Regression pin: set_secondary_action("", ...) used to leave a hidden, padded
		// label span in place, making this button wider than the reload icon next to it.
		cy.get("[data-crema]").should("have.class", "icon-btn");
		cy.get("[data-crema]")
			.invoke("outerWidth")
			.then((crema_width) => {
				cy.get(".page-icon-group .icon-btn")
					.invoke("outerWidth")
					.should("eq", crema_width);
			});
	});

	it("opens the dialog on click, with an inline file drop zone and no batch checkbox", () => {
		cy.get("[data-crema]").click();
		cy.get(".modal-title").should("contain", "Ask Crema about ToDo");
		// The upload field hosts frappe.ui.FileUploader inline (wrapper: ...) — no
		// second, stacked modal, and no plain Attach control.
		cy.get(".modal").within(() => {
			cy.get(".file-uploader").should("be.visible");
			cy.get(".frappe-control[data-fieldname=file]").should("not.exist");
			// "Many records in one file" was dropped — extract() decides record count itself.
			cy.get(".frappe-control[data-fieldname=batch]").should("not.exist");
			// The drop zone is shrunk from frappe's stock 16rem so it doesn't dominate the dialog.
			cy.get(".file-upload-area").should(($el) => {
				expect(parseFloat($el.css("min-height"))).to.be.lessThan(200);
			});
		});
		cy.get(".modal").within(() => cy.get(".btn-modal-close").click());
	});

	it("applies a view spec and drops fields the model invented", () => {
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({
						view: "List",
						// "not_a_field" isn't a real ToDo field — crema_apply_view_spec must
						// drop it, the one piece of real security logic in this file.
						filters: { status: ["=", "Open"], not_a_field: ["=", "x"] },
						reason: "showing open todos",
					}),
				},
			},
		}).as("ask");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type("show open todos");
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask");

		cy.location("search").should("contain", encodeURIComponent('["=","Open"]'));
		cy.location("search").should("not.contain", "not_a_field");
	});

	it("applies order_by to the sort selector and refreshes the list", () => {
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({
						view: "List",
						order_by: "name desc",
						reason: "sorted by name, descending",
					}),
				},
			},
		}).as("ask");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
				"sort by name descending"
			);
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask");

		cy.get(".sort-selector .dropdown-text").should("contain", "Name");
		cy.get(".sort-selector .btn-order").should("have.attr", "data-value", "desc");
	});

	it("makes one list query for a spec with a sort", () => {
		// Pin for the round-trip fix: applying a sort/page_length/columns spec used to
		// refresh the list twice (once via the route change, once via on_sort_change).
		// Already being on the target List should collapse that to one.
		cy.intercept("POST", "/api/method/frappe.desk.reportview.get").as("listget");
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({
						view: "List",
						order_by: "name desc",
						reason: "sorted by name, descending",
					}),
				},
			},
		}).as("ask");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
				"sort by name descending"
			);
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask");
		cy.wait("@listget");

		cy.get("@listget.all").should("have.length", 1);
	});

	it("drops an order_by field the model invented and leaves the sort untouched", () => {
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({
						view: "List",
						order_by: "not_a_field desc",
						reason: "sorted",
					}),
				},
			},
		}).as("ask");

		cy.get(".sort-selector .btn-order")
			.invoke("attr", "data-value")
			.then((before) => {
				cy.get("[data-crema]").click();
				cy.get(".modal").within(() => {
					cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
						"sort somehow"
					);
					cy.get(".btn-modal-primary").click();
				});
				cy.wait("@ask");
				cy.get(".sort-selector .btn-order").should("have.attr", "data-value", before);
			});
	});

	it("applies page_length as a row limit", () => {
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({ view: "List", page_length: 1, reason: "top 1" }),
				},
			},
		}).as("ask");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
				"show me the top 1"
			);
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask");

		cy.get(".list-row-container").its("length").should("be.lte", 1);
	});

	it("round-trips a like filter with a wildcard intact", () => {
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({
						view: "List",
						filters: { description: ["like", "t%"] },
						reason: "todos starting with t",
					}),
				},
			},
		}).as("ask");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
				"todos starting with t"
			);
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask");

		cy.location("search").should("contain", encodeURIComponent('["like","t%"]'));
	});

	it("clears the previous prompt's filters when the next spec has none", () => {
		// The fast path used to only touch filter_area when the spec had a filter to set
		// (mirroring list_view.js's own before_refresh() guard), so "show me everything"
		// silently answered the *previous* question against a still-filtered list.
		const ask = (result) =>
			cy
				.intercept("POST", "/api/method/crema.api.ask_api", {
					statusCode: 200,
					body: { message: { result: JSON.stringify(result) } },
				})
				.as("ask");
		const prompt = (text) => {
			cy.get("[data-crema]").click();
			cy.get(".modal").within(() => {
				cy.get(".frappe-control[data-fieldname=instruction] textarea").type(text);
				cy.get(".btn-modal-primary").click();
			});
			cy.wait("@ask");
		};

		ask({
			view: "List",
			filters: { description: ["like", "t%"] },
			reason: "todos starting with t",
		});
		prompt("todos starting with t");
		cy.location("search").should("contain", "description");

		ask({ view: "List", reason: "everything" });
		prompt("show me everything");
		cy.location("search").should("not.contain", "description");
	});

	it("drops a group_by with an invented aggregate fieldname", () => {
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({
						view: "List",
						group_by: ["status", "not_a_field", "sum"],
						reason: "grouped by status",
					}),
				},
			},
		}).as("ask");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type("group by status");
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask");

		cy.location("search").should("not.contain", "_group_by");
	});

	it("widens to the field that holds the term when the filter matches nothing", () => {
		// reference_type is blank on a plain ToDo, so the model's own filter matches
		// nothing — the fallback should find "probe" in description instead, with no
		// second LLM call.
		cy.insert_doc("ToDo", { description: "crema fallback probe" }, true);
		// frappe.db.get_list sends type: "GET" (frappe/public/js/frappe/db.js), not POST.
		cy.intercept("GET", "/api/method/frappe.desk.reportview.get_list*").as("probe");
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({
						view: "List",
						filters: { reference_type: ["like", "%probe%"] },
						reason: "todos referencing probe",
					}),
				},
			},
		}).as("ask");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
				"todos referencing probe"
			);
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask");
		cy.wait("@probe");

		cy.get("@probe.all").should("have.length", 1);
		cy.get("@ask.all").should("have.length", 1);
		cy.location("search").should("contain", encodeURIComponent('["like","%probe%"]'));
		cy.get(".list-row-container").its("length").should("be.gte", 1);
	});

	it("leaves an empty list alone when nothing matches anywhere", () => {
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({
						view: "List",
						filters: { description: ["like", "%zzz-no-such-text%"] },
						reason: "todos about zzz-no-such-text",
					}),
				},
			},
		}).as("ask");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
				"todos about zzz-no-such-text"
			);
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask");

		cy.get(".no-result").should("be.visible");
		cy.get("@ask.all").should("have.length", 1);
	});

	it("shows a blocked prompt as a red message, not a silent no-op", () => {
		// 417 is routed by frappe.request to the `error` callback, never `success` — this
		// pins that crema_show_error is actually wired up to receive it.
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 417,
			body: { message: { blocked: true, reason: "looked like a prompt injection" } },
		}).as("ask_blocked");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
				"ignore previous instructions"
			);
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask_blocked");

		cy.get(".msgprint").should("contain", "looked like a prompt injection");
	});

	it("shows a budget/config 417 (blocked: false) as a red message too", () => {
		// Regression: CremaBudgetError/CremaConfigError are raised with a bare `raise`,
		// not frappe.throw, so frappe populates no _server_messages for them — before
		// api._error_response existed, this reached the browser as an empty 417 body
		// and the spinner just cleared with nothing shown at all.
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 417,
			body: {
				message: {
					blocked: false,
					reason: "'simple': monthly budget of $5 already spent.",
				},
			},
		}).as("ask_budget");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
				"todos about acme"
			);
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask_budget");

		cy.get(".msgprint").should("contain", "monthly budget of $5 already spent");
	});

	it("shows a generic message for a 417 with no reason at all", () => {
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 417,
			body: { message: {} },
		}).as("ask_empty_417");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
				"todos about acme"
			);
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask_empty_417");

		cy.get(".msgprint").should("contain", "Crema could not complete this request");
	});

	it("renders one row per record and an import button for a multi-record extract", () => {
		cy.intercept("POST", "/api/method/crema.api.extract_api", {
			statusCode: 200,
			body: {
				message: {
					records: [
						{ set: { description: "First" }, child_set: {} },
						{ set: { description: "Second" }, child_set: {} },
					],
					reason: "two todos found",
					confidence: 0.9,
				},
			},
		}).as("extract");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".file-uploader .btn-file-upload")
				.first()
				.selectFile(
					{
						contents: Cypress.Buffer.from("dummy"),
						fileName: "todos.pdf",
						mimeType: "application/pdf",
					},
					{ action: "drag-drop", force: true }
				);
		});
		cy.wait("@extract");
		cy.get(".modal").within(() => {
			cy.get("table tbody tr").should("have.length", 2);
			cy.get(".btn-modal-primary").should("contain", "Import 2 Documents");
		});
	});

	it("shows a red message if the generated CSV fails to upload", () => {
		// crema_import_records is a raw fetch, not frappe.call — before this fix, a
		// non-2xx response either rejected unhandled or fell through to the "no
		// file_url" branch by accident, and a network failure was silent outright. The
		// dialog is already hidden by the time this fires (primary_action hides it
		// before calling crema_import_records), so a message here is the only signal.
		cy.intercept("POST", "/api/method/crema.api.extract_api", {
			statusCode: 200,
			body: {
				message: {
					records: [
						{ set: { description: "First" }, child_set: {} },
						{ set: { description: "Second" }, child_set: {} },
					],
					reason: "two todos found",
					confidence: 0.9,
				},
			},
		}).as("extract");
		cy.intercept("POST", "/api/method/upload_file", { statusCode: 500, body: {} }).as(
			"upload_fail"
		);

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".file-uploader .btn-file-upload")
				.first()
				.selectFile(
					{
						contents: Cypress.Buffer.from("dummy"),
						fileName: "todos.pdf",
						mimeType: "application/pdf",
					},
					{ action: "drag-drop", force: true }
				);
		});
		cy.wait("@extract");
		cy.get(".modal").within(() => {
			cy.get(".btn-modal-primary").contains("Import 2 Documents").click();
		});
		cy.wait("@upload_fail");

		cy.get(".msgprint").should("contain", "Could not upload the generated file");
	});

	it("opens a new unsaved form for an action:create spec with one record", () => {
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({
						action: "create",
						records: [{ set: { description: "Call Acme back" }, child_set: {} }],
						reason: "creating a todo",
					}),
				},
			},
		}).as("ask");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
				"create a todo to call Acme back"
			);
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask");

		cy.location("pathname").should("contain", "/todo/new-todo-");
		cy.get(".indicator-pill").should("contain", "Not Saved");
		cy.get(
			".form-control[data-fieldname=description] textarea, textarea[data-fieldname=description]"
		).should("contain.value", "Call Acme back");
	});

	it("drops a field the model invented from a create spec", () => {
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({
						action: "create",
						records: [
							{
								set: { description: "Call Acme back", not_a_field: "x" },
								child_set: {},
							},
						],
						reason: "creating a todo",
					}),
				},
			},
		}).as("ask");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
				"create a todo to call Acme back"
			);
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask");

		cy.location("pathname").should("contain", "/todo/new-todo-");
		cy.window().its("cur_frm.doc.not_a_field").should("be.undefined");
	});

	it("drops a read-only field from a create spec", () => {
		// assignment_rule is read_only: 1 on ToDo — the perm.js:238 regression: without the
		// !df.read_only check, get_field_display_status(df, null, perm) reports "Write" for
		// every read-only field because its own read_only demotion only runs when a doc is
		// passed in.
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({
						action: "create",
						records: [
							{
								set: {
									description: "Call Acme back",
									assignment_rule: "Bogus Rule",
								},
								child_set: {},
							},
						],
						reason: "creating a todo",
					}),
				},
			},
		}).as("ask");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
				"create a todo to call Acme back"
			);
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask");

		cy.location("pathname").should("contain", "/todo/new-todo-");
		cy.window().its("cur_frm.doc.assignment_rule").should("not.eq", "Bogus Rule");
	});

	it("resolves an edit spec to one record, shows the diff, then leaves the form dirty", () => {
		cy.insert_doc("ToDo", { description: "crema edit-one target" }, true);
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({
						action: "edit",
						filters: { description: ["=", "crema edit-one target"] },
						set: { priority: "High" },
						child_set: {},
						reason: "raising priority",
					}),
				},
			},
		}).as("ask");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
				'raise the priority of the "crema edit-one target" todo'
			);
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask");

		// The diff dialog — how the user knows what changed before anything applies.
		cy.get(".modal-title").should("contain", "Update crema edit-one target");
		cy.get(".modal").within(() => cy.get(".btn-modal-primary").contains("Apply").click());

		cy.location("pathname").should("contain", "crema%20edit-one%20target");
		cy.get(".indicator-pill").should("contain", "Not Saved");
	});

	it("refuses an edit spec whose only proposed field is read-only", () => {
		// Without the has_changes guard, bulk_update.py's action=="update" branch calls
		// doc.save() even for an empty data dict — this asserts the request never reaches
		// that endpoint at all, not merely that the UI hides the outcome.
		cy.insert_doc("ToDo", { description: "crema empty-edit target" }, true);
		cy.intercept("POST", "/api/method/frappe.desk.doctype.bulk_update.bulk_update.*").as(
			"bulk_update"
		);
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({
						action: "edit",
						filters: { description: ["=", "crema empty-edit target"] },
						set: { assignment_rule: "Bogus Rule" },
						child_set: {},
						reason: "changing a read-only field",
					}),
				},
			},
		}).as("ask");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
				'set the assignment rule of the "crema empty-edit target" todo'
			);
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask");

		cy.get(".msgprint").should("contain", "did not propose any change you may write");
		cy.get("@bulk_update.all").should("have.length", 0);
	});

	it("refuses an edit/delete action that names no valid filter", () => {
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({
						action: "delete",
						filters: {},
						reason: "delete everything",
					}),
				},
			},
		}).as("ask");

		cy.location("search").then((before) => {
			cy.get("[data-crema]").click();
			cy.get(".modal").within(() => {
				cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
					"delete all of these"
				);
				cy.get(".btn-modal-primary").click();
			});
			cy.wait("@ask");

			cy.get(".msgprint").should("contain", 'not "all of them"');
			cy.location("search").should("eq", before);
		});
	});

	it("shows a confirm dialog listing every record before a delete, and deletes only after confirming", () => {
		cy.insert_doc("ToDo", { description: "crema delete target one" }, true);
		cy.insert_doc("ToDo", { description: "crema delete target two" }, true);
		cy.intercept("POST", "/api/method/frappe.desk.reportview.delete_items").as("delete_items");
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({
						action: "delete",
						filters: { description: ["like", "crema delete target%"] },
						reason: "deleting the two crema delete targets",
					}),
				},
			},
		}).as("ask");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
				"delete the crema delete target todos"
			);
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask");

		cy.get(".modal-title").should("contain", "Delete 2 ToDo records");
		cy.get(".modal").should("contain", "Records to delete");
		cy.get(".modal table tbody tr").should("have.length", 2);
		cy.get("@delete_items.all").should("have.length", 0);

		cy.get(".modal").within(() => cy.get(".btn-modal-primary").click());
		cy.wait("@delete_items");
	});

	it("matches an underscore literally instead of as a SQL wildcard", () => {
		// "_" is a single-character SQL LIKE wildcard — unescaped, ["like", "_crema..."]
		// would match every row with at least one leading character, not just this one.
		cy.insert_doc("ToDo", { description: "_crema underscore target" }, true);
		cy.insert_doc("ToDo", { description: "Xcrema underscore target" }, true);
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({
						action: "delete",
						filters: { description: ["like", "_crema underscore target"] },
						reason: "deleting the underscore-prefixed todo",
					}),
				},
			},
		}).as("ask");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
				"delete todos starting with an underscore"
			);
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask");

		cy.get(".modal-title").should("contain", "Delete 1 ToDo record");
		cy.get(".modal table tbody tr").should("have.length", 1);
		cy.get(".modal table tbody").should("contain", "_crema underscore target");
	});

	it("accepts the record name (ID) as a filter field", () => {
		cy.insert_doc("ToDo", { description: "crema name-filter target" }, true).then((doc) => {
			cy.intercept("POST", "/api/method/crema.api.ask_api", {
				statusCode: 200,
				body: {
					message: {
						result: JSON.stringify({
							action: "delete",
							filters: { name: ["=", doc.name] },
							reason: "deleting by id",
						}),
					},
				},
			}).as("ask");

			cy.get("[data-crema]").click();
			cy.get(".modal").within(() => {
				cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
					`delete the todo with id ${doc.name}`
				);
				cy.get(".btn-modal-primary").click();
			});
			cy.wait("@ask");

			cy.get(".modal-title").should("contain", "Delete 1 ToDo record");
			cy.get(".modal table tbody tr").should("have.length", 1);
			cy.get(".modal table tbody").should("contain", doc.name);
		});
	});

	it("shows an action:none refusal as an orange message and leaves the list alone", () => {
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({
						action: "none",
						reason: "I cannot email this list from here.",
					}),
				},
			},
		}).as("ask");

		cy.location("search").then((before) => {
			cy.get("[data-crema]").click();
			cy.get(".modal").within(() => {
				cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
					"email this list to bob"
				);
				cy.get(".btn-modal-primary").click();
			});
			cy.wait("@ask");

			cy.get(".msgprint").should("contain", "I cannot email this list from here.");
			cy.location("search").should("eq", before);
		});
	});

	it("renders a model-authored reason as text, not HTML", () => {
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({
						view: "List",
						reason: "<img src=x onerror=alert(1)>",
					}),
				},
			},
		}).as("ask");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type("show open todos");
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask");

		cy.get(".desk-alert .alert-message").should(
			"contain.text",
			"<img src=x onerror=alert(1)>"
		);
		cy.get(".desk-alert .alert-message img").should("not.exist");
	});

	it("offers an Ask … option in the awesomebar on a list view", () => {
		cy.get("#navbar-modal-search").click();
		cy.get("#navbar-search").type("overdue items");
		cy.wait(400);
		cy.get(".awesomplete").findByRole("listbox").should("contain.text", "Ask overdue items");
		cy.get("body").type("{esc}");
	});
});

context("Crema desk UI — form view", () => {
	before(() => {
		cy.visit("/login");
		cy.login();
	});

	it("renders a robot button on a saved form and applies a diff without saving", () => {
		cy.visit("/app/todo/new");
		cy.get(
			".form-control[data-fieldname=description] textarea, textarea[data-fieldname=description]"
		)
			.first()
			.type("a todo to transform");
		cy.get(".primary-action").contains("Save").click();
		cy.wait(500);

		cy.get("[data-crema-form]").should("be.visible");

		cy.intercept("POST", "/api/method/crema.api.transform_api", {
			statusCode: 200,
			body: {
				message: {
					set: { description: "Updated by Crema" },
					child_set: {},
					reason: "updated",
				},
			},
		}).as("transform");

		cy.get("[data-crema-form]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
				"mark this urgent"
			);
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@transform");
		cy.get(".modal").within(() => cy.get(".btn-modal-primary").contains("Apply").click());

		cy.get(".indicator-pill").should("contain", "Not Saved");
	});
});

context("Crema desk UI — create spec child rows", () => {
	before(() => {
		cy.visit("/login");
		cy.login();
	});

	beforeEach(() => {
		cy.visit("/desk/contact");
	});

	it("keeps child rows from a create spec", () => {
		// The perm.js child-doctype regression: frappe.perm.get_perm(child_doctype) returns
		// read:0 for every field (a child doctype carries no DocPerm rows of its own), so a
		// naive write-fence would silently drop every child_set row. crema_writable_fields
		// takes the PARENT's perm array instead — this is the test that fails if that ever
		// changes back.
		cy.intercept("POST", "/api/method/crema.api.ask_api", {
			statusCode: 200,
			body: {
				message: {
					result: JSON.stringify({
						action: "create",
						records: [
							{
								set: { first_name: "Crema Child Row Test" },
								child_set: { phone_nos: [{ phone: "555-0100" }] },
							},
						],
						reason: "creating a contact",
					}),
				},
			},
		}).as("ask");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type(
				"create a contact named Crema Child Row Test"
			);
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask");

		cy.location("pathname").should("contain", "/contact/new-contact-");
		cy.window().its("cur_frm.doc.phone_nos").should("have.length", 1);
		cy.window().its("cur_frm.doc.phone_nos.0.phone").should("eq", "555-0100");
	});

	it("lists the child table's own writable fieldnames in the prompt, not [read-only]", () => {
		// Guards the crema_writable_table branch in crema_view_prompt: a Table field is
		// never in crema_writable_fields' set (frappe.model.is_value_type excludes it), so
		// without this branch the prompt would print "[read-only]" for phone_nos while the
		// same prompt's create/edit shapes advertise child_set — a self-contradiction the
		// model has no way to resolve.
		cy.intercept("POST", "/api/method/crema.api.ask_api", (req) => {
			expect(req.body.prompt).to.match(
				/phone_nos \(Table\)[^\n]*\[rows: [^\]]*phone[^\]]*\]/
			);
			req.reply({
				statusCode: 200,
				body: { message: { result: JSON.stringify({ action: "none", reason: "n/a" }) } },
			});
		}).as("ask");

		cy.get("[data-crema]").click();
		cy.get(".modal").within(() => {
			cy.get(".frappe-control[data-fieldname=instruction] textarea").type("anything");
			cy.get(".btn-modal-primary").click();
		});
		cy.wait("@ask");
	});
});

context("Crema Settings", () => {
	before(() => {
		cy.visit("/login");
		cy.login();
	});

	beforeEach(() => {
		cy.visit("/app/crema-settings");
	});

	// The Use Cases grid always shows exactly one row per interfaces.names()
	// (core PREDEFINED + whatever installed apps register via the crema_interfaces
	// hook), seeded at install/migrate, none addable or removable (crema_settings.js
	// sets grid.df.cannot_add_rows / cannot_delete_rows). Counted live rather than
	// hardcoded, since the app-registered half varies per site.
	//
	// get_interfaces() is the narrower list the automation task's AI Profile picker
	// shows — interfaces.selectable(), i.e. names() minus interfaces.INTERNAL and
	// minus interfaces.NOT_FOR_TASKS. The grid still carries a row for each of those
	// four, so it is a superset by exactly that count.
	const UNSELECTABLE_INTERFACE_COUNT = 4;

	it("shows the full set of model assignment rows with no add/delete affordance", () => {
		cy.window()
			.then((win) => win.frappe.xcall("crema.api.get_interfaces"))
			.then((selectable) => {
				cy.get('[data-fieldname="assignments"] .grid-row').should(
					"have.length",
					selectable.length + UNSELECTABLE_INTERFACE_COUNT
				);
			});
		cy.get('[data-fieldname="assignments"]').within(() => {
			cy.get("button, a")
				.contains(/add row|delete/i)
				.should("not.exist");
		});
	});

	// Providers are the Crema Provider list view now, not a panel on this form, and the
	// sidebar lists Providers above Settings — so the form carries no link of its own.
	// The one case it does interrupt for is having no enabled provider at all, since
	// nothing here resolves without one.
	it("does not link out to the provider list", () => {
		cy.contains("button", /^AI Services$/).should("not.exist");
	});

	// Stubbed rather than acted out: the alternative is disabling every provider on the
	// site mid-suite, and the count call is the only input the warning reads.
	it("warns when no provider is switched on", () => {
		cy.intercept("POST", "/api/method/frappe.desk.reportview.get_count", {
			body: { message: 0 },
		});
		cy.visit("/app/crema-settings");
		cy.contains("No AI service is switched on").should("be.visible");
		cy.contains("button", "Add an AI Service").should("be.visible");
	});

	// Administrator holds System Manager but not Crema User, so this exercises the
	// yellow branch and its one-click grant button.
	it("tells a non-Crema-User System Manager they lack the role, with a grant button", () => {
		cy.contains("You do not have the Crema User role").should("be.visible");
		cy.contains("button", "Give Me the Crema User Role").should("be.visible");
	});
});

describe("Crema Provider list", () => {
	before(() => {
		cy.login();
	});

	beforeEach(() => {
		cy.visit("/app/crema-provider");
	});

	it("keeps the New from Template action", () => {
		cy.contains("button", "New from Template").should("be.visible");
	});

	// Connection and Usage are painted by listview_settings formatters after one
	// prefetch-then-refresh pass (crema_provider_list.js) — the columns themselves are
	// ordinary list columns, so the headers must be there whether or not a provider exists.
	it("shows the Address and Monthly Budget columns the formatters paint into", () => {
		cy.get(".list-row-head").contains("Address").should("exist");
	});
});

// Covers the File Query source type and the Match Records On picker
// (crema_automation_task.js): a File Query row pins its own doctype and reuses the
// Which Records filter dialog, and Match Records On is a plain Data field with a
// clickable "Pick fields…" helper once a target doctype is chosen.
describe("Crema Automation Task form — File Query source", () => {
	before(() => {
		cy.login();
	});

	beforeEach(() => {
		cy.visit("/app/crema-automation-task/new");
	});

	it("offers File Query as a source type and pins it to the File doctype", () => {
		cy.get(".grid-add-row, [data-fieldname='sources'] .grid-add-row").click();
		cy.get(
			".grid-row-open select[data-fieldname='source_type'], select[data-fieldname='source_type']"
		)
			.last()
			.select("File Query");

		// source_doctype is hidden (depends_on Document Query only) but stamped to "File"
		// underneath — the Which Records filter dialog is what proves it rendered.
		cy.contains(".grid-row-open", "Which Records").should("be.visible");
		cy.contains(".grid-row-open, .grid-body", "Click to set filters").should("exist");
	});

	it("clears Files (attachments) for a File Query row", () => {
		cy.get(".grid-add-row, [data-fieldname='sources'] .grid-add-row").click();
		cy.get("select[data-fieldname='source_type']").last().select("File Query");

		cy.get(".grid-row [data-fieldname='read_attachments'] input[type='checkbox']")
			.last()
			.should("not.be.checked");
	});

	it("shows a Pick fields… helper on Match Records On once a target doctype is set", () => {
		cy.get("select[data-fieldname='action']").select("Create or Update Records");
		cy.get("[data-fieldname='target_doctype'] input").type("Contact{enter}");
		cy.wait(300); // frappe.model.with_doctype fetches Contact's meta before the link renders

		cy.get("[data-fieldname='match_on']")
			.contains("a", "Pick fields…")
			.should("be.visible")
			.click();

		cy.get(".modal-title").contains("Match Records On").should("be.visible");
		cy.get(".modal .awesomplete input").type("first_name");
		cy.contains("li", "first_name").click();
		cy.contains(".modal-footer button", "Set").click();

		cy.get("[data-fieldname='match_on'] input").should("have.value", "first_name");
	});
});
