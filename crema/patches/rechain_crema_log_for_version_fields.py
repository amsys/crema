"""Crema Log: re-chain the tamper-evidence hash after CHAIN_FIELDS grew two fields.

served_model and app_version were added to CHAIN_FIELDS (crema.crema.doctype.crema_log.
crema_log) so an incident can be replayed against the exact model and app version that
answered it. Every existing row's stored chain_sha was computed under the OLD field
tuple -- two more fields means two more separators in the hashed payload even when both
new values are empty, so every stored hash stops matching the moment CHAIN_FIELDS grows.

Verifies the WHOLE existing chain under the frozen pre-change tuple before touching
anything: a tamper that happened before this upgrade must stay visible after it, not be
silently erased by a blanket re-hash. Only a chain that verifies clean end to end gets
re-chained, under the new tuple -- served_model/app_version are NULL on every
pre-existing row (this patch never backfills them; a guessed value in an audit row is
worse than an empty one), so _canon renders them "" the same as any other falsy value.
"""

import frappe

# Frozen snapshot of CHAIN_FIELDS as it stood before served_model/app_version were
# added. Must NEVER be changed to track crema.crema.doctype.crema_log.crema_log.
# CHAIN_FIELDS -- that is the whole point: this is what a pre-upgrade row's stored
# chain_sha was actually computed over.
_OLD_CHAIN_FIELDS = (
    "interface",
    "model",
    "provider",
    "user",
    "status",
    "detail",
    "prompt_sha",
    "duration_ms",
    "llm_calls",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost_usd",
    "creation",
    "chain_seq",
)


def execute() -> None:
    if not frappe.db.has_column("Crema Log", "served_model"):
        return
    if not frappe.db.has_column("Crema Log", "app_version"):
        return

    from crema.crema.doctype.crema_log.crema_log import CHAIN_FIELDS, chain_hash

    # fields is built from CHAIN_FIELDS, a fixed module-level constant -- no user input
    # reaches this query. chain_seq is one of CHAIN_FIELDS, so it's already included.
    fields = ", ".join(["name", "chain_sha", *CHAIN_FIELDS])
    rows = frappe.db.sql(  # nosemgrep: frappe-sql-format-injection
        f"select {fields} from `tabCrema Log` order by chain_seq asc", as_dict=True
    )
    if not rows:
        return

    # Pass 1: verify the existing chain under the OLD field tuple -- same walk and the
    # same anchor rule as crema.log.verify_chain (a row with no chain_sha resets the
    # anchor; the next chained row is trusted, not recomputed, the same way a row whose
    # true predecessor retention cleanup already deleted is trusted).
    previous: str | None = None
    for row in rows:
        if not row.chain_sha:
            previous = None
            continue
        if previous is None:
            previous = row.chain_sha
            continue
        if chain_hash(row, previous, fields=_OLD_CHAIN_FIELDS) != row.chain_sha:
            frappe.log_error(
                title="Crema Log rechain skipped: pre-existing chain break",
                message=(
                    f"Row {row.name} failed verification under the pre-upgrade chain "
                    "fields. The audit chain was left untouched by "
                    "rechain_crema_log_for_version_fields -- investigate the break "
                    "before re-running a migrate."
                ),
            )
            return
        previous = row.chain_sha

    # Pass 2: the whole chain verified clean -- re-chain every row under the new tuple,
    # in the same order, with the same anchor rule.
    previous = None
    for row in rows:
        if not row.chain_sha:
            previous = None
            continue
        new_sha = chain_hash(row, previous)
        frappe.db.set_value("Crema Log", row.name, "chain_sha", new_sha, update_modified=False)
        previous = new_sha
