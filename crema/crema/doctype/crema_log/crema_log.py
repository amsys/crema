"""Crema Log controller. Records are inserted with ignore_permissions=True from
crema/api.py and are otherwise read/delete only -- the one exception is before_insert,
which stamps a hash chain (see crema.log.verify_chain) linking each row to the one
before it, so an edit or a deletion inside the retention window becomes evident."""

from __future__ import annotations

import datetime
import hashlib

import frappe
from frappe.model.document import Document

# The exact, ordered set of fields chained into a row's fingerprint: every audit field
# the row can carry, plus chain_seq itself (so reordering the chain is caught, not only
# editing a row's content), except `name` (autoname runs after before_insert, so it
# isn't set yet) and chain_sha itself. crema.log.verify_chain imports this so both sides
# of the hash always agree on what's covered.
CHAIN_FIELDS = (
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


def _canon(value) -> str:
    """One canonical string per value on BOTH sides of the hash: at insert time the row
    holds python values (creation is frappe.utils.now()'s "%Y-%m-%d %H:%M:%S.%f" string,
    cost_usd a raw float accumulated by client._record_usage), while verify_chain reads
    the database's round trip (creation a datetime that str() would render WITHOUT the
    ".000000" on a zero-microsecond row; cost_usd a decimal(21,9) column, i.e. rounded to
    9 places, handed back as float). Without this, any cost with a float artifact past
    9 decimals -- 0.1 + 0.2 -- hashes differently before and after the round trip and
    verify_chain reports tampering that never happened."""
    if not value:
        return ""
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")
    if isinstance(value, float):
        # ponytail: 9 = the column's decimal(21,9) scale; python's half-even rounding
        # can disagree with MySQL's half-up on an exact .5 at the 9th place, but no
        # accumulated float cost lands exactly there.
        return f"{value:.9f}"
    return str(value)


def chain_hash(row, previous: str | None) -> str:
    """SHA-256 of `previous` (the prior row's chain_sha, or "" for the first row in the
    chain) plus this row's own CHAIN_FIELDS values. `row` is anything CHAIN_FIELDS can
    be read off with .get() -- a Document at insert time, or a plain dict when
    verify_chain re-reads a row from the database; _canon keeps those two views of one
    value hashing identically."""
    payload = "|".join(_canon(row.get(f)) for f in CHAIN_FIELDS)
    return hashlib.sha256(f"{previous or ''}|{payload}".encode()).hexdigest()


def _tail_chain_state() -> tuple[str | None, int]:
    """The most recently chained row's (chain_sha, chain_seq), read under a row lock so
    two concurrent inserts can't both chain off the same tail and fork the chain.

    Ordered by chain_seq, not creation: creation is a datetime(6) column, but several
    rows inserted back to back -- three in one test method, or a burst of real calls --
    can still land on the identical microsecond, and name (a random hash) breaks that
    tie in an order that has nothing to do with when each row was actually written.
    chain_seq is assigned under this same lock specifically so it can't tie: (0, None)
    when nothing has chained yet, so the first row in the chain gets chain_seq 1.
    # ponytail: tail row lock, unindexed chain_seq scan -- fine at expected log volume;
    # add an index (and/or per-shard chains) if this ever becomes a hot path.
    """
    row = frappe.db.sql(
        "select chain_sha, chain_seq from `tabCrema Log` order by chain_seq desc limit 1 for update",
        as_dict=True,
    )
    if not row or row[0].chain_seq is None:
        return None, 0
    return row[0].chain_sha, row[0].chain_seq


class CremaLog(Document):
    def before_insert(self):
        previous_sha, previous_seq = _tail_chain_state()
        self.chain_seq = previous_seq + 1
        self.chain_sha = chain_hash(self, previous_sha)
