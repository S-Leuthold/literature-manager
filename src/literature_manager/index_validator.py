"""Index validation and repair utilities."""

import json
import fcntl
from pathlib import Path
from typing import Dict, List, Tuple
from literature_manager.config import Config
from literature_manager.utils import compute_file_hash


def validate_and_repair_index(config: Config, verbose: bool = False) -> Tuple[int, int]:
    """
    Validate index against actual files and repair mismatches.

    Scans library directories for PDFs and ensures index paths are correct.

    Args:
        config: Configuration object
        verbose: Print detailed output

    Returns:
        Tuple of (files_checked, paths_repaired)
    """
    if verbose:
        print("Validating index...")

    # Load index with non-blocking lock
    if not config.index_path.exists():
        if verbose:
            print("  No index found, skipping validation")
        return 0, 0

    try:
        with open(config.index_path, 'r') as f:
            # Try to acquire non-blocking shared lock (multiple readers OK)
            fcntl.flock(f.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            index = json.load(f)
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (BlockingIOError, OSError) as e:
        if verbose:
            print("  ⚠ Index locked by another process, skipping validation")
        return 0, 0

    # Scan all PDF locations
    scan_dirs = [
        config.by_topic_path,
        config.workshop_root / 'library' / 'protocols',
        config.workshop_root / 'library' / 'publications',
    ]

    # Build lookup of indexed files by path for cache checking
    index_by_path = {}
    for entry in index.values():
        if entry.get('filepath'):
            index_by_path[entry['filepath']] = entry

    # Build map of actual files (hash -> path)
    actual_files = {}
    files_scanned = 0
    hashes_computed = 0

    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue

        for pdf_path in scan_dir.rglob('*.pdf'):
            # Skip symlinks (only index real files)
            if pdf_path.is_symlink():
                continue

            files_scanned += 1
            stat = pdf_path.stat()
            rel_path = str(pdf_path.relative_to(config.workshop_root))

            # Check if we have cached hash with matching mtime/size
            cached_entry = index_by_path.get(rel_path)

            if (cached_entry
                and cached_entry.get('file_mtime') == stat.st_mtime
                and cached_entry.get('file_size') == stat.st_size
                and cached_entry.get('file_hash')):
                # Use cached hash (file unchanged)
                file_hash = cached_entry['file_hash']
            else:
                # Compute new hash (file changed or not in index)
                file_hash = compute_file_hash(pdf_path)
                hashes_computed += 1

            actual_files[file_hash] = pdf_path

    # Check index entries against actual files
    repairs_needed = {}

    for file_hash, entry in index.items():
        indexed_path_rel = entry.get('filepath', '')

        if not indexed_path_rel:
            continue

        indexed_path = config.workshop_root / indexed_path_rel

        # Check if file exists at indexed location
        if indexed_path.exists() and not indexed_path.is_symlink():
            # Path is correct
            continue

        # Path is wrong - check if we have this file elsewhere
        if file_hash in actual_files:
            actual_path = actual_files[file_hash]
            new_path_rel = str(actual_path.relative_to(config.workshop_root))

            if new_path_rel != indexed_path_rel:
                repairs_needed[file_hash] = new_path_rel

    # Apply repairs
    if repairs_needed:
        for file_hash, new_path in repairs_needed.items():
            index[file_hash]['filepath'] = new_path

        # Save updated index with exclusive lock
        try:
            with open(config.index_path, 'w') as f:
                # Acquire exclusive lock for writing
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                json.dump(index, f, indent=2)
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

            if verbose:
                print(f"  ✓ Repaired {len(repairs_needed)} path(s)")
        except (BlockingIOError, OSError):
            if verbose:
                print("  ⚠ Could not acquire lock to save repairs, skipping")

    elif verbose:
        print(f"  ✓ Index valid ({files_scanned} files checked)")

    return files_scanned, len(repairs_needed)
