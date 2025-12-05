"""Utility functions for Literature Manager."""

import hashlib
import re
from pathlib import Path
from typing import Optional


def sanitize_filename(filename: str, max_length: int = 200) -> str:
    """
    Sanitize filename for filesystem compatibility.

    Args:
        filename: Original filename
        max_length: Maximum length for filename

    Returns:
        Sanitized filename
    """
    # Replace problematic characters
    replacements = {
        "/": "-",
        "\\": "-",
        ":": " -",
        "*": "",
        "?": "",
        '"': "'",
        "<": "",
        ">": "",
        "|": "-",
    }

    for old, new in replacements.items():
        filename = filename.replace(old, new)

    # Remove any remaining non-printable characters
    filename = "".join(char for char in filename if char.isprintable())

    # Collapse multiple spaces
    filename = re.sub(r"\s+", " ", filename)

    # Trim to max length (preserve extension)
    if len(filename) > max_length:
        if "." in filename:
            name, ext = filename.rsplit(".", 1)
            name = name[: max_length - len(ext) - 1]
            filename = f"{name}.{ext}"
        else:
            filename = filename[:max_length]

    return filename.strip()


def compute_file_hash(filepath: Path) -> str:
    """
    Compute SHA256 hash of file.

    Args:
        filepath: Path to file

    Returns:
        Hex digest of file hash
    """
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def extract_doi_from_text(text: str) -> Optional[str]:
    """
    Extract DOI from text using regex.

    Finds all DOI matches and returns the longest valid one,
    avoiding truncated DOIs that appear in URLs/references.

    Args:
        text: Text to search for DOI

    Returns:
        DOI string if found, None otherwise
    """
    # DOI regex pattern
    doi_pattern = r"10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+"
    matches = re.findall(doi_pattern, text)

    if not matches:
        return None

    # Filter out obviously truncated DOIs (< 15 chars is suspicious)
    # e.g., "10.1073/pnas." (truncated) vs "10.1073/pnas.2217481120" (valid)
    valid_matches = [d for d in matches if len(d) >= 15]

    # If we have valid matches, return the longest one
    if valid_matches:
        return max(valid_matches, key=len)

    # Fallback to longest match if no valid ones found
    return max(matches, key=len)


def normalize_whitespace(text: str) -> str:
    """
    Normalize whitespace in text.

    Args:
        text: Text to normalize

    Returns:
        Text with normalized whitespace
    """
    # Replace multiple whitespace with single space
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fuzzy_match_score(str1: str, str2: str) -> float:
    """
    Calculate similarity score between two strings.

    Uses simple character-based comparison.

    Args:
        str1: First string
        str2: Second string

    Returns:
        Similarity score between 0.0 and 1.0
    """
    from difflib import SequenceMatcher

    return SequenceMatcher(None, str1.lower(), str2.lower()).ratio()
