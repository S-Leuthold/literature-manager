"""Index validation and repair utilities."""

import json
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

    # Load index
    if not config.index_path.exists():
        if verbose:
            print("  No index found, skipping validation")
        return 0, 0

    with open(config.index_path) as f:
        index = json.load(f)

    # Scan all PDF locations
    scan_dirs = [
        config.by_topic_path,
        config.workshop_root / 'library' / 'protocols',
        config.workshop_root / 'library' / 'publications',
    ]

    # Build map of actual files (hash -> path)
    actual_files = {}
    files_scanned = 0

    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue

        for pdf_path in scan_dir.rglob('*.pdf'):
            # Skip symlinks (only index real files)
            if pdf_path.is_symlink():
                continue

            files_scanned += 1

            # Compute hash
            file_hash = compute_file_hash(pdf_path)
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

        # Save updated index
        with open(config.index_path, 'w') as f:
            json.dump(index, f, indent=2)

        if verbose:
            print(f"  ✓ Repaired {len(repairs_needed)} path(s)")

    elif verbose:
        print(f"  ✓ Index valid ({files_scanned} files checked)")

    return files_scanned, len(repairs_needed)
