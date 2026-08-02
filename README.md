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

## Desk UI

A robot button on every list view and every form, plus `Ask …` in the awesomebar.
Runs as the signed-in user, never a service account.

- **List → view.** Type a request to filter, sort, limit, group, or switch to Report or
  Kanban. If the result is empty, one more database query finds which text field holds
  your words and filters on that — no second model call.
- **List → records.** Drop a document to get one or more prefilled new records (the
  model decides how many).
- **Form → diff.** Type an instruction to get a proposed diff for the open document.

Nothing is written until you save it. A blocked prompt, a budget cap, or any other
failure surfaces as a message — never a silent no-op.

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
| `transcribe` | Speech-to-text for audio files | none |

## Features

- **One entry point** — no public function accepts a model, provider, or API key; only
  an interface name. `ask()` also takes context, files (File URLs or in-memory
  `(bytes, mime)` tuples), and multi-turn history.
- **12 named interfaces** with an automatic fallback chain. Other apps register their
  own interfaces through a `crema_interfaces` hook; a predefined name can never be
  overridden.
- **Three security layers** — a local regex/unicode prompt scan, an optional LLM guard
  that classifies risk, and an output-trap nonce that catches a hijacked response.
- **Isolation user sandbox** — document access runs under a fenced, low-privilege
  Frappe user; Frappe's own permission engine enforces it, not Crema's.
- **OCR** for images, scanned PDFs, and text PDFs, with a confidence score and
  automatic escalation to a stronger model.
- **Transcription** — `transcribe()` turns an audio file into text, with the same
  budget checks and audit log as every other call.
- **Propose, never write** — `extract()` proposes one or more new documents from a
  file (how many is the model's own call); `transform()` proposes a diff for an
  existing document. Neither writes anything; the caller applies the result.
- **Automation** — a task reads a URL or a permission-fenced query over your own
  records, self-plans once, and then either upserts records, writes values back onto
  the records it read, or just reports. It runs on a cron schedule or on a document
  event, and a Dry Run button shows what it would do before it does it. The plan is
  data, never code.
- **Health probe** — `health()` and `is_configured()` let a consuming app show its own
  status page; the caller applies its own role check.
- **Cost control and audit** — an optional monthly USD budget per interface and per
  provider, response caching per interface or per call, a usage dashboard, and a full
  audit log: interface, model, user, status, duration, tokens, cost, and a prompt
  hash — never the prompt or document content itself.
- **HTTP endpoints** for `ask`, `extract`, and `transform`, rate-limited per IP and per
  user.
- **Admin in one place** — Crema Settings holds the providers (a template wizard for
  OpenAI, OpenRouter, Groq, Mistral, DeepSeek, or Ollama, with live model autocomplete
  and connection checks) and the per-interface model assignments; a default provider,
  model, and isolation user cover every interface out of the box. A Desk workspace
  adds the doctypes and the usage report.

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
