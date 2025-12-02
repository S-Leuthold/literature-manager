"""Custom exceptions for metadata extraction."""

from pathlib import Path
from typing import Optional


class ExtractionError(Exception):
    """Base exception for all extraction errors."""

    def __init__(self, message: str, pdf_path: Optional[Path] = None, method: Optional[str] = None):
        """
        Initialize extraction error with context.

        Args:
            message: Human-readable error description
            pdf_path: Path to PDF being processed (if available)
            method: Extraction method that failed (e.g., "doi_lookup", "llm_parsing")
        """
        self.message = message
        self.pdf_path = str(pdf_path) if pdf_path else None
        self.method = method
        super().__init__(message)

    def __str__(self):
        parts = [self.message]
        if self.method:
            parts.append(f"(method: {self.method})")
        if self.pdf_path:
            parts.append(f"(file: {Path(self.pdf_path).name})")
        return " ".join(parts)


class CorruptedPDFError(ExtractionError):
    """
    PDF file is corrupted or unreadable.

    Raised when:
    - PDF can't be opened
    - PDF has zero pages
    - PDF structure is malformed (can't extract text)

    Routing: Move to corrupted/ folder, stop processing immediately.
    """
    pass


class MetadataNotFoundError(ExtractionError):
    """
    Metadata could not be extracted from valid PDF.

    Raised when:
    - DOI not found in PDF
    - PDF metadata fields empty
    - LLM extraction returns no results

    This is NOT an error - it's expected for some PDFs.
    Routing: Continue to next extraction method.
    """
    pass


class NetworkError(ExtractionError):
    """
    Network-related error during metadata lookup.

    Raised when:
    - CrossRef API timeout
    - Rate limit exceeded (429)
    - Connection failure
    - Server error (5xx)

    Note: 404 (DOI not found) is NOT a NetworkError - return None instead.
    Routing: Log error, DON'T move file (transient failure, may succeed on retry).
    """

    def __init__(self, message: str, status_code: Optional[int] = None, **kwargs):
        """
        Initialize with HTTP status code if applicable.

        Args:
            message: Error description
            status_code: HTTP status code (e.g., 429 for rate limit, 503 for timeout)
            **kwargs: Additional context (pdf_path, method)
        """
        self.status_code = status_code
        super().__init__(message, **kwargs)


class LLMError(ExtractionError):
    """
    LLM API or parsing error.

    Raised when:
    - Anthropic API failure (auth, rate limit, timeout)
    - Invalid JSON response from LLM
    - Empty response from LLM

    Routing: Log error, DON'T move file (transient failure or fixable API issue).
    """

    def __init__(self, message: str, api_error: Optional[str] = None, **kwargs):
        """
        Initialize with API error details if available.

        Args:
            message: Error description
            api_error: Raw error from API (for debugging)
            **kwargs: Additional context (pdf_path, method)
        """
        self.api_error = api_error
        super().__init__(message, **kwargs)


class ConfigurationError(ExtractionError):
    """
    Missing or invalid configuration.

    Raised when:
    - API key missing
    - Config file invalid
    - Required paths don't exist

    Routing: Fail fast - user needs to fix config before processing.
    """
    pass
