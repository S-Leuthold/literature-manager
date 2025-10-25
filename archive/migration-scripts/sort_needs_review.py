#!/usr/bin/env python3
"""
Sort papers from needs-review folder into appropriate locations.

Categories:
- Statistical methods papers → by-topic/statistical-methods/
- Lab manuals → library/protocols/laboratory/
- Soil science papers → manual topic assignment or back to needs-review
- Junk → delete

Run with: python3 sort_needs_review.py [--dry-run]
"""

import shutil
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Sort needs-review papers')
    parser.add_argument('--dry-run', action='store_true', help='Preview without moving')
    args = parser.parse_args()

    # Paths
    needs_review_dir = Path('/Users/samleuthold/Desktop/workshop/library/literature/by-topic/needs-review')
    stats_dir = Path('/Users/samleuthold/Desktop/workshop/library/literature/by-topic/statistical-methods')
    protocols_dir = Path('/Users/samleuthold/Desktop/workshop/library/protocols/laboratory')

    print("=" * 70)
    print("SORTING NEEDS-REVIEW FOLDER")
    print("=" * 70)
    print()

    if args.dry_run:
        print("⚠️  DRY RUN MODE\n")

    # Define categorization rules
    stats_papers = [
        'Bates',  # lme4
        'Dietterich',  # ensemble methods
        'Pearl',  # causality
        'Mudge',  # hypothesis testing
        'Friston',  # statistical review
        'Addicott',  # causal inference
        'matsueda'  # SEM history
    ]

    lab_manuals = [
        'USDA',  # Kellogg manual
        'Sparks',  # Methods of Soil Analysis
        'Bob',  # Western States
        'Rakshit'  # Recent trends
    ]

    # Scan needs-review
    pdfs = list(needs_review_dir.glob('*.pdf'))

    categorized = {
        'stats': [],
        'protocols': [],
        'maybe_delete': [],
        'keep_for_review': []
    }

    for pdf in pdfs:
        name = pdf.name

        # Check if it's stats
        if any(author in name for author in stats_papers):
            categorized['stats'].append(pdf)
        # Check if it's lab manual
        elif any(manual in name for manual in lab_manuals):
            categorized['protocols'].append(pdf)
        # Check if it's obvious junk (needs-review.pdf with no real name)
        elif 'needs-review.pdf' in name and name.count(',') <= 1 and len(name) < 40:
            categorized['maybe_delete'].append(pdf)
        else:
            categorized['keep_for_review'].append(pdf)

    # Show what would happen
    print(f"Total papers in needs-review: {len(pdfs)}")
    print()

    if categorized['stats']:
        print(f"STATISTICAL METHODS → by-topic/statistical-methods/ ({len(categorized['stats'])}):")
        for pdf in categorized['stats']:
            print(f"  • {pdf.name}")
        print()

    if categorized['protocols']:
        print(f"LAB MANUALS → library/protocols/laboratory/ ({len(categorized['protocols'])}):")
        for pdf in categorized['protocols']:
            print(f"  • {pdf.name}")
        print()

    if categorized['maybe_delete']:
        print(f"SUGGEST DELETE (failed extraction) ({len(categorized['maybe_delete'])}):")
        for pdf in categorized['maybe_delete']:
            print(f"  • {pdf.name}")
        print()

    if categorized['keep_for_review']:
        print(f"KEEP FOR MANUAL REVIEW ({len(categorized['keep_for_review'])}):")
        for pdf in categorized['keep_for_review']:
            print(f"  • {pdf.name}")
        print()

    # Execute moves
    if not args.dry_run:
        print("=" * 70)
        print("EXECUTING MOVES")
        print("=" * 70)
        print()

        # Create directories
        stats_dir.mkdir(parents=True, exist_ok=True)
        protocols_dir.mkdir(parents=True, exist_ok=True)

        # Move stats
        for pdf in categorized['stats']:
            dest = stats_dir / pdf.name
            shutil.move(str(pdf), str(dest))
            print(f"  ✓ {pdf.name} → statistical-methods/")

        # Move protocols
        for pdf in categorized['protocols']:
            dest = protocols_dir / pdf.name
            shutil.move(str(pdf), str(dest))
            print(f"  ✓ {pdf.name} → protocols/laboratory/")

        # Delete junk
        for pdf in categorized['maybe_delete']:
            pdf.unlink()
            print(f"  ✗ Deleted: {pdf.name}")

        print()
        print(f"✅ Sorted {len(categorized['stats']) + len(categorized['protocols'])} papers")
        print(f"✅ Deleted {len(categorized['maybe_delete'])} junk files")
        print(f"📋 {len(categorized['keep_for_review'])} papers remain for manual review")

    print()
    print("=" * 70)


if __name__ == '__main__':
    main()
