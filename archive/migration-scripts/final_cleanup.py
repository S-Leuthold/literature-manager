#!/usr/bin/env python3
"""
Final cleanup of needs-review folder - move papers to appropriate topics.

Run with: python3 final_cleanup.py [--dry-run]
"""

import shutil
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Final needs-review cleanup')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    needs_review = Path('/Users/samleuthold/Desktop/workshop/library/literature/by-topic/needs-review')
    by_topic = Path('/Users/samleuthold/Desktop/workshop/library/literature/by-topic')

    # Manual assignments based on review
    assignments = {
        # Soil carbon papers
        'Janzen, 2024': 'soil-carbon',
        'Derrien et al., 2023': 'soil-carbon',

        # Methods papers
        'Hajdas et al., 2021': 'isotope-methods',
        'Rapson & Dacres, 2014': 'nitrogen-cycling',

        # Climate/agriculture
        'Lamaoui et al., 2018': 'climate-change',
        'Li et al., 2024': 'modeling-and-prediction',

        # Agroecology/food systems
        'Khoury et al., 2014': 'agroecology',
        'Publishers, 2022': 'agroecology',
        'Schipanski et al., 2016': 'agroecology',

        # Contamination
        'Powlson et al., 2008': 'soil-contamination',
    }

    delete_list = [
        'Carolan, 2022',
        'Béné et al., 2019',
        'Montenegro De Wit & Iles, 2016',
        'Montenegro De Wit et al., 2021',
        'Rathgens et al., 2020',
        'Unknown, 2013',  # Marine ecology
    ]

    print("=" * 70)
    print("FINAL NEEDS-REVIEW CLEANUP")
    print("=" * 70)
    print()

    if args.dry_run:
        print("⚠️  DRY RUN MODE\n")

    pdfs = list(needs_review.glob('*.pdf'))

    moved = 0
    deleted = 0
    errors = 0

    # Move papers to topics
    for pdf in pdfs:
        # Check assignments
        for key, topic in assignments.items():
            if key in pdf.name:
                dest_dir = by_topic / topic
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / pdf.name

                try:
                    if not args.dry_run:
                        shutil.move(str(pdf), str(dest))
                    print(f"  ✓ {pdf.name[:55]}... → {topic}")
                    moved += 1
                except Exception as e:
                    print(f"  ✗ Error: {e}")
                    errors += 1
                break

        # Check deletions
        for del_key in delete_list:
            if del_key in pdf.name:
                try:
                    if not args.dry_run:
                        pdf.unlink()
                    print(f"  ✗ Deleted: {pdf.name[:60]}...")
                    deleted += 1
                except Exception as e:
                    print(f"  ✗ Error deleting: {e}")
                    errors += 1
                break

    # Summary
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Papers moved to topics: {moved}")
    print(f"Papers deleted: {deleted}")
    print(f"Errors: {errors}")

    if not args.dry_run:
        remaining = len(list(needs_review.glob('*.pdf')))
        print(f"Papers still in needs-review: {remaining}")

    print()
    if args.dry_run:
        print("⚠️  DRY RUN - run without --dry-run to execute")
    else:
        print("✅ Cleanup complete!")
    print("=" * 70)


if __name__ == '__main__':
    main()
