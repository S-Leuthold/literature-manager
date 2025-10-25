#!/usr/bin/env python3
"""
Copy all PDFs from Zotero storage to literature-manager inbox.

Simple script that:
1. Finds all PDFs in ~/Zotero/storage/
2. Copies them to inbox/
3. Lets literature-manager watch mode handle the rest

Run with: python3 copy_zotero_to_inbox.py [--dry-run]
"""

import shutil
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description='Copy PDFs from Zotero storage to literature-manager inbox'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview what would be copied without actually copying files'
    )

    args = parser.parse_args()

    # Paths
    zotero_storage = Path.home() / 'Zotero' / 'storage'
    inbox = Path.home() / 'Desktop' / 'workshop' / 'library' / 'literature' / 'inbox'

    # Create inbox if it doesn't exist
    if not args.dry_run:
        inbox.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("ZOTERO → INBOX MIGRATION")
    print("=" * 70)
    print()

    if args.dry_run:
        print("⚠️  DRY RUN MODE - No files will be copied\n")

    print(f"Source: {zotero_storage}")
    print(f"Destination: {inbox}")
    print()

    # Find all PDFs in Zotero storage
    pdfs = list(zotero_storage.rglob('*.pdf'))

    print(f"Found {len(pdfs)} PDFs in Zotero storage")
    print()

    # Copy each PDF
    copied = 0
    skipped = 0
    errors = 0

    for pdf_path in pdfs:
        dest_path = inbox / pdf_path.name

        # Handle duplicates
        if dest_path.exists():
            # Add Zotero key to filename to avoid collisions
            zotero_key = pdf_path.parent.name
            dest_path = inbox / f"{pdf_path.stem}_{zotero_key}.pdf"

        try:
            if not args.dry_run:
                shutil.copy2(pdf_path, dest_path)

            copied += 1

            if copied <= 10 or args.dry_run:
                print(f"  ✓ {pdf_path.name[:65]}...")
            elif copied % 50 == 0:
                print(f"  ... {copied} PDFs copied so far ...")

        except Exception as e:
            print(f"  ✗ Error copying {pdf_path.name}: {e}")
            errors += 1

    # Summary
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"PDFs found: {len(pdfs)}")
    print(f"Copied: {copied}")
    print(f"Errors: {errors}")
    print()

    if args.dry_run:
        print("⚠️  This was a DRY RUN - run without --dry-run to copy files")
    else:
        print("✅ PDFs copied to inbox!")
        print()
        print("Next step:")
        print("  cd /Users/samleuthold/Desktop/workshop/library/literature")
        print("  literature-manager watch")

    print("=" * 70)


if __name__ == '__main__':
    main()
