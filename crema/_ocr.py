"""OCR pipeline: PDF/image -> extracted text with a confidence score, escalating to
a stronger interface when the first attempt is unparseable or low-confidence.

Reimplements the pattern proven in fcr_fleet/ocr/engine.py (not imported — crema has no
dependency on fcr_fleet): pymupdf for PDF text extraction and page rendering, PIL for
downscaling plain images. Bypasses crema.api.ask() on purpose — this is a document
extraction pipeline, not a user-prompt path, so it talks to crema.client directly (the
same boundary the tests mock). That also means layer 1 (security.scan) never runs on
OCR'd document content — by design: a scan tuned for user prompts throws false
positives on arbitrary document text (dates, numbers, long base64-like OCR noise,
etc.), and this path never reaches crema.api.ask() where that layer is wired in.
Every call is still audited via Crema Log, below.
"""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import time
from typing import Any

import frappe
from crema import client
from crema import log as _log
from crema._json import strip_fence
from crema.exceptions import CremaBlockedError, CremaBudgetError, CremaConfigError

_MAX_SIDE_PX = 1568  # optimal long-edge resolution for vision models
_TEXT_PDF_MIN_CHARS = 100  # minimum extractable chars to treat a PDF as "text", not scanned
_MIN_CONFIDENCE = 0.7

_JSON_INSTRUCTION = 'Output ONLY JSON `{"text": "<extracted text>", "confidence": <0-1>}`.'

_MAGIC_MIME = [
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
]


def _sniff_mime(data: bytes) -> str:
    """Best-effort mime sniff from magic bytes, for raw bytes input with no filename."""
    for magic, mime in _MAGIC_MIME:
        if data.startswith(magic):
            return mime
    return ""


def _is_pdf(data: bytes, mime: str | None) -> bool:
    return mime == "application/pdf" or data[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# File retrieval
# ---------------------------------------------------------------------------


def _load_bytes(file: str | bytes) -> tuple[bytes, str]:
    """Return (bytes, mime) for `file`.

    A str is treated as a frappe File URL, permission-checked as the CALLING (session)
    user before its content is read — the same fence api._resolve_files applies to
    ask(files=[...]). This is the isolation user's own permission wherever this runs
    inside sandbox.isolation (every automation path, per CLAUDE.md); the desk
    file->record flow instead runs as the browser user who just uploaded the file,
    which is deliberate — crema.bundle.js uploads a private, unattached File owned by
    that user, and only that user (or an isolation-user check) could then read it back.
    Raises frappe.PermissionError on a File the caller can't read, and
    frappe.DoesNotExistError if `file` doesn't name a File document at all — no more
    falling back to reading an arbitrary on-disk path. Raw bytes are used as-is, mime
    sniffed from magic bytes only.
    """
    if isinstance(file, bytes):
        return file, _sniff_mime(file)

    file_doc = frappe.get_doc("File", {"file_url": file})
    file_doc.check_permission("read")
    content = file_doc.get_content()

    if isinstance(content, str):
        content = content.encode("utf-8")
    mime = mimetypes.guess_type(file_doc.file_name or file)[0] or _sniff_mime(content)
    return content, mime


# ---------------------------------------------------------------------------
# PDF / image prep -> message content parts
# ---------------------------------------------------------------------------


def _extract_pdf_text(data: bytes) -> str:
    """Extract text from a PDF. Returns "" if it's a scan (no embedded text)."""
    import pymupdf

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
        return "".join(page.get_text() for page in doc).strip()
    except Exception:
        return ""


def _pdf_to_images(data: bytes) -> list[tuple[bytes, str]]:
    """Render each PDF page at 2x zoom, capping the long edge at _MAX_SIDE_PX."""
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    images: list[tuple[bytes, str]] = []
    for page in doc:
        pix = page.get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0))
        if max(pix.width, pix.height) > _MAX_SIDE_PX:
            zoom = 2.0 * _MAX_SIDE_PX / max(pix.width, pix.height)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        images.append((pix.tobytes("png"), "image/png"))
    return images


def _downscale_image(data: bytes, mime: str) -> tuple[bytes, str]:
    """Downscale a plain image so its long edge is <= _MAX_SIDE_PX."""
    try:
        from PIL import Image as PILImage

        img = PILImage.open(io.BytesIO(data))
        w, h = img.size
        if max(w, h) <= _MAX_SIDE_PX:
            return data, mime
        scale = _MAX_SIDE_PX / max(w, h)
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), PILImage.LANCZOS)
        buf = io.BytesIO()
        fmt = "PNG" if mime == "image/png" else "JPEG"
        img.save(buf, format=fmt)
        return buf.getvalue(), ("image/png" if fmt == "PNG" else "image/jpeg")
    except Exception:
        return data, mime


def _image_part(data: bytes, mime: str) -> dict:
    import base64

    b64 = base64.standard_b64encode(data).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def prep_parts(content: bytes, mime: str) -> list[dict]:
    """Message content-array parts for a file's bytes: extracted text for a text PDF,
    rendered page images for a scanned PDF, or a single downscaled image part for a
    plain image. [] for anything else. Shared by ocr() below and crema.api._resolve_files
    (the ask(files=[...]) vision path)."""
    if _is_pdf(content, mime):
        text = _extract_pdf_text(content)
        if len(text) >= _TEXT_PDF_MIN_CHARS:
            return [{"type": "text", "text": text}]
        return [_image_part(data, m) for data, m in _pdf_to_images(content)]
    if mime.startswith("image/"):
        return [_image_part(*_downscale_image(content, mime))]
    return []


# ---------------------------------------------------------------------------
# LLM call + confidence parsing
# ---------------------------------------------------------------------------


def _parse_result(raw: str) -> tuple[str, float | None]:
    """Parse the forced {"text", "confidence"} JSON. (text, None) if unparseable."""
    try:
        parsed = json.loads(strip_fence(raw))
    except json.JSONDecodeError, TypeError:
        return "", None
    if not isinstance(parsed, dict):
        return "", None

    text = parsed.get("text") or ""
    try:
        confidence = float(parsed.get("confidence"))
    except TypeError, ValueError:
        confidence = None
    return text, confidence


def _call(cfg: dict[str, Any], user_message: dict, response_format: dict) -> str:
    messages = [{"role": "system", "content": cfg.get("system_prompt") or ""}, user_message]
    return client._complete(cfg, messages, response_format)


def _run(cfg: dict[str, Any], user_message: dict, response_format: dict) -> tuple[dict[str, Any], dict]:
    """The escalation logic itself, split out so `ocr()` can wrap it in one
    try/except/log. Returns (result, cfg_of_the_interface_the_returned_text_came_from)
    — "ocr" unless escalation to "advanced_ocr" fired."""
    text, confidence = _parse_result(_call(cfg, user_message, response_format))
    if confidence is not None and confidence >= _MIN_CONFIDENCE:
        return {"text": text, "confidence": confidence, "escalated": False}, cfg

    try:
        adv_cfg = client._resolve("advanced_ocr")
    except CremaConfigError:
        return {"text": text, "confidence": confidence or 0.0, "escalated": False}, cfg

    # With a Crema Settings default_provider/default_model configured, an unconfigured
    # "advanced_ocr" now resolves to the SAME provider+model as "ocr" instead of raising
    # CremaConfigError above — retrying the identical call buys nothing. Skip it.
    if (adv_cfg["provider"], adv_cfg["model"]) == (cfg["provider"], cfg["model"]):
        return {"text": text, "confidence": confidence or 0.0, "escalated": False}, cfg

    # Escalation spends against advanced_ocr's own interface ceiling, not ocr's
    # (already checked by our caller) — enforce it before the second provider call.
    # Over budget, the paid-for base result is served instead of the retry, same
    # shape as the unresolvable-advanced_ocr skip above.
    try:
        _log.check_budget(adv_cfg)
    except CremaBudgetError:
        return {"text": text, "confidence": confidence or 0.0, "escalated": False}, cfg

    adv_text, adv_confidence = _parse_result(_call(adv_cfg, user_message, response_format))
    if adv_confidence is not None and (confidence is None or adv_confidence > confidence):
        return {"text": adv_text, "confidence": adv_confidence, "escalated": True}, adv_cfg
    # The retry did not beat the first attempt, so the ORIGINAL text is what's served —
    # return `cfg`, not `adv_cfg`, so Crema Log attributes it to the interface it came from.
    return {"text": text, "confidence": confidence or 0.0, "escalated": True}, cfg


def ocr(file: str | bytes, instruction: str | None = None) -> dict[str, Any]:
    """OCR a frappe File URL or raw bytes.

    Returns {"text": str, "confidence": float, "escalated": bool}. See the module
    docstring and the app plan for the full pipeline description. Every call is
    logged to Crema Log (never the extracted text) — see the module docstring for why
    the security scan layers are deliberately not part of this path.
    """
    cfg = client._resolve("ocr")

    # File loading and PDF/image prep are INSIDE the try: a bad File URL, a permission
    # error or a corrupt/malicious PDF (pymupdf is unguarded) must produce an audit row
    # too, otherwise "every call is logged" above would be a lie.
    prompt_sha = None
    start = time.monotonic()
    try:
        content, mime = _load_bytes(file)
        prompt_sha = hashlib.sha256(content).hexdigest()
        parts = prep_parts(content, mime)

        prompt_text = f"{instruction}\n\n{_JSON_INSTRUCTION}" if instruction else _JSON_INSTRUCTION
        user_message = {"role": "user", "content": [{"type": "text", "text": prompt_text}, *parts]}
        response_format = {"type": "json_object"}

        _log.check_budget(cfg)
        result, used_cfg = _run(cfg, user_message, response_format)
    except (CremaBudgetError, CremaBlockedError) as exc:
        # CremaBlockedError here is a guardrail inside client._complete's onion — in
        # practice the reply check, the only guardrail whose default covers OCR (the
        # scan skips this path by design) — logged as Blocked, not
        # Error, same reasoning as crema.api._Ask.__call__'s equivalent handler.
        duration_ms = int((time.monotonic() - start) * 1000)
        _log.insert(
            cfg["interface"],
            cfg.get("model"),
            "Blocked",
            str(exc),
            prompt_sha=prompt_sha,
            duration_ms=duration_ms,
            provider=cfg.get("provider"),
        )
        raise
    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        _log.insert(
            cfg["interface"],
            cfg.get("model"),
            "Error",
            _log.redact(f"{type(exc).__name__}: {exc}")[:2000],
            prompt_sha=prompt_sha,
            duration_ms=duration_ms,
            provider=cfg.get("provider"),
        )
        raise

    duration_ms = int((time.monotonic() - start) * 1000)
    _log.insert(
        used_cfg["interface"],
        used_cfg.get("model"),
        "Success",
        "escalated" if result["escalated"] else "",
        prompt_sha=prompt_sha,
        duration_ms=duration_ms,
        provider=used_cfg.get("provider"),
    )
    return result
