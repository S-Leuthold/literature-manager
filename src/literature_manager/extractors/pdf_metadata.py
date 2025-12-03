"""PDF metadata extraction from file properties."""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import PyPDF2
from PyPDF2.errors import PdfReadError

from literature_manager.utils import normalize_whitespace


def parse_author_string(author_str: str) -> List[str]:
    """
    Parse author string into list of individual authors.

    Handles various formats:
    - "Smith, John; Jones, Jane"
    - "Smith, J., Jones, K."
    - "John Smith and Jane Jones"

    Args:
        author_str: Author string from PDF metadata

    Returns:
        List of author names
    """
    if not author_str:
        return []

    # Split by common separators
    separators = [";", " and ", ","]

    authors = [author_str]

    for sep in separators:
        new_authors = []
        for author in authors:
            new_authors.extend([a.strip() for a in author.split(sep) if a.strip()])
        authors = new_authors

    # Format as "Last, F."
    formatted = []
    for author in authors:
        author = author.strip()

        # Skip if already formatted or very short
        if len(author) < 2:
            continue

        # Try to extract last name and initial
        if "," in author:
            # Already has comma, likely "Last, First" format
            parts = author.split(",", 1)
            last = parts[0].strip()
            first = parts[1].strip() if len(parts) > 1 else ""
            initial = first[0] if first else ""
            formatted.append(f"{last}, {initial}." if initial else last)
        else:
            # Assume "First Last" format
            parts = author.split()
            if len(parts) >= 2:
                last = parts[-1]
                first = parts[0]
                formatted.append(f"{last}, {first[0]}.")
            else:
                formatted.append(author)

    return formatted[:10]  # Limit to 10 authors


def extract_year_from_date(date_str: str) -> Optional[int]:
    """
    Extract year from date string.

    Args:
        date_str: Date string from PDF metadata

    Returns:
        Year as integer, or None
    """
    if not date_str:
        return None

    # Try common date formats
    patterns = [
        r"(\d{4})",  # Just a year
        r"D:(\d{4})",  # PDF date format: D:20240101...
    ]

    for pattern in patterns:
        match = re.search(pattern, str(date_str))
        if match:
            year = int(match.group(1))
            # Sanity check
            if 1900 <= year <= datetime.now().year + 1:
                return year

    return None


def extract_pdf_metadata(pdf_path: Path) -> Optional[Dict]:
    """
    Extract metadata from PDF file properties.

    Args:
        pdf_path: Path to PDF file

    Returns:
        Metadata dict if sufficient info found, None otherwise
    """
    try:
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            metadata = reader.metadata

            if not metadata:
                return None

            # Extract fields
            title = metadata.get("/Title", "")
            author = metadata.get("/Author", "")
            subject = metadata.get("/Subject", "")
            keywords = metadata.get("/Keywords", "")
            creation_date = metadata.get("/CreationDate", "")

            # Must have at least title or author
            if not title and not author:
                return None

            # Parse data
            result = {
                "title": normalize_whitespace(title) if title else "",
                "authors": parse_author_string(author) if author else [],
                "year": extract_year_from_date(creation_date),
                "abstract": subject if subject else None,
                "keywords": [k.strip() for k in keywords.split(",") if k.strip()]
                if keywords
                else [],
                "extraction_method": "pdf_metadata",
                "extraction_confidence": 0.70,  # Medium confidence
            }

            # Lower confidence if missing key fields
            if not result["title"]:
                result["extraction_confidence"] = 0.50
            if not result["authors"]:
                result["extraction_confidence"] = min(result["extraction_confidence"], 0.60)
            if not result["year"]:
                result["year"] = datetime.now().year  # Use current year as fallback

            # Must have title
            if not result["title"]:
                return None

            return result

    except PdfReadError as e:
        logging.debug(f"PDF read error for {pdf_path.name}: {e}")
        return None
    except Exception as e:
        logging.warning(f"Unexpected error reading PDF metadata from {pdf_path.name}: {type(e).__name__}: {e}")
        return None
