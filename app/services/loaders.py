"""Bill loaders — convert uploaded bytes/text into a `BillExtractionResult`.

Three concrete loaders:

  * `TextLoader`  — the user typed a description ("Pay PHCN 12k by Friday").
  * `PDFLoader`   — text-based PDF. We use PyMuPDF (`fitz`) which we
                    already ship; no extra system deps.
  * `ImageLoader` — vision-capable LLM. We send the image as base64 to
                    the chat completions endpoint; no system tesseract
                    install required.

If no LLM is configured, the loaders still return what they can
extract heuristically (text-mode extracts vendor + amount with regex;
PDF mode returns the raw extracted text). The decision agent downstream
will then ask the user for missing fields.

All loaders are async and accept raw bytes (so the FastAPI
`UploadFile.read()` path works without writing to disk).
"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
from abc import ABC, abstractmethod
from typing import Optional

from app.schemas.bill import BillExtractionResult
from app.core.config import settings

logger = logging.getLogger(__name__)

# A rough match for "₦12,345.67" or "NGN 12,345.67"
_AMOUNT_RE = re.compile(r"(?:[₦N]|NGN)\s*([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)
_DATE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b"
)


# ── LLM client (lazy, optional) ─────────────────────────────────────

def _get_llm_client():
    """Return a Groq client wrapped by instructor, or None if no key.

    The loaders work without an LLM — they fall back to regex
    extraction. This keeps the build runnable on a fresh machine
    that has no `GROQ_API_KEY` set.
    """
    api_key = settings.groq_api_key
    if not api_key:
        return None
    try:
        import instructor  # type: ignore
        from groq import Groq  # type: ignore
    except ImportError:
        return None
    return instructor.from_groq(Groq(api_key=api_key))



def _llm_extract(system: str, user: str) -> Optional[BillExtractionResult]:
    """Call Groq with structured output. Returns None on any failure."""
    client = _get_llm_client()
    if client is None:
        return None
    try:
        result = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            response_model=BillExtractionResult,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM extraction failed: %s", exc)
        return None
    return result


# ── Heuristic fallback ──────────────────────────────────────────────

def _regex_extract(text: str) -> BillExtractionResult:
    """Best-effort bill extraction when no LLM is available."""
    amount_match = _AMOUNT_RE.search(text)
    amount = float(amount_match.group(1).replace(",", "")) if amount_match else 0.0
    date_match = _DATE_RE.search(text)
    return BillExtractionResult(
        vendor_name="",  # the LLM does this; regex can't reliably pick a vendor
        amount=amount,
        raw_text=text,
    )


# ── Base class ──────────────────────────────────────────────────────

class BaseLoader(ABC):
    """All loaders implement a single `extract()` async method."""

    @abstractmethod
    async def extract(self) -> BillExtractionResult:
        ...


# ── Text loader ─────────────────────────────────────────────────────

class TextLoader(BaseLoader):
    """User-typed bill description. The LLM does the heavy lifting."""

    def __init__(self, text: str) -> None:
        self.text = text

    async def extract(self) -> BillExtractionResult:
        result = _llm_extract(
            system=(
                "You are a financial assistant. Extract bill or payment "
                "details from the user's message. If a piece of info is "
                "missing, leave it null. Today's date is "
                f"{__import__('datetime').date.today().isoformat()} — use "
                "it to resolve relative dates like 'next Friday'. "
                "For the `due_date` field, ALWAYS return an ISO 8601 date "
                "string in the form `YYYY-MM-DD` (or null if the bill "
                "has no due date). Do not return times, timezones, or "
                "natural-language phrases — just the bare date."
            ),
            user=f"Extract details from this message:\n\n{self.text}",
        )
        if result is not None:
            result.raw_text = self.text
            return result
        return _regex_extract(self.text)


# ── PDF loader ──────────────────────────────────────────────────────

class PDFLoader(BaseLoader):
    """Text-based PDF via PyMuPDF. No system tesseract required."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self._text: Optional[str] = None

    def _extract_text(self) -> str:
        if self._text is not None:
            return self._text
        try:
            import fitz  # PyMuPDF
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("pymupdf not installed") from exc

        doc = fitz.open(stream=self.data, filetype="pdf")
        try:
            chunks: list[str] = []
            for page in doc:
                chunks.append(page.get_text())
            self._text = "\n".join(chunks)
        finally:
            doc.close()
        return self._text

    async def extract(self) -> BillExtractionResult:
        text = self._extract_text()
        if not text.strip():
            raise ValueError("No text could be extracted from this PDF.")

        result = _llm_extract(
            system=(
                "You are an expert financial auditor. Extract bill details "
                "accurately. Leave fields null if not present in the text. "
                "For the `due_date` field, ALWAYS return an ISO 8601 date "
                "string in the form `YYYY-MM-DD` (or null). Do not return "
                "times, timezones, or natural-language phrases."
            ),
            user=f"Extract details from this bill text:\n\n{text[:8000]}",
        )
        if result is not None:
            result.raw_text = text
            return result
        return _regex_extract(text)


# ── Image loader ────────────────────────────────────────────────────

class ImageLoader(BaseLoader):
    """Image-based bill. Sends the image to a vision-capable LLM
    (Groq's `llama-3.2-90b-vision-preview` if available; falls back
    to heuristic extraction)."""

    SUPPORTED_MIME = {"image/png", "image/jpeg", "image/jpg", "image/webp"}

    def __init__(self, data: bytes, mime_type: str = "image/png") -> None:
        self.data = data
        self.mime_type = mime_type if mime_type in self.SUPPORTED_MIME else "image/png"

    async def extract(self) -> BillExtractionResult:
        client = _get_llm_client()
        if client is None:
            raise ValueError("Image extraction requires GROQ_API_KEY to be set.")

        b64 = base64.b64encode(self.data).decode("ascii")
        data_url = f"data:{self.mime_type};base64,{b64}"

        try:
            # The instructor wrapper wants a model that supports vision.
            # Use a vision-capable Groq model; fall back to the text
            # model if not available.
            result = client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                response_model=BillExtractionResult,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Extract bill details from this image. "
                                    "Leave fields null if not visible. "
                                    "Important note: all currency is in NGN "
                                    "(Nigerian Naira). For the `due_date` "
                                    "field, ALWAYS return an ISO 8601 date "
                                    "string in the form `YYYY-MM-DD` "
                                    "(or null if not visible). Do not return "
                                    "times, timezones, or natural-language "
                                    "phrases like 'next Friday'."
                                ),
                            },
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
                strict=True
            )
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("Vision LLM extraction failed: %s", exc)
            raise ValueError(f"Could not extract bill from image: {exc}") from exc


# ── Factory ─────────────────────────────────────────────────────────

def loader_from_upload(
    filename: str,
    content_type: Optional[str],
    data: bytes,
) -> BaseLoader:
    """Pick a loader based on filename / content-type."""
    name = (filename or "").lower()
    ctype = (content_type or "").lower()

    if name.endswith(".pdf") or "pdf" in ctype:
        return PDFLoader(data)
    if (
        name.endswith((".png", ".jpg", ".jpeg", ".webp"))
        or ctype.startswith("image/")
    ):
        mime = ctype if ctype in ImageLoader.SUPPORTED_MIME else "image/png"
        return ImageLoader(data, mime_type=mime)
    # Default: treat as text
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    return TextLoader(text)
