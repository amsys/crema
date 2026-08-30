# Changes for a consuming app

This page is for an app that calls crema from Python (`from crema import ask`, ...) or
registers interfaces through `crema_interfaces` in its own `hooks.py`. It lists a change
that can break such an app. It does not list an addition — a new interface, a new field,
a new HTTP endpoint — because an addition does not break anything that already works.

The contract this page tracks:

- the functions `crema/__init__.py` exports (`ask`, `ask_json`, `ocr`, `extract`,
  `transform`, `transcribe`, `health`, `configure`, `is_configured`, `seed_provider`)
  and their signatures and return shapes;
- the `crema_interfaces`, `crema_guardrails`, and `crema_scan_patterns` `hooks.py` keys,
  and the shape each expects;
- the `Crema Model Assignment` fieldnames an app may seed through a `crema_interfaces`
  entry;
- the exception classes (`CremaBlockedError`, `CremaConfigError`, `CremaBudgetError`).

Only a removal or a rename is listed. Read newest first.

## 2026-08-24 — the per-provider budget is gone

`Crema Provider.monthly_budget_usd` is removed. `get_usage()` lost its `"providers"` key
with it. Budgets are now per-interface and per-user only (`Crema Model Assignment`,
`Crema Settings`).

## 2026-08-24 — the `security` interface name is gone

`interfaces.PREDEFINED` no longer has a `security` entry. A `Crema Automation Task` or
call that named it as `interface` must move to another interface.

## 2026-08-08 — three Crema Model Assignment fields are gone

`enable_prompt_scan`, `enable_llm_guard`, and `output_trap` are removed from
`Crema Model Assignment`. Security posture moved to the `Crema Guardrails` Single,
site-wide and admin-owned — see [security.md](security.md). An app that seeded any of
these three keys through its own `crema_interfaces` entry now gets that key dropped, with
a `frappe.logger("crema")` warning naming the interface and the key.

## 2026-08-04 — `label` in a `crema_interfaces` entry is now live

Not a removal — an activation, listed here because it changed what an existing key does.
A `crema_interfaces` entry's optional `"label"` string used to be inert. It is now read by
`interfaces.label_for()` and shown in place of the raw interface name wherever a label is
displayed (the Use Cases grid, a Crema Log row's title). An app that already set `"label"`
gets no code change, only a better label.
