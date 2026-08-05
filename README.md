# Frappe Crema

> A hardened LLM interface and security layer for Frappe apps — not a chatbot.

[![CI](https://github.com/amsys/crema/actions/workflows/ci.yml/badge.svg)](https://github.com/amsys/crema/actions/workflows/ci.yml)
![License](https://img.shields.io/badge/license-MIT-blue)
![Frappe](https://img.shields.io/badge/frappe-v16-3B82F6)
![Python](https://img.shields.io/badge/python-3.14-yellow)
![Status](https://img.shields.io/badge/status-beta-yellow)

> **Beta.** Crema is version 0.1.0. The public API and the doctype fields are stable.
> Test Crema on a non-production site before you deploy it.

Crema gives every app in your bench one route to OpenAI-compatible LLM providers —
OpenAI, OpenRouter, Groq, Mistral, DeepSeek, Ollama, and more. A named **interface**
(one per use-case: translation, OCR, extraction, ...) binds a provider, a model, a
system prompt, an isolation user, and a security layer. Application code never names
a provider — every call is `from crema import ask`.

Crema is not a conversational agent, and a chatbot is out of scope — see
[ROADMAP.md](ROADMAP.md).

## Quick start

1. **Add a provider.** Open **Providers** — the first item in the Crema sidebar —
   click **New from Template**, pick a preset, paste your API key, and create.
2. **Set the default model.** Open **Crema Settings**, pick a **Default Model**, and
   save. Every interface now works.
3. **Call it:**

   ```python
   from crema import ask
   ask("Fix grammar: helo wrld")
   ```

See [docs/](docs/README.md) for full setup, usage, and automation guides.

## Desk UI

Users with the `Crema User` role get a robot button on list views and forms, plus
`Ask …` in the search bar. Type a plain request and the model turns it into a view,
a new record, an edit, or a delete — or refuses if the list view cannot do it. Drop
a document on a list view and it becomes one or more prefilled records. Nothing is
written until you save the result yourself, and a bulk edit or delete lists every
affected record for you to confirm first. Full details in [docs/use.md](docs/use.md).

## Interfaces

| Interface | Purpose |
|---|---|
| `simple` | General lightweight calls |
| `translation` | Faithful translation |
| `complex` | Careful reasoning for demanding tasks |
| `ocr` | Text from images and scanned PDFs |
| `advanced_ocr` | Stronger retry when `ocr` is unsure |
| `security` | The guard: classifies a prompt's risk |
| `extraction` | Structured data out of content |
| `classification` | Classify or label content |
| `summarization` | Short, accurate summaries |
| `transform` | Propose a diff for a document |
| `view` | The desk List Assistant |
| `transcribe` | Speech-to-text for audio files |

Most interfaces fall back to `simple` or `complex` when unconfigured, and other apps
can register their own — see [docs/configure.md](docs/configure.md).

## Features

- **One entry point** — no public function accepts a model, provider, or API key;
  only an interface name.
- **Three security layers** — a local prompt scan, an optional LLM guard, and an
  output trap that catches a hijacked response
  ([docs/security.md](docs/security.md)).
- **Isolation user sandbox** — document access runs as a low-privilege user, fenced
  by Frappe's own permission engine.
- **OCR and transcription** — images, PDFs, and audio, with confidence scoring and
  automatic escalation.
- **Propose, never write** — `extract()` and `transform()` return proposals; the
  caller applies them.
- **Automation** — a task reads URLs, your own records, or uploaded files, plans
  once, and creates or updates records on a schedule, a document event, or a webhook
  ([docs/automation.md](docs/automation.md)).
- **Cost control and audit** — monthly budgets, response caching, a usage dashboard,
  and an audit row for every call — never the prompt or document content itself.
- **HTTP endpoints and admin UI** — rate-limited endpoints for `ask`, `extract`, and
  `transform`, and one settings page with a provider template wizard
  ([docs/use.md](docs/use.md)).

## Requirements

| Requirement | Notes |
|---|---|
| Frappe v16 | installed and managed by bench |
| Python 3.14 | |
| [`litellm`](https://github.com/BerriAI/litellm) | the provider call layer |
| [`pymupdf`](https://github.com/pymupdf/PyMuPDF) | PDF text extraction and rendering |
| [`ftfy`](https://github.com/rspeer/python-ftfy) | repairs damaged text before the scan |
| [`anyascii`](https://github.com/anyascii/anyascii) | folds look-alike letters to ASCII |
| `croniter` | validates schedules — ships with Frappe |

## Documentation

Full guides live in [docs/](docs/README.md): install, configure, use, automate, and
the security model.

## License

MIT — see [LICENSE](LICENSE).
