# Frappe Crema

> A hardened LLM interface and security layer for Frappe apps — not a chatbot.

[![CI](https://github.com/amsys/crema/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/amsys/crema/actions/workflows/ci.yml?query=branch%3Adevelop)
[![Quality gate status](https://sonarcloud.io/api/project_badges/measure?project=amsys_crema&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=amsys_crema)
![License](https://img.shields.io/badge/license-MIT-blue)
![Frappe](https://img.shields.io/badge/frappe-develop-3B82F6)
![Python](https://img.shields.io/badge/python-3.14-yellow)
![Status](https://img.shields.io/badge/status-alpha-orange)

> **Alpha.** This branch is version 17.0.0-dev. It follows Frappe `develop`, the future
> v17 — not released yet. It needs a bench on Frappe `develop`, and it needs heavy
> testing before its first release. For a Frappe v16 bench, use the `version-16` branch
> instead.

Crema gives every app in your bench one route to OpenAI-compatible LLM providers —
OpenAI, OpenRouter, Groq, and about twenty more, hosted and local. A named **interface**
(one per use-case: translation, OCR, extraction, ...) binds a provider, a model, a
system prompt, and an isolation user; one ordered, site-wide guardrail list checks
every request. Application code never names a provider — every call is
`from crema import ask`.

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
- **Guardrails** — one ordered, pluggable list of safety checks on every request —
  a local prompt scan, a reply check — each with its own
  action and use-case filter, and a hook for an app to add its own check
  ([docs/security.md](docs/security.md#guardrails)).
- **Works behind a gateway** — point a Crema Provider at litellm-proxy (or another
  gateway) for prompt-injection detection, NER-based PII masking, and per-key spend
  limits, with no code change; Crema stamps the caller and interface on every call so
  the gateway's own metering lines up with its audit log
  ([docs/security.md](docs/security.md#behind-a-gateway)).
- **Reversible masking (Experimental)** — two Hide guardrails swap personal values
  (emails, phone numbers, likely names) and health keywords for placeholders before
  a request leaves the server, then restore the real values in the reply
  ([docs/security.md](docs/security.md#masking-experimental)).
- **Isolation user sandbox** — document access runs as a low-privilege user, fenced
  by Frappe's own permission engine, plus a site-wide blocked-doctype list for the
  automation and desk write paths ([docs/security.md](docs/security.md)).
- **OCR and transcription** — images, PDFs, and audio, with confidence scoring and
  automatic escalation.
- **Propose, never write** — `extract()` and `transform()` return proposals; the
  caller applies them.
- **Automation** — a task reads URLs, your own records, or uploaded files, plans
  once, and creates or updates records on a schedule, a document event, or a webhook
  ([docs/automation.md](docs/automation.md)).
- **Cost control and audit** — per-user, per-interface, and per-provider monthly
  budgets, response caching, a usage dashboard, and a tamper-evident audit row for
  every call — never the prompt or document content itself.
- **HTTP endpoints and admin UI** — rate-limited endpoints for `ask`, `extract`,
  `ocr`, and `transform`, and one settings page with a provider template wizard
  ([docs/use.md](docs/use.md)).

## Requirements

| Requirement | Notes |
|---|---|
| Frappe develop (pre-release v17) | installed and managed by bench |
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
