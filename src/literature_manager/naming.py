"""File naming logic for literature PDFs."""

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from literature_manager.utils import sanitize_filename


def format_authors(authors: List[str]) -> str:
    """
    Format author list for filename.

    Rules:
    - 0 authors: "Unknown"
    - 1 author: "Smith"
    - 2 authors: "Smith & Jones"
    - 3+ authors: "Smith et al."

    Args:
        authors: List of author names

    Returns:
        Formatted author string
    """
    if not authors or len(authors) == 0:
        return "Unknown"

    if len(authors) == 1:
        # Extract just last name
        name = authors[0]
        if "," in name:
            return name.split(",")[0].strip()
        else:
            parts = name.split()
            return parts[-1] if parts else name

    elif len(authors) == 2:
        # Two authors: "Smith & Jones"
        names = []
        for author in authors:
            if "," in author:
                last = author.split(",")[0].strip()
            else:
                parts = author.split()
                last = parts[-1] if parts else author
            names.append(last)
        return f"{names[0]} & {names[1]}"

    else:
        # Three or more: "Smith et al."
        author = authors[0]
        if "," in author:
            last = author.split(",")[0].strip()
        else:
            parts = author.split()
            last = parts[-1] if parts else author
        return f"{last} et al."


def shorten_title(title: str, max_words: int = 8) -> str:
    """
    Shorten title to max_words, breaking at natural points.

    Args:
        title: Full title
        max_words: Maximum number of words

    Returns:
        Shortened title in title case
    """
    if not title:
        return "Untitled"

    # Split into words
    words = title.split()

    if len(words) <= max_words:
        return title.title()

    # Find natural break points (punctuation)
    break_chars = [":", "-", "—", ",", ";"]

    # Check if any break point exists within max_words
    for i in range(max_words, 0, -1):
        word = words[i - 1] if i <= len(words) else ""
        if any(char in word for char in break_chars):
            # Break here
            shortened = " ".join(words[:i])
            # Remove trailing punctuation
            shortened = re.sub(r"[:\-—,;]+$", "", shortened)
            return shortened.title()

    # No natural break point, just truncate
    shortened = " ".join(words[:max_words])
    return shortened.title()


def generate_filename(metadata: Dict, max_length: int = 200) -> str:
    """
    Generate standardized filename from metadata.

    Format: "Author et al., Year - Short Title.pdf"

    Args:
        metadata: Metadata dict with title, authors, year
        max_length: Maximum filename length

    Returns:
        Sanitized filename string
    """
    # Format authors
    authors = metadata.get("authors", [])
    author_str = format_authors(authors)

    # Get year
    year = metadata.get("year")
    if not year:
        year = datetime.now().year

    # Get title (use short_title if available from LLM, otherwise shorten)
    if metadata.get("short_title"):
        title_str = metadata["short_title"]
    else:
        title = metadata.get("title", "Untitled")
        title_str = shorten_title(title)

    # Construct filename
    filename = f"{author_str}, {year} - {title_str}.pdf"

    # Sanitize for filesystem
    filename = sanitize_filename(filename, max_length)

    return filename


def resolve_duplicate_filename(dest_dir: Path, filename: str) -> Path:
    """
    Resolve duplicate filenames by appending (2), (3), etc.

    Args:
        dest_dir: Destination directory
        filename: Desired filename

    Returns:
        Unique filepath
    """
    filepath = dest_dir / filename

    if not filepath.exists():
        return filepath

    # File exists, find unique name
    name, ext = filename.rsplit(".", 1) if "." in filename else (filename, "")

    counter = 2
    while True:
        new_filename = f"{name} ({counter}).{ext}" if ext else f"{name} ({counter})"
        new_filepath = dest_dir / new_filename

        if not new_filepath.exists():
            return new_filepath

        counter += 1

        # Safety check to avoid infinite loop
        if counter > 100:
            # Use timestamp as fallback
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            fallback_filename = (
                f"{name}_{timestamp}.{ext}" if ext else f"{name}_{timestamp}"
            )
            return dest_dir / fallback_filename
