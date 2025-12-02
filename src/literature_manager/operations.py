"""File operations, duplicate detection, logging, and indexing."""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from slugify import slugify

from literature_manager.config import Config
from literature_manager.naming import generate_filename, resolve_duplicate_filename
from literature_manager.utils import compute_file_hash, fuzzy_match_score


def determine_destination(
    metadata: Dict, topics: List[str], confidence: float, config: Config
) -> Tuple[Path, List[Path]]:
    """
    Determine where to file the paper with multi-topic support.

    Option A: File lives in by-topic/{first-topic}/, with symlinks to other topics.
    Recent folder gets a copy (deleted after 3 days).

    Args:
        metadata: Paper metadata
        topics: List of suggested topics (1-3 topics, first is primary)
        confidence: Topic confidence (0.85 = high confidence from LLM)
        config: Configuration object

    Returns:
        Tuple of (primary_destination, secondary_destinations_for_symlinks)
    """
    threshold = config.get("confidence_threshold", 0.85)

    # If we have topics with high confidence, file to by-topic
    if topics and confidence >= threshold:
        # Primary location: by-topic/{first-topic}/
        primary_topic = topics[0]
        topic_slug = slugify(primary_topic)
        primary_dest = config.by_topic_path / topic_slug

        # Secondary locations: symlinks to other topics
        secondary_dests = []
        for topic in topics[1:]:  # Skip first topic (already primary)
            topic_slug = slugify(topic)
            topic_dir = config.by_topic_path / topic_slug
            secondary_dests.append(topic_dir)
    else:
        # Low confidence or no topics -> recent only
        primary_dest = config.recent_path
        secondary_dests = []

    return primary_dest, secondary_dests


def move_and_rename_file(
    source: Path, dest_dir: Path, filename: str, create_symlinks: List[Path] = None
) -> Path:
    """
    Move and rename file, creating symlinks if needed.

    Args:
        source: Source file path
        dest_dir: Destination directory
        filename: New filename
        create_symlinks: List of additional directories to create symlinks in

    Returns:
        Final filepath
    """
    # Ensure destination directory exists
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Resolve duplicate filename
    dest_path = resolve_duplicate_filename(dest_dir, filename)

    # Move file
    shutil.move(str(source), str(dest_path))

    # Create symlinks if specified
    if create_symlinks:
        for symlink_dir in create_symlinks:
            symlink_dir.mkdir(parents=True, exist_ok=True)
            symlink_path = symlink_dir / dest_path.name

            # Create symlink (relative path for portability)
            try:
                os.symlink(dest_path, symlink_path)
            except FileExistsError:
                pass  # Symlink already exists
            except Exception as e:
                print(f"Warning: Failed to create symlink: {e}")

    return dest_path


def copy_to_recent(source_path: Path, recent_dir: Path) -> Optional[Path]:
    """
    Copy file to recent directory (for 3-day window).

    Args:
        source_path: Path to file (after being moved to by-topic)
        recent_dir: Recent directory path

    Returns:
        Path to copy in recent/, or None if failed
    """
    try:
        recent_dir.mkdir(parents=True, exist_ok=True)
        dest_path = recent_dir / source_path.name

        # Don't copy if already in recent
        if source_path.parent == recent_dir:
            return source_path

        # Copy file (don't move, since it's already in by-topic)
        shutil.copy2(str(source_path), str(dest_path))
        return dest_path

    except Exception as e:
        print(f"Warning: Failed to copy to recent: {e}")
        return None


def load_index(index_path: Path) -> Dict:
    """
    Load literature index from JSON.

    Args:
        index_path: Path to index file

    Returns:
        Index dictionary
    """
    if not index_path.exists():
        return {}

    try:
        with open(index_path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_index(index: Dict, index_path: Path):
    """
    Save literature index to JSON.

    Args:
        index: Index dictionary
        index_path: Path to index file
    """
    index_path.parent.mkdir(parents=True, exist_ok=True)

    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)


def update_index(metadata: Dict, filepath: Path, config: Config):
    """
    Update literature index with new entry.

    Args:
        metadata: Paper metadata
        filepath: Final filepath of paper
        config: Configuration object
    """
    index = load_index(config.index_path)

    # Create entry
    entry = {
        "filepath": str(filepath.relative_to(config.workshop_root)),
        "original_filename": metadata.get("original_filename", ""),
        "doi": metadata.get("doi", ""),
        "title": metadata.get("title", ""),
        "authors": metadata.get("authors", []),
        "year": metadata.get("year"),
        "abstract": metadata.get("abstract"),
        "keywords": metadata.get("keywords", []),
        "topic": metadata.get("matched_topic", ""),
        "confidence": metadata.get("topic_confidence", 0.0),
        "extraction_method": metadata.get("extraction_method", ""),
        "extraction_confidence": metadata.get("extraction_confidence", 0.0),
        "processed_date": datetime.now().isoformat(),
        "file_hash": compute_file_hash(filepath),
    }

    # Use file hash as key (unique identifier)
    index[entry["file_hash"]] = entry

    save_index(index, config.index_path)


def log_action(
    action: str, metadata: Dict, source: Path, destination: Path, config: Config, **kwargs
):
    """
    Log processing action to log file.

    Args:
        action: Action type (PROCESSED, REVIEW_NEEDED, ERROR, etc.)
        metadata: Paper metadata
        source: Source filepath
        destination: Destination filepath
        config: Configuration object
        **kwargs: Additional info to log
    """
    log_path = config.log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Generate filename from metadata
    from literature_manager.naming import generate_filename

    display_name = generate_filename(metadata)

    log_entry = f"{timestamp} | {action} | {display_name}\n"
    log_entry += f"  → Source: {source.name}\n"
    log_entry += f"  → Destination: {destination.relative_to(config.workshop_root)}\n"

    if "confidence" in kwargs:
        log_entry += f"  → Confidence: {kwargs['confidence']:.0%}\n"
    if "method" in kwargs:
        log_entry += f"  → Method: {kwargs['method']}\n"
    if "topic" in kwargs:
        log_entry += f"  → Topic: {kwargs['topic']}\n"
    if "reason" in kwargs:
        log_entry += f"  → Reason: {kwargs['reason']}\n"

    # Enhanced error details for ERROR actions
    if action == "ERROR" and "errors" in metadata and metadata["errors"]:
        # Show what methods were attempted and why they failed (first 3 errors)
        error_details = "; ".join(metadata["errors"][:3])
        log_entry += f"  → Details: {error_details}\n"

    log_entry += "\n"

    with open(log_path, "a") as f:
        f.write(log_entry)


def check_duplicate_by_doi(doi: str, index: Dict) -> Optional[str]:
    """
    Check if paper with DOI already exists in index.

    Args:
        doi: DOI string
        index: Current index

    Returns:
        Filepath of duplicate if found, None otherwise
    """
    if not doi:
        return None

    for entry in index.values():
        if entry.get("doi") == doi and doi:
            return entry.get("filepath")

    return None


def check_duplicate_by_title(title: str, index: Dict, threshold: float = 0.90) -> Optional[str]:
    """
    Check if paper with similar title exists in index.

    Args:
        title: Paper title
        index: Current index
        threshold: Similarity threshold

    Returns:
        Filepath of duplicate if found, None otherwise
    """
    if not title:
        return None

    for entry in index.values():
        existing_title = entry.get("title", "")
        if existing_title:
            similarity = fuzzy_match_score(title, existing_title)
            if similarity >= threshold:
                return entry.get("filepath")

    return None


def check_duplicate(metadata: Dict, config: Config) -> Optional[Tuple[str, str]]:
    """
    Check if paper is a duplicate.

    Args:
        metadata: Paper metadata
        config: Configuration object

    Returns:
        Tuple of (method, filepath) if duplicate found, None otherwise
    """
    index = load_index(config.index_path)

    # Check by DOI first (most reliable)
    doi = metadata.get("doi")
    if doi:
        duplicate = check_duplicate_by_doi(doi, index)
        if duplicate:
            return ("doi", duplicate)

    # Check by title similarity
    title = metadata.get("title")
    if title:
        duplicate = check_duplicate_by_title(title, index)
        if duplicate:
            return ("title", duplicate)

    return None


def handle_duplicate(
    new_pdf: Path, existing_path: str, action: str = "skip", config: Config = None
) -> bool:
    """
    Handle duplicate paper detection.

    Always deletes the duplicate PDF from inbox and skips LLM processing.

    Args:
        new_pdf: Path to new PDF (will be deleted)
        existing_path: Path to existing PDF (relative to workshop)
        action: Action to take (ignored, always deletes duplicate)
        config: Configuration object

    Returns:
        False (always skip processing of duplicates)
    """
    # Always delete the duplicate from inbox
    if new_pdf.exists():
        new_pdf.unlink()

    # Skip processing (don't query LLM for duplicates)
    return False
