# Frappe Crema

> A hardened LLM interface and security layer for Frappe apps — not a chatbot.

[![CI](https://github.com/amsys/crema/actions/workflows/ci.yml/badge.svg)](https://github.com/amsys/crema/actions/workflows/ci.yml)
![License](https://img.shields.io/badge/license-MIT-blue)
![Frappe](https://img.shields.io/badge/frappe-v16-3B82F6)
![Python](https://img.shields.io/badge/python-3.14-yellow)
![Status](https://img.shields.io/badge/status-beta-yellow)

> **Beta.** Crema is version 0.1.0. The public API and the doctype fields are stable.
> A change that breaks them will come with a migration path. Test Crema on a
> non-production site before you deploy it.

Frappe Crema gives every app in your bench one route to OpenAI-compatible LLM
providers — OpenAI, OpenRouter, Groq, Mistral, DeepSeek, Ollama, and more. Named
**interfaces** (one per use-case: translation, OCR, extraction, ...) bind a provider,
a model, a system prompt, an isolation user, and a security layer. There is no other
way to reach a provider from application code — every call goes through
`from crema import ask`.

Crema is a security and integration layer, not a conversational agent. Building a
chatbot on top is explicitly out of scope — see [ROADMAP.md](ROADMAP.md).

## Quick start

1. **Add a provider.** Open **Providers** — the first item in the Crema sidebar —
   and click **New from Template**, pick a preset, paste your API key, and create.
   This wizard also sets it as the default provider.
2. **Set the default model.** Open **Crema Settings** and pick a **Default Model** above the Use Cases
   grid. A **Default Runs-As User** is already filled in (install creates one for
   you). Save — every interface now works; the grid stays collapsed unless you open a
   row to override one interface's provider, model, or Runs As user individually.
3. **Call it:**

   ```python
   from crema import ask
   ask("Fix grammar: helo wrld")
   ```

See [docs/](docs/README.md) for full setup, usage, and automation guides.

## Desk UI

A robot button on list views and on forms you may edit, plus `Ask …` in the search
bar (the awesomebar). Your request runs under your own session. Document and file
access inside a server call runs as the interface's isolation user — see
[docs/security.md](docs/security.md).

- **List → view.** Type a request to filter, sort, limit, group, or switch to Report or
  Kanban. If the result is empty and the request was a single text lookup, one more
  database query probes up to eight readable text fields to find which one holds your
  words — no second model call.
- **List → records.** Drop a document to get one or more prefilled new records (the
  model decides how many).
- **Form → diff.** Type an instruction to get a proposed diff for the open document.

Crema writes nothing until you save the result. A blocked prompt, an exceeded
monthly budget, or any other failure surfaces as a message — never a silent no-op.

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
- **Isolation user sandbox** — document access inside a call runs as a dedicated
  low-privilege Frappe user, the isolation user; Frappe's own permission engine
  enforces it, not Crema's.
- **OCR** for images, scanned PDFs, and text PDFs, with a confidence score. A
  low-confidence result escalates to the `advanced_ocr` interface — when that
  interface resolves to a different provider or model than `ocr` (see
  [docs/configure.md](docs/configure.md)).
- **Transcription** — `transcribe()` turns an audio file into text, with the same
  budget checks and audit log as every other call.
- **Propose, never write** — `extract()` proposes one or more new documents from a
  file (how many is the model's own call); `transform()` proposes a diff for an
  existing document. Neither writes anything; the caller applies the result.
- **Automation** — a task reads one or more sources — any mix of URLs and
  permission-fenced queries over your own records, optionally including the files
  attached to each record (so an invoice that arrives by email can be read into a
  record), or a permission-fenced query over uploaded files on their own (so an
  invoice attached anywhere on the site can be read into a record, matched on a field
  you choose so a re-read updates instead of duplicating) — self-plans once, and then
  either creates or updates records, writes values back onto the records it read, or
  changes nothing. Any task can email what it did. It runs on a cron schedule, on a
  document event, or on an authenticated webhook call, and a Dry Run button shows what
  it would do before it does it. The plan is data, never code.
- **Health probe** — `health()` and `is_configured()` let a consuming app show its own
  status page; the caller applies its own role check.
- **Cost control and audit** — an optional monthly budget per interface and per
  provider, response caching per interface or per call, and a usage dashboard. Every
  call writes an audit row: interface, model, user, status, duration, tokens, cost,
  and a request hash — never the prompt or document content itself.
- **HTTP endpoints** for `ask`, `extract`, and `transform`, rate-limited per IP and
  per user, plus System-Manager utility endpoints (see
  [docs/use.md](docs/use.md)).
- **Admin in one place** — Crema Settings holds the providers and the per-interface
  model assignments. A template wizard creates a provider for OpenAI, OpenRouter,
  Groq, Mistral, DeepSeek, or Ollama, with live model autocomplete and connection
  checks. A default provider, model, and isolation user cover every interface out of
  the box. A Desk workspace adds the doctypes and the usage report.

## Requirements

| Requirement | Notes |
|---|---|
| Frappe v16 | installed and managed by bench |
| Python 3.14 | |
| [`litellm`](https://github.com/BerriAI/litellm) | the provider call layer |
| [`pymupdf`](https://github.com/pymupdf/PyMuPDF) | PDF text extraction and rendering |
| [`ftfy`](https://github.com/rspeer/python-ftfy) | repairs damaged text before the prompt scan |
| [`anyascii`](https://github.com/anyascii/anyascii) | folds look-alike letters to ASCII for the prompt scan |
| `croniter` | validates automation schedules — ships with Frappe itself |

## Documentation

Full guides live in [docs/](docs/README.md): install, configure, use, automate, and
the security model.

## License

MIT — see [LICENSE](LICENSE).
