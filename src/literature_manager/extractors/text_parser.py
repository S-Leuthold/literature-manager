"""Text extraction from PDFs."""

from pathlib import Path
from typing import Optional

import pdfplumber

from literature_manager.utils import normalize_whitespace
from literature_manager.extractors.exceptions import CorruptedPDFError


def extract_text_from_pdf(pdf_path: Path, max_pages: int = 3) -> Optional[str]:
    """
    Extract text from PDF file.

    Args:
        pdf_path: Path to PDF file
        max_pages: Maximum number of pages to extract (default: 3)

    Returns:
        Extracted text as string, or None if no text found (scanned images)

    Raises:
        CorruptedPDFError: If PDF is malformed or unreadable
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if len(pdf.pages) == 0:
                raise CorruptedPDFError(
                    "PDF has zero pages",
                    pdf_path=pdf_path,
                    method="text_extraction"
                )

            # Extract text from first N pages
            text_parts = []
            for i in range(min(max_pages, len(pdf.pages))):
                page_text = pdf.pages[i].extract_text()
                if page_text:
                    text_parts.append(page_text)

            if not text_parts:
                # No text extracted - might be scanned images (not an error)
                return None

            full_text = " ".join(text_parts)
            return normalize_whitespace(full_text)

    except CorruptedPDFError:
        # Re-raise our custom exceptions
        raise
    except Exception as e:
        # Catch pdfplumber/pdfminer exceptions
        raise CorruptedPDFError(
            f"Failed to read PDF: {type(e).__name__}: {e}",
            pdf_path=pdf_path,
            method="text_extraction"
        )


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

    Fast check that opens PDF and tests first page extraction. This catches
    corrupted PDFs early before attempting expensive DOI lookup or LLM processing.

    Args:
        pdf_path: Path to PDF file

    Returns:
        Tuple of (is_readable, error_reason)
        - is_readable: True if PDF can be opened and has content
        - error_reason: None if readable, string describing issue otherwise
          Possible reasons: "zero_pages", "extraction_failed: <Exception>",
          "open_failed: <Exception>"
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if len(pdf.pages) == 0:
                return False, "zero_pages"

            # Test if we can extract from first page
            try:
                _ = pdf.pages[0].extract_text()
                return True, None
            except Exception as e:
                return False, f"extraction_failed: {type(e).__name__}"

    except Exception as e:
        return False, f"open_failed: {type(e).__name__}"
