"""Text extraction from PDFs.

Reading is done through a fallback chain of independent readers
(:func:`_read_pdf_text`). pdfminer (via pdfplumber) is non-deterministic on some
valid PDFs — notably certain publisher-exported files — and used to be the sole
reader, so any pdfminer hiccup got the file terminally quarantined to
``corrupted/``. We now fall back to pypdfium2 and then poppler's ``pdftotext``
before declaring a file unreadable, so only a file that *no* reader can open is
treated as corrupt.
"""

import subprocess
from pathlib import Path
from typing import Optional

from literature_manager.utils import normalize_whitespace
from literature_manager.extractors.exceptions import CorruptedPDFError

_PDFTOTEXT_BIN = "/usr/bin/pdftotext"
_PDFTOTEXT_TIMEOUT = 30  # seconds, per file


def _try_pdfplumber(pdf_path: Path, max_pages: int) -> tuple[bool, Optional[str]]:
    """Read via pdfplumber (pdfminer). Returns (opened, text). Never raises.

    ``opened`` is True if the file opened and reported >=1 page. ``text`` is the
    extracted text, or None if the file opened but yielded no text.
    """
    try:
        import pdfplumber

        with pdfplumber.open(pdf_path) as pdf:
            n = len(pdf.pages)
            if n == 0:
                return False, None
            parts = []
            for i in range(min(max_pages, n)):
                try:
                    t = pdf.pages[i].extract_text()
                except Exception:
                    t = None
                if t:
                    parts.append(t)
            return True, (" ".join(parts) if parts else None)
    except Exception:
        return False, None


def _try_pypdfium2(pdf_path: Path, max_pages: int) -> tuple[bool, Optional[str]]:
    """Read via pypdfium2 (PDFium). Returns (opened, text). Never raises.

    PDFium is C-backed, so page/textpage/document handles are closed explicitly
    to avoid leaks in the long-lived watcher process.
    """
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(pdf_path))
        try:
            n = len(doc)
            if n == 0:
                return False, None
            parts = []
            for i in range(min(max_pages, n)):
                page = doc[i]
                textpage = page.get_textpage()
                t = textpage.get_text_range()
                textpage.close()
                page.close()
                if t and t.strip():
                    parts.append(t)
            return True, (" ".join(parts) if parts else None)
        finally:
            doc.close()
    except Exception:
        return False, None


def _try_pdftotext(pdf_path: Path, max_pages: int) -> tuple[bool, Optional[str]]:
    """Read via poppler's ``pdftotext`` subprocess. Returns (opened, text).

    Most tolerant reader; used last. Never raises — a timeout or nonzero exit
    returns (False, None).
    """
    try:
        result = subprocess.run(
            [_PDFTOTEXT_BIN, "-l", str(max_pages), str(pdf_path), "-"],
            capture_output=True,
            timeout=_PDFTOTEXT_TIMEOUT,
        )
        if result.returncode != 0:
            return False, None
        text = result.stdout.decode("utf-8", errors="replace").strip()
        return True, (text or None)
    except Exception:
        return False, None


_READERS = (_try_pdfplumber, _try_pypdfium2, _try_pdftotext)


def _read_pdf_text(pdf_path: Path, max_pages: int = 3) -> tuple[bool, Optional[str]]:
    """Try every available reader until one opens the PDF.

    Readers are tried in order (pdfplumber, pypdfium2, pdftotext) so normal files
    keep pdfplumber's layout-aware text and speed; the fallbacks only run when an
    earlier reader fails.

    Returns (opened, text):
      - opened=True  -> at least one reader opened the file (>=1 page). ``text``
                        is the first non-empty text found, or None if the file
                        opened everywhere but yielded no text (scanned image ->
                        NOT an error).
      - opened=False -> ALL readers failed to open the file. Caller treats this
                        as corrupt/unreadable. ``text`` is None.
    """
    opened_anywhere = False
    for reader in _READERS:
        opened, text = reader(pdf_path, max_pages)
        if opened:
            opened_anywhere = True
            if text:
                return True, text
            # opened but no text — a later reader may still pull text
    if opened_anywhere:
        return True, None
    return False, None


def extract_text_from_pdf(pdf_path: Path, max_pages: int = 3) -> Optional[str]:
    """
    Extract text from PDF file.

    Args:
        pdf_path: Path to PDF file
        max_pages: Maximum number of pages to extract (default: 3)

    Returns:
        Extracted text as string, or None if no text found (scanned images)

    Raises:
        CorruptedPDFError: If no reader can open the PDF
    """
    opened, text = _read_pdf_text(pdf_path, max_pages)
    if not opened:
        raise CorruptedPDFError(
            "All PDF readers failed (pdfplumber, pypdfium2, pdftotext)",
            pdf_path=pdf_path,
            method="text_extraction",
        )
    if text is None:
        # Opened but no text - might be scanned images (not an error)
        return None
    return normalize_whitespace(text)


def truncate_text_for_llm(text: str, max_chars: int = 16000) -> str:
    """
    Truncate text to fit within LLM token limits.

    Approximately 4 chars = 1 token, so 16000 chars ≈ 4000 tokens

    Args:
        text: Text to truncate
        max_chars: Maximum characters to keep

    Returns:
        Truncated text
    """
    if len(text) <= max_chars:
        return text

    # Truncate and add indicator
    return text[:max_chars] + "\n\n[... text truncated ...]"


def is_pdf_readable(pdf_path: Path) -> tuple[bool, Optional[str]]:
    """
    Check if PDF is readable before expensive metadata extraction.

    Fast early gate: a PDF is readable if ANY reader (pdfplumber, pypdfium2,
    pdftotext) can open it and report at least one page. A file that opens but
    yields no text is still "readable" — that's the scanned-image case, handled
    downstream as None text, not corruption. Only when every reader fails to open
    the file is it declared unreadable.

    Args:
        pdf_path: Path to PDF file

    Returns:
        Tuple of (is_readable, error_reason)
        - is_readable: True if any reader can open the PDF
        - error_reason: None if readable, "all_readers_failed" otherwise
    """
    opened, _text = _read_pdf_text(pdf_path, max_pages=1)
    if not opened:
        return False, "all_readers_failed"
    return True, None
