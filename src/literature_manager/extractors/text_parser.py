"""Text extraction from PDFs."""

from pathlib import Path
from typing import Optional

import pdfplumber

from literature_manager.utils import normalize_whitespace


def extract_text_from_pdf(pdf_path: Path, max_pages: int = 3) -> Optional[str]:
    """
    Extract text from PDF file.

    Args:
        pdf_path: Path to PDF file
        max_pages: Maximum number of pages to extract (default: 3)

    Returns:
        Extracted text as string, or None if extraction fails
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if len(pdf.pages) == 0:
                return None

            # Extract text from first N pages
            text_parts = []
            for i in range(min(max_pages, len(pdf.pages))):
                page_text = pdf.pages[i].extract_text()
                if page_text:
                    text_parts.append(page_text)

            if not text_parts:
                return None

            full_text = " ".join(text_parts)
            return normalize_whitespace(full_text)

    except Exception:
        return None


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
