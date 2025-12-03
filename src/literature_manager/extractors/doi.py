"""DOI extraction and CrossRef API lookup."""

import re
import time
from pathlib import Path
from typing import Dict, Optional

import PyPDF2
import pdfplumber
import requests

from literature_manager.utils import extract_doi_from_text, normalize_whitespace
from literature_manager.extractors.exceptions import CorruptedPDFError, NetworkError


def _retry_request(url: str, headers: dict, max_retries: int = 3, base_delay: float = 1.0):
    """
    Make HTTP request with exponential backoff retry.

    Args:
        url: URL to request
        headers: Request headers
        max_retries: Maximum number of retry attempts
        base_delay: Base delay in seconds (exponentially increased)

    Returns:
        Response object

    Raises:
        NetworkError: If all retries fail
    """
    last_error = None

    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=10)

            # Success or client error (don't retry 4xx except 429)
            if response.status_code < 500 and response.status_code != 429:
                return response

            # Server error or rate limit - retry
            last_error = f"HTTP {response.status_code}"

        except requests.Timeout:
            last_error = "timeout"
        except requests.ConnectionError as e:
            last_error = f"connection: {e}"

        # Exponential backoff (skip on last attempt)
        if attempt < max_retries - 1:
            delay = base_delay * (2 ** attempt)
            time.sleep(delay)

    raise NetworkError(
        f"CrossRef API failed after {max_retries} retries: {last_error}",
        method="doi_lookup"
    )


def _is_valid_metadata(metadata: Dict) -> bool:
    """
    Validate metadata quality to reject bad extractions.

    Args:
        metadata: Metadata dict to validate

    Returns:
        True if metadata appears valid, False otherwise
    """
    title = metadata.get("title", "").lower()

    # Reject obvious bad titles
    bad_titles = [
        "acknowledgement",
        "acknowledgements",
        "references",
        "bibliography",
        "table of contents",
        "contents",
        "index",
        "appendix",
        "supplementary",
        "erratum",
        "corrigendum",
        "retraction",
        "front matter",
        "back matter",
    ]

    for bad in bad_titles:
        if bad in title and len(title) < 50:  # Short titles matching these patterns
            return False

    # Reject if title is just numbers or very short
    if len(title) < 10 or title.replace(".", "").replace(" ", "").isdigit():
        return False

    # Must have at least a title
    if not metadata.get("title"):
        return False

    return True


def extract_doi_from_pdf(pdf_path: Path) -> Optional[str]:
    """
    Extract DOI from PDF file.

    Tries multiple methods:
    1. PDF metadata fields
    2. First page text
    3. Full text (if first page fails)

    Args:
        pdf_path: Path to PDF file

    Returns:
        DOI string if found, None otherwise
    """
    # Method 1: Try PDF metadata
    try:
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            metadata = reader.metadata

            if metadata:
                # Check common DOI fields
                for field in ["/doi", "/DOI", "/Subject", "/Keywords"]:
                    if field in metadata and metadata[field]:
                        doi = extract_doi_from_text(str(metadata[field]))
                        if doi:
                            return doi
    except Exception:
        pass  # Metadata extraction failed, continue to text extraction

    # Method 2: Try first page text
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if len(pdf.pages) > 0:
                first_page_text = pdf.pages[0].extract_text()
                if first_page_text:
                    doi = extract_doi_from_text(first_page_text)
                    if doi:
                        return doi

                # Method 3: Try first 3 pages if first page failed
                if len(pdf.pages) > 1:
                    text = ""
                    for i in range(min(3, len(pdf.pages))):
                        page_text = pdf.pages[i].extract_text()
                        if page_text:
                            text += page_text + " "

                    doi = extract_doi_from_text(text)
                    if doi:
                        return doi
    except Exception:
        pass  # Text extraction failed

    return None


def lookup_doi_metadata(doi: str, email: Optional[str] = None) -> Optional[Dict]:
    """
    Look up metadata from CrossRef API using DOI.

    Args:
        doi: DOI string
        email: Email for polite pool (optional but recommended)

    Returns:
        Metadata dict with title, authors, year, abstract, keywords or None
    """
    url = f"https://api.crossref.org/works/{doi}"

    headers = {"User-Agent": f"LiteratureManager/0.1 (mailto:{email or 'unknown'})"}

    try:
        response = _retry_request(url, headers)

        if response.status_code == 200:
            data = response.json()
            message = data.get("message", {})

            # Extract metadata
            metadata = {
                "doi": doi,
                "title": "",
                "authors": [],
                "year": None,
                "abstract": None,
                "keywords": [],
            }

            # Title
            titles = message.get("title", [])
            if titles:
                metadata["title"] = normalize_whitespace(titles[0])

            # Authors
            authors_data = message.get("author", [])
            authors = []
            for author in authors_data:
                family = author.get("family", "")
                given = author.get("given", "")

                if family:
                    # Normalize capitalization (CrossRef sometimes returns all caps)
                    family = family.title()
                    # Format as "Last, F."
                    if given:
                        initial = given[0].upper() if given else ""
                        authors.append(f"{family}, {initial}.")
                    else:
                        authors.append(family)

            metadata["authors"] = authors

            # Year
            published = message.get("published-print") or message.get("published-online")
            if published and "date-parts" in published:
                date_parts = published["date-parts"][0]
                if date_parts:
                    metadata["year"] = date_parts[0]

            # Abstract (if available)
            abstract = message.get("abstract")
            if abstract:
                # CrossRef abstracts often have XML/HTML tags
                abstract = re.sub(r"<[^>]+>", "", abstract)
                metadata["abstract"] = normalize_whitespace(abstract)

            # Keywords/subjects
            subjects = message.get("subject", [])
            if subjects:
                metadata["keywords"] = subjects

            # Validate metadata quality
            if not _is_valid_metadata(metadata):
                return None

            return metadata

        elif response.status_code == 404:
            return None  # DOI not found - this is OK
        else:
            # Other client errors (4xx) that weren't retried
            raise NetworkError(
                f"CrossRef API error: {response.status_code}",
                status_code=response.status_code,
                method="doi_lookup"
            )

    except NetworkError:
        # Re-raise our custom exceptions (including from _retry_request)
        raise
    except Exception as e:
        # Unknown error - still raise NetworkError for routing
        raise NetworkError(
            f"CrossRef API error: {type(e).__name__}: {e}",
            method="doi_lookup"
        )


def extract_with_doi(pdf_path: Path, email: Optional[str] = None) -> Optional[Dict]:
    """
    Extract metadata using DOI + CrossRef lookup.

    Args:
        pdf_path: Path to PDF file
        email: Email for CrossRef polite pool

    Returns:
        Metadata dict if successful, None otherwise
    """
    # Extract DOI
    doi = extract_doi_from_pdf(pdf_path)

    if not doi:
        return None

    # Look up metadata
    metadata = lookup_doi_metadata(doi, email)

    if metadata:
        metadata["extraction_method"] = "doi_lookup"
        metadata["extraction_confidence"] = 0.95  # High confidence for DOI lookups

    return metadata
