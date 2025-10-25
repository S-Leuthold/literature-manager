#!/usr/bin/env python3
"""
Migrate academic papers from Downloads to literature-manager inbox.

This script:
1. Identifies academic papers in ~/Downloads (excluding proofs, receipts)
2. Moves them to literature-manager inbox for processing
3. Lists other files for manual review

Run with: python3 migrate_downloads.py [--dry-run]
"""

import shutil
import argparse
from pathlib import Path
from datetime import datetime


def categorize_pdf(pdf_path: Path) -> str:
    """
    Categorize a PDF based on filename patterns.

    Returns: 'academic', 'proof', 'receipt', 'generic_download', 'other'
    """
    name = pdf_path.name
    name_lower = name.lower()

    # Receipts
    if 'receipt' in name_lower or 'citi' in name_lower:
        return 'receipt'

    # Proofs (journal galleys, peer review)
    if 'proof' in name_lower or any(code in name_lower for code in ['erfs-', 'cjss-', 'ejss-']):
        return 'proof'

    # Generic downloads from journals (likely academic)
    if name_lower.startswith('1-s2.0-'):  # ScienceDirect
        return 'generic_download'

    if name_lower.startswith('document'):  # Generic download
        return 'generic_download'

    # Academic papers (recognizable names)
    academic_indicators = [
        'et al',
        'journal',
        'soil',
        'carbon',
        'nitrogen',
        'machine_learning',
        'european j',
        'science',
        '.R1',  # Revision markers
        'preprint'
    ]

    if any(indicator in name_lower for indicator in academic_indicators):
        return 'academic'

    # Everything else
    return 'other'


def main():
    parser = argparse.ArgumentParser(
        description='Migrate academic papers from Downloads to literature-manager inbox'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview what would be moved without actually moving files'
    )

    args = parser.parse_args()

    # Paths
    downloads = Path.home() / 'Downloads'
    inbox = Path.home() / 'Desktop' / 'workshop' / 'workspace' / 'inbox'

    print("=" * 70)
    print("DOWNLOADS → INBOX MIGRATION")
    print("=" * 70)
    print()

    if args.dry_run:
        print("⚠️  DRY RUN MODE - No files will be moved\n")

    # Find all PDFs
    pdfs = list(downloads.glob('*.pdf'))
    print(f"Total PDFs in Downloads: {len(pdfs)}")
    print()

    # Categorize
    categorized = {
        'academic': [],
        'proof': [],
        'receipt': [],
        'generic_download': [],
        'other': []
    }

    for pdf in pdfs:
        category = categorize_pdf(pdf)
        categorized[category].append(pdf)

    # Show breakdown
    print("CATEGORIZATION:")
    print("-" * 70)
    print(f"  Academic papers (named): {len(categorized['academic'])}")
    print(f"  Generic downloads: {len(categorized['generic_download'])}")
    print(f"  Journal proofs: {len(categorized['proof'])}")
    print(f"  Receipts/admin: {len(categorized['receipt'])}")
    print(f"  Other: {len(categorized['other'])}")
    print()

    # Academic papers to migrate
    to_migrate = categorized['academic'] + categorized['generic_download']

    print("=" * 70)
    print(f"MIGRATING {len(to_migrate)} ACADEMIC PAPERS TO INBOX")
    print("=" * 70)
    print()

    if not args.dry_run:
        inbox.mkdir(parents=True, exist_ok=True)

    moved = 0
    errors = 0

    for pdf in to_migrate:
        dest = inbox / pdf.name

        # Handle duplicates in inbox
        if dest.exists():
            # Add timestamp to avoid collision
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            dest = inbox / f"{pdf.stem}_{timestamp}.pdf"

        try:
            if not args.dry_run:
                shutil.move(str(pdf), str(dest))

            moved += 1

            if moved <= 10:
                print(f"  ✓ {pdf.name[:65]}...")
            elif moved % 20 == 0:
                print(f"  ... {moved} papers moved so far ...")

        except Exception as e:
            print(f"  ✗ Error moving {pdf.name}: {e}")
            errors += 1

    # Show what's left for manual review
    print()
    print("=" * 70)
    print("FILES LEFT IN DOWNLOADS FOR REVIEW:")
    print("=" * 70)

    if categorized['proof']:
        print(f"\nJOURNAL PROOFS ({len(categorized['proof'])} files):")
        print("(These might be peer review or your own paper proofs)")
        for pdf in sorted(categorized['proof'], key=lambda p: p.stat().st_mtime, reverse=True):
            mtime = datetime.fromtimestamp(pdf.stat().st_mtime)
            print(f"  {mtime.strftime('%Y-%m-%d')}: {pdf.name}")

    if categorized['receipt']:
        print(f"\nRECEIPTS/ADMIN ({len(categorized['receipt'])} files):")
        for pdf in categorized['receipt']:
            print(f"  {pdf.name}")

    if categorized['other']:
        print(f"\nOTHER ({len(categorized['other'])} files):")
        print("(Review these manually - unclear what they are)")
        for pdf in sorted(categorized['other'], key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
            mtime = datetime.fromtimestamp(pdf.stat().st_mtime)
            print(f"  {mtime.strftime('%Y-%m-%d')}: {pdf.name[:60]}...")
        if len(categorized['other']) > 10:
            print(f"  ... and {len(categorized['other']) - 10} more")

    # Summary
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Academic papers moved to inbox: {moved}")
    print(f"Errors: {errors}")
    print(f"Files remaining in Downloads: {len(categorized['proof']) + len(categorized['receipt']) + len(categorized['other'])}")
    print()

    if args.dry_run:
        print("⚠️  This was a DRY RUN - run without --dry-run to move files")
    else:
        print("✅ Academic papers migrated to inbox!")
        print()
        print("Next steps:")
        print("  1. Review proofs - decide if they're your work or peer reviews")
        print("  2. Delete receipts or move to operations/admin/")
        print("  3. Review 'other' files individually")

    print("=" * 70)


if __name__ == '__main__':
    main()
