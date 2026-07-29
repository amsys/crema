"""Crema Provider controller.

Providers start out empty in the list — see the "New from Template" list action in
crema_provider_list.js, backed by PRESETS + get_presets() below.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

import frappe
from crema import cache
from frappe.model.document import Document

# Apply-template presets: label -> base_url. User picks one, pastes their key, enables.
PRESETS = {
    "OpenAI": "https://api.openai.com/v1",
    "OpenRouter": "https://openrouter.ai/api/v1",
    "Groq": "https://api.groq.com/openai/v1",
    "Mistral": "https://api.mistral.ai/v1",
    "DeepSeek": "https://api.deepseek.com/v1",
    "Ollama": "http://localhost:11434/v1",
}


def _is_local_or_private(base_url: str) -> bool:
    """True if base_url points at localhost or a private/loopback address (e.g. a
    local Ollama instance) — the one case where an unauthenticated provider is fine."""
    host = urlparse(base_url).hostname or ""
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return bool(ip.is_private or ip.is_loopback)
    except ValueError:
        return False


class CremaProvider(Document):
    def validate(self) -> None:
        if self.enabled and not self.get_password("api_key", raise_exception=False):
            if not _is_local_or_private(self.base_url or ""):
                frappe.throw(
                    "API Key is required to enable a provider, unless its Base URL is "
                    "localhost or a private network address (e.g. a local Ollama instance)."
                )

    def on_update(self) -> None:
        cache.clear_provider(self.name)

    def on_trash(self) -> None:
        cache.clear_provider(self.name)


@frappe.whitelist()
def get_presets() -> dict[str, str]:
    """Data source for the "New from Template" list action dialog."""
    frappe.only_for("System Manager")
    return PRESETS


@frappe.whitelist()
def create_from_template(
    preset: str,
    provider_name: str,
    base_url: str,
    api_key: str | None = None,
    enabled: bool = True,
    set_as_default: bool = True,
) -> str:
    """Back end for the "New from Template" wizard (crema_new_provider_dialog in
    crema.bundle.js) — key + optional "set as default" in one step, instead of
    dumping the admin on an empty Crema Provider form. `preset` is only used to
    validate the template picked is a real one; provider_name/base_url are the
    (possibly edited) prefill, since a second key for the same vendor needs a
    distinct name (Crema Provider.autoname is field:provider_name).

    A normal doc.insert() — never frappe.db.set_value — so CremaProvider.validate
    runs (enabled-without-api_key is still rejected for a public base_url) and the
    Password field is encrypted the ordinary way. No ignore_permissions: the
    frappe.only_for below is what makes that safe.

    `enabled`/`set_as_default` are run through frappe.utils.sbool: this module has
    `from __future__ import annotations` (every annotation is a string at runtime),
    so frappe.call's whitelisted-method dispatch — get_newargs in frappe/__init__.py —
    does no bool coercion from the Check field's wire value; an un-coerced falsy-
    looking string like "0" is truthy in Python and would silently ignore an unticked
    checkbox (see extract_api's docstring for the same footgun)."""
    frappe.only_for("System Manager")
    if preset not in PRESETS:
        frappe.throw(f"'{preset}' is not a known provider template.")

    enabled = frappe.utils.sbool(enabled)
    set_as_default = frappe.utils.sbool(set_as_default)

    doc = frappe.new_doc("Crema Provider")
    doc.provider_name = provider_name
    doc.base_url = base_url
    doc.enabled = 1 if enabled else 0
    if api_key:
        doc.api_key = api_key
    doc.insert()

    if set_as_default:
        settings = frappe.get_single("Crema Settings")
        settings.default_provider = doc.name
        settings.save()

    return doc.name
