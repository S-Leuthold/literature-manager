#!/usr/bin/env python3
"""
Migrate PDFs from Zotero storage to organized literature library.

This script:
1. Finds PDFs in ~/Zotero/storage/ that aren't already in the literature library
2. Extracts metadata (DOI from Zotero database)
3. Processes through literature-manager (gets topics, summary)
4. Moves to appropriate by-topic/ folder
5. Updates literature index

Run with: python3 migrate_zotero_pdfs.py [--dry-run]
"""

import sqlite3
import json
import shutil
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# Import literature-manager components
import sys
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from literature_manager.config import Config
from literature_manager.extractors.orchestrator import extract_metadata
from literature_manager.naming import generate_filename
from literature_manager.file_organizer import organize_by_topic


class ZoteroMigrator:
    """Migrates PDFs from Zotero storage to literature library."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.config = Config()

        # Paths
        self.zotero_db = Path.home() / 'Zotero' / 'zotero.sqlite'
        self.zotero_storage = Path.home() / 'Zotero' / 'storage'
        self.lit_index_path = Path(__file__).parent / '.literature-index.json'

        # Load existing index
        if self.lit_index_path.exists():
            with open(self.lit_index_path) as f:
                self.lit_index = json.load(f)
        else:
            self.lit_index = {}

        # Stats
        self.stats = {
            'found': 0,
            'already_in_library': 0,
            'no_doi': 0,
            'processing_errors': 0,
            'migrated': 0,
            'skipped': 0
        }

    def get_zotero_pdfs(self) -> List[Tuple[str, Path, Optional[str]]]:
        """
        Get list of PDFs from Zotero storage with their DOIs.

        Returns:
            List of (key, pdf_path, doi) tuples
        """
        conn = sqlite3.connect(self.zotero_db)
        cursor = conn.cursor()

        # Get all PDFs in storage with their parent items and DOIs
        cursor.execute("""
            SELECT
                items.key,
                itemAttachments.path,
                parent_items.itemID,
                doi_values.value as doi
            FROM items
            JOIN itemAttachments ON items.itemID = itemAttachments.itemID
            LEFT JOIN items as parent_items ON itemAttachments.parentItemID = parent_items.itemID
            LEFT JOIN (
                SELECT itemData.itemID, itemDataValues.value
                FROM itemData
                JOIN itemDataValues ON itemData.valueID = itemDataValues.valueID
                JOIN fields ON itemData.fieldID = fields.fieldID
                WHERE fields.fieldName = 'DOI'
            ) as doi_values ON parent_items.itemID = doi_values.itemID
            WHERE itemAttachments.contentType = 'application/pdf'
            AND itemAttachments.path LIKE 'storage:%'
        """)

        results = []
        for key, path, parent_id, doi in cursor.fetchall():
            if path and path.startswith('storage:'):
                filename = path.replace('storage:', '')
                pdf_path = self.zotero_storage / key / filename

                if pdf_path.exists():
                    results.append((key, pdf_path, doi))
                    self.stats['found'] += 1

        conn.close()
        return results

    def is_already_in_library(self, doi: Optional[str], pdf_path: Path) -> bool:
        """Check if paper is already in literature library."""
        if not doi:
            return False

        doi_clean = doi.lower().strip()

        # Check by DOI
        for paper in self.lit_index.values():
            if paper.get('doi', '').lower().strip() == doi_clean:
                return True

        # Check by filename (in case DOI match fails)
        filename = pdf_path.name
        for paper in self.lit_index.values():
            indexed_filename = Path(paper.get('filepath', '')).name
            if indexed_filename == filename:
                return True

        return False

    def migrate_pdf(self, key: str, pdf_path: Path, doi: Optional[str]) -> bool:
        """
        Migrate a single PDF to literature library.

        Returns:
            True if successful, False otherwise
        """
        print(f"\nProcessing: {pdf_path.name}")

        # Check if already in library
        if self.is_already_in_library(doi, pdf_path):
            print(f"  ℹ Already in library, skipping")
            self.stats['already_in_library'] += 1
            return False

        if not doi:
            print(f"  ⚠ No DOI found in Zotero")
            self.stats['no_doi'] += 1
            # Could still try to process via PDF metadata/LLM
            # For now, skip
            return False

        try:
            # Extract metadata using literature-manager
            print(f"  → Extracting metadata...")
            metadata = extract_metadata(pdf_path, self.config)

            if not metadata:
                print(f"  ✗ Failed to extract metadata")
                self.stats['processing_errors'] += 1
                return False

            # Check if we got topics and summary
            if not metadata.get('suggested_topic'):
                print(f"  ⚠ No topics assigned")
                self.stats['processing_errors'] += 1
                return False

            # Generate new filename
            new_filename = generate_filename(metadata)

            # Determine destination
            topics = metadata['suggested_topic'].split('|')
            primary_topic = topics[0]

            if primary_topic == 'needs-review':
                dest_dir = self.config.by_topic_path / 'needs-review'
            else:
                dest_dir = self.config.by_topic_path / primary_topic

            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / new_filename

            # Handle duplicates
            if dest_path.exists():
                counter = 2
                while dest_path.exists():
                    name_without_ext = new_filename.rsplit('.', 1)[0]
                    dest_path = dest_dir / f"{name_without_ext} ({counter}).pdf"
                    counter += 1

            print(f"  → Destination: {primary_topic}/{dest_path.name}")

            if not self.dry_run:
                # Copy (not move) to preserve Zotero storage
                shutil.copy2(pdf_path, dest_path)

                # Update metadata with new path
                metadata['filepath'] = str(dest_path.relative_to(dest_path.parents[3]))  # Relative to library root
                metadata['original_filename'] = pdf_path.name

                # Add to index
                file_hash = metadata.get('file_hash')
                if not file_hash:
                    import hashlib
                    with open(dest_path, 'rb') as f:
                        file_hash = hashlib.sha256(f.read()).hexdigest()
                    metadata['file_hash'] = file_hash

                self.lit_index[file_hash] = metadata

                # Save index
                with open(self.lit_index_path, 'w') as f:
                    json.dump(self.lit_index, f, indent=2)

                # Create symlinks for secondary topics
                if len(topics) > 1:
                    for secondary_topic in topics[1:]:
                        if secondary_topic != 'needs-review':
                            secondary_dir = self.config.by_topic_path / secondary_topic
                            secondary_dir.mkdir(parents=True, exist_ok=True)
                            symlink_path = secondary_dir / new_filename

                            if not symlink_path.exists():
                                symlink_path.symlink_to(dest_path)
                                print(f"  → Symlink: {secondary_topic}/{new_filename}")

            print(f"  ✓ Migrated successfully")
            self.stats['migrated'] += 1
            return True

        except Exception as e:
            print(f"  ✗ Error: {e}")
            self.stats['processing_errors'] += 1
            return False

    def run(self):
        """Run the migration."""
        print("=" * 70)
        print("ZOTERO → LITERATURE LIBRARY MIGRATION")
        print("=" * 70)

        if self.dry_run:
            print("\n⚠️  DRY RUN MODE - No files will be moved\n")

        print(f"\nScanning Zotero storage: {self.zotero_storage}")

        pdfs = self.get_zotero_pdfs()

        print(f"\nFound {len(pdfs)} PDFs in Zotero storage")
        print(f"Current library has {len(self.lit_index)} papers\n")

        # Process each PDF
        for key, pdf_path, doi in pdfs:
            self.migrate_pdf(key, pdf_path, doi)

        # Print summary
        print("\n" + "=" * 70)
        print("MIGRATION SUMMARY")
        print("=" * 70)
        print(f"PDFs found in Zotero: {self.stats['found']}")
        print(f"Already in library: {self.stats['already_in_library']}")
        print(f"No DOI (skipped): {self.stats['no_doi']}")
        print(f"Processing errors: {self.stats['processing_errors']}")
        print(f"Successfully migrated: {self.stats['migrated']}")
        print()

        if self.dry_run:
            print("⚠️  This was a DRY RUN - no files were actually moved")
            print("    Run without --dry-run to perform actual migration")
        else:
            print("✅ Migration complete!")

        print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description='Migrate PDFs from Zotero storage to organized literature library'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview what would be migrated without actually moving files'
    )

    args = parser.parse_args()

    migrator = ZoteroMigrator(dry_run=args.dry_run)
    migrator.run()


if __name__ == '__main__':
    main()
