# Frappe Crema

> A hardened LLM interface and security layer for Frappe apps — not a chatbot.

![License](https://img.shields.io/badge/license-MIT-blue)
![Frappe](https://img.shields.io/badge/frappe-v16-3B82F6)
![Python](https://img.shields.io/badge/python-3.14-yellow)
![Status](https://img.shields.io/badge/status-alpha-orange)

> **Alpha.** Crema is version 0.0.1. The public API, the doctype fields, and the
> interface set can change without a migration path. Do not run it in production yet.

Frappe Crema gives every app in your bench one route to OpenAI-compatible LLM
providers — OpenAI, OpenRouter, Groq, Mistral, DeepSeek, Ollama, and more. Named
**interfaces** (one per use-case: translation, OCR, extraction, ...) bind a provider,
a model, a system prompt, an isolation user, and a security layer. There is no other
way to reach a provider from application code — every call goes through
`from crema import ask`.

Crema is a security and integration layer, not a conversational agent. Building a
chatbot on top is explicitly out of scope — see [ROADMAP.md](ROADMAP.md).

## Quick start

1. **Add a provider.** Open **Crema Settings** (Crema's default landing page), click
   **New from Template** in the Providers panel, pick a preset, paste your API key,
   and create — this wizard also sets it as the default provider.
2. **Set the default model.** Pick a **Default Model** above the Model Assignments
   grid. A **Default Isolation User** is already filled in (install creates one for
   you). Save — every interface now works; the grid stays collapsed unless you open a
   row to override one interface's provider, model, or isolation user individually.
3. **Call it:**

   ```python
   from crema import ask
   ask("Fix grammar: helo wrld")
   ```

See [docs/](docs/README.md) for full setup, usage, and automation guides.

## Interfaces

| Interface | Purpose | Fallback |
|---|---|---|
| `simple` | General-purpose, lightweight calls | none — end of the fallback chain |
| `translation` | Faithful translation, preserving tone and meaning | `simple` |
| `complex` | Careful, thorough reasoning for demanding tasks | `simple` |
| `ocr` | Extract text from images / scanned PDFs | none |
| `advanced_ocr` | Stronger retry when `ocr` is low-confidence | none — escalation target only |
| `security` | Layer 2 guard: classifies a prompt's risk | none — fails loud if unresolved |
| `extraction` | Pull structured data out of content | `complex` |
| `classification` | Classify / label content | `simple` |
| `summarization` | Concise, accurate summaries | `simple` |
| `transform` | Propose a diff for an ERP document (never auto-writes) | `complex` |
| `view` | Turn a prompt into a List/Report/Kanban view for the desk UI | `complex` |

## Features

- **One entry point** — no public function anywhere accepts a model, provider, or API
  key; only an interface name.
- **11 named interfaces** with an automatic fallback chain.
- **Desk UI** — a robot button on any list view, `Ask …` in the awesomebar (list views
  only), and a robot button on any form. On a list, upload a document to get one or more
  prefilled new records (the model decides how many), or type a prompt to filter, sort,
  limit, group, report, or kanban-ize the current list. If a filtered list finds nothing,
  one more database query finds which text field holds your words and filters on that
  field instead — no second model call. On a form, type an instruction to
  get a proposed diff to apply and save yourself. A blocked prompt surfaces as a **Blocked** message, never a
  silent failure. Runs as the signed-in user, not a service account.
- **Provider templates** — a wizard sets up OpenAI, OpenRouter, Groq, Mistral,
  DeepSeek, or Ollama with a key in one step, and a live Connection status shows
  whether that key actually works.
- **Model autocomplete** — the model list is fetched live from your provider.
- **Two-layer security** — a local regex/unicode prompt scan, plus an optional LLM
  guard that classifies risk.
- **Isolation user sandbox** — every document access runs under a fenced, low-privilege
  Frappe user; Frappe's own permission engine is the enforcement, not Crema's. Install
  creates a default one (`crema@<site>`, no password) so this works out of the box.
- **OCR** for images, scanned PDFs, and text PDFs, with a confidence score and
  automatic escalation to a stronger model.
- **Smart import** — `extract()` reads a file and proposes the fields of one or more
  new documents; how many records the file holds is the model's own call.
- **Document transform** — `transform()` proposes a diff for an existing document, with
  a form-view apply button in the Desk UI. Neither call writes anything; the caller
  applies the result.
- **Scheduled automation** — fetch a URL, self-plan once, extract, upsert, on a cron
  schedule. The plan is data, never code.
- **Response caching**, configurable per interface or per call.
- **Full audit log** — interface, model, user, status, duration, tokens, and cost, plus
  a prompt hash. Never the prompt or document content itself.
- **Usage dashboard** — a Crema Usage report (group by interface, model, user, or day,
  with a total row and a bar chart) and workspace number cards for calls, cost, and
  blocked rate, all reachable from the Crema workspace sidebar.
- **Budgets** — an optional monthly USD cap per interface, with a shared default and a
  combined per-provider ceiling; the next call over budget is blocked before it reaches
  the provider, and a live Usage column shows spend against budget in Crema Settings.
- **HTTP endpoints** for `ask`, `extract`, and `transform`, rate-limited per IP and per
  user.
- **Crema Settings** — Providers and Model Assignments on one page, and Crema's default
  landing page when opened from the Framework app switcher. A Default Provider/Model/
  Isolation User covers every interface out of the box; the model assignment set is
  fixed and always present, reconciled on every save, at install, and at migrate, and
  its grid stays collapsed until a row actually overrides a default.
- **Desk workspace** with all doctypes, the usage report, and a sidebar entry.

## Requirements

| Requirement | Notes |
|---|---|
| Frappe v16 | installed and managed by bench |
| Python 3.14 | |
| [`litellm`](https://github.com/BerriAI/litellm) | the provider call layer |
| [`pymupdf`](https://github.com/pymupdf/PyMuPDF) | PDF text extraction and rendering |
| `croniter` | validates automation schedules — ships with Frappe itself |

## Documentation

Full guides live in [docs/](docs/README.md): install, configure, use, automate, and
the security model.

## License

MIT — see [LICENSE](LICENSE).
