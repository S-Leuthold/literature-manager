"""Command-line interface for Literature Manager."""

import sys
import time
import os
import fcntl
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import click
from colorama import Fore, Style, init

from literature_manager.config import load_config
from literature_manager.extractors import extract_metadata
from literature_manager.extractors.exceptions import (
    CorruptedPDFError,
    NetworkError,
    LLMError,
    ConfigurationError,
)
from literature_manager.naming import generate_filename
from literature_manager.operations import (
    check_duplicate,
    copy_to_recent,
    determine_destination,
    handle_duplicate,
    load_index,
    log_action,
    move_and_rename_file,
    update_index,
)
from literature_manager.topics import (
    load_topic_profiles,
    match_topic,
    save_topic_profiles,
    update_topic_profile,
)

# Initialize colorama for cross-platform color support
init(autoreset=True)


def print_success(message: str):
    """Print success message in green."""
    click.echo(f"{Fore.GREEN}✓ {message}{Style.RESET_ALL}")


def print_error(message: str):
    """Print error message in red."""
    click.echo(f"{Fore.RED}✗ {message}{Style.RESET_ALL}")


def print_warning(message: str):
    """Print warning message in yellow."""
    click.echo(f"{Fore.YELLOW}⚠ {message}{Style.RESET_ALL}")


def print_info(message: str):
    """Print info message."""
    click.echo(f"{Fore.CYAN}ℹ {message}{Style.RESET_ALL}")


@click.group()
@click.option("--config", type=click.Path(exists=True), help="Path to config.yaml")
@click.pass_context
def main(ctx, config):
    """Literature Manager - Automated PDF organization tool."""
    try:
        ctx.ensure_object(dict)
        ctx.obj["config"] = load_config(Path(config) if config else None)
    except FileNotFoundError as e:
        print_error(str(e))
        sys.exit(1)


def process_single_pdf(pdf_path: Path, config, dry_run: bool = False, verbose: bool = True) -> bool:
    """
    Process a single PDF file.

    Returns True if successful, False otherwise.
    """
    if verbose:
        print_info(f"Processing: {pdf_path.name}")

    ##  ---------------------------------------------------------------------------
    ## Check PDF readability before expensive operations
    ## ---------------------------------------------------------------------------
    from literature_manager.extractors.text_parser import is_pdf_readable
    from literature_manager.operations import log_action

    is_readable, error_reason = is_pdf_readable(pdf_path)

    if not is_readable:
        print_error(f"  Corrupted or unreadable PDF: {error_reason}")

        if not dry_run and pdf_path.exists():
            corrupted_path = config.corrupted_path
            corrupted_path.mkdir(parents=True, exist_ok=True)
            dest = corrupted_path / pdf_path.name

            try:
                pdf_path.rename(dest)
                print_warning(f"  Moved to corrupted/")

                # Log with ERROR action
                log_action(
                    "ERROR",
                    {"title": pdf_path.name, "authors": [], "year": None},
                    pdf_path,
                    dest,
                    config,
                    reason=f"corrupted_pdf: {error_reason}",
                )
            except FileNotFoundError:
                # File was already moved, ignore
                pass

        return False

    try:
        # Quick duplicate check BEFORE expensive metadata extraction
        # Try to get DOI quickly from PDF metadata first
        from literature_manager.extractors.doi import extract_doi_from_pdf
        quick_doi = extract_doi_from_pdf(pdf_path)

        if quick_doi:
            # Check if this DOI already exists
            from literature_manager.operations import check_duplicate_by_doi, load_index
            index = load_index(config.index_path)
            existing = check_duplicate_by_doi(quick_doi, index)

            if existing:
                print_warning(f"  Duplicate detected (doi): {existing}")
                if not dry_run and pdf_path.exists():
                    pdf_path.unlink()
                    print_info("  Duplicate deleted, skipping")
                return False

        # Extract full metadata (includes LLM enhancement)
        if verbose:
            click.echo("  Extracting metadata...")
        metadata = extract_metadata(pdf_path, config)

        if metadata.get("extraction_confidence", 0) == 0.0:
            # Complete failure
            print_error(f"  Failed to extract metadata from {pdf_path.name}")

            if not dry_run and pdf_path.exists():
                # Move to unknowables
                unknowables_path = config.unknowables_path
                unknowables_path.mkdir(parents=True, exist_ok=True)
                dest = unknowables_path / pdf_path.name
                try:
                    pdf_path.rename(dest)
                    print_warning(f"  Moved to unknowables/")
                except FileNotFoundError:
                    # File was already moved, ignore
                    pass

            return False

        if verbose:
            click.echo(f"  Title: {metadata.get('title', 'Unknown')[:60]}...")
            click.echo(f"  Authors: {', '.join(metadata.get('authors', ['Unknown'])[:3])}")
            click.echo(f"  Method: {metadata.get('extraction_method')}")

        # Check for duplicates
        duplicate = check_duplicate(metadata, config)
        if duplicate:
            method, existing_path = duplicate
            print_warning(f"  Duplicate detected ({method}): {existing_path}")

            if not dry_run:
                action = config.get("duplicate_action", "merge")
                should_continue = handle_duplicate(pdf_path, existing_path, action, config)
                if not should_continue:
                    print_info("  Duplicate handled, skipping")
                    return False

        # Generate filename
        filename = generate_filename(metadata, config.get("max_filename_length", 200))
        if verbose:
            click.echo(f"  New name: {filename}")

        # Get LLM-suggested topics (pipe-separated list)
        suggested_topics_str = metadata.get("suggested_topic", "")
        topics = [t.strip() for t in suggested_topics_str.split("|") if t.strip()] if suggested_topics_str else []

        if topics:
            if verbose:
                if len(topics) == 1:
                    click.echo(f"  Topic: {topics[0]}")
                else:
                    # Show primary topic and additional topics
                    click.echo(f"  Primary topic: {topics[0]}")
                    click.echo(f"  Also in: {', '.join(topics[1:])}")
            # Use first topic as primary
            topic = topics[0]
            confidence = 0.85  # High confidence for LLM suggestions
        else:
            if verbose:
                click.echo(f"  No topic suggested")
            topic = None
            confidence = 0.0

        # Determine destination (pass all topics for multi-topic symlinks)
        primary_dest, secondary_dests = determine_destination(metadata, topics, confidence, config)

        if verbose:
            click.echo(f"  Destination: {primary_dest.relative_to(config.workshop_root)}")

        if dry_run:
            print_success(f"  [DRY RUN] Would move to: {primary_dest}")
            return True

        # Move and rename file (creates symlinks in other topic folders)
        final_path = move_and_rename_file(pdf_path, primary_dest, filename, secondary_dests)

        # Always copy to recent/ for 3-day window (unless already in recent)
        if primary_dest != config.recent_path:
            copy_to_recent(final_path, config.recent_path)

        # Update index
        metadata["topics"] = topics  # Store all topics
        metadata["topic_confidence"] = confidence
        update_index(metadata, final_path, config)

        # Log action
        action = "PROCESSED" if confidence >= config.get("confidence_threshold", 0.85) else "REVIEW_NEEDED"
        log_action(
            action,
            metadata,
            pdf_path,
            final_path,
            config,
            confidence=confidence,
            method=metadata.get("extraction_method"),
            topic=topic or "none",
        )

        # Upload to Zotero (if enabled)
        if not dry_run and config.get("zotero_sync_enabled", False):
            try:
                from literature_manager.zotero_sync import ZoteroSync
                zot_sync = ZoteroSync()
                zot_sync.upload_paper(metadata, final_path, topics)
            except Exception as e:
                print_warning(f"  Zotero upload failed: {e}")
                # Don't fail the whole process if Zotero upload fails

        print_success(f"  Processed successfully!")
        return True

    except CorruptedPDFError as e:
        # PDF is structurally broken - already handled by early check
        # This catches any that slip through (shouldn't happen)
        print_error(f"  Corrupted PDF: {e.message}")
        if verbose:
            print_info(f"  Method: {e.method}")

        if not dry_run and pdf_path.exists():
            corrupted_path = config.corrupted_path
            corrupted_path.mkdir(parents=True, exist_ok=True)
            dest = corrupted_path / pdf_path.name
            try:
                pdf_path.rename(dest)
                print_warning(f"  Moved to corrupted/")
                log_action("ERROR", {"title": pdf_path.name, "authors": [], "year": None},
                          pdf_path, dest, config, reason=f"corrupted: {e.method}")
            except FileNotFoundError:
                pass
        return False

    except NetworkError as e:
        # Network issue - don't move file (transient, user can retry)
        print_error(f"  Network error: {e.message}")
        if verbose:
            print_info(f"  Status: {e.status_code or 'timeout'}")
            print_info(f"  Method: {e.method}")

        # Log but don't move file
        log_action("ERROR", {"title": pdf_path.name, "authors": [], "year": None},
                  pdf_path, pdf_path, config, reason=f"network: {e.method}")
        return False

    except LLMError as e:
        # LLM API issue - don't move file (transient, API issue)
        print_error(f"  LLM error: {e.message}")
        if verbose:
            if e.api_error:
                print_info(f"  Details: {e.api_error[:100]}")
            print_info(f"  Method: {e.method}")

        # Log but don't move file
        log_action("ERROR", {"title": pdf_path.name, "authors": [], "year": None},
                  pdf_path, pdf_path, config, reason=f"llm: {e.method}")
        return False

    except ConfigurationError as e:
        # Config issue - fail fast
        print_error(f"  Configuration error: {e.message}")
        print_info(f"  Fix your config.yaml or .env and try again")
        if verbose:
            print_info(f"  Method: {e.method}")
        return False

    except Exception as e:
        # Catch-all for unexpected errors
        print_error(f"  Unexpected error: {type(e).__name__}: {e}")
        if verbose:
            import traceback
            traceback.print_exc()
        return False


@main.command()
@click.option("--dry-run", is_flag=True, help="Show what would happen without making changes")
@click.option("--verbose/--quiet", default=True, help="Verbose output")
@click.pass_context
def process(ctx, dry_run, verbose):
    """Process all PDFs in inbox."""
    config = ctx.obj["config"]

    # Ensure directories exist
    config.ensure_directories()

    # Find all PDFs in inbox
    inbox_path = config.inbox_path
    pdf_files = list(inbox_path.glob("*.pdf"))

    if not pdf_files:
        print_info("No PDFs found in inbox")
        return

    click.echo(f"\nFound {len(pdf_files)} PDF(s) in inbox\n")

    if dry_run:
        print_warning("DRY RUN MODE - No changes will be made\n")

    # Process each PDF
    success_count = 0
    fail_count = 0

    with click.progressbar(pdf_files, label="Processing PDFs") as bar:
        for pdf_path in bar:
            if process_single_pdf(pdf_path, config, dry_run, verbose):
                success_count += 1
            else:
                fail_count += 1
            click.echo()  # Blank line between files

    # Summary
    click.echo(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    click.echo(f"{Fore.CYAN}Summary:{Style.RESET_ALL}")
    print_success(f"Successfully processed: {success_count}")
    if fail_count > 0:
        print_error(f"Failed: {fail_count}")
    click.echo(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")


@main.command()
@click.option("--verbose/--quiet", default=True, help="Verbose output")
@click.pass_context
def watch(ctx, verbose):
    """Watch inbox for new PDFs and process them automatically."""
    config = ctx.obj["config"]
    config.ensure_directories()

    # Create PID file to prevent multiple instances
    pid_file_path = config.workshop_root / '.tools' / 'literature-manager' / 'logs' / 'watch.pid'
    pid_file_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        pid_file = open(pid_file_path, 'w')
        fcntl.flock(pid_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        pid_file.write(str(os.getpid()))
        pid_file.flush()
    except (IOError, OSError):
        print_error("Another instance of literature-manager watch is already running")
        print_info("If you're sure no other instance is running, delete:")
        print_info(f"  {pid_file_path}")
        sys.exit(1)

    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    processed_files = set()

    class PDFHandler(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory:
                return

            path = Path(event.src_path)
            if path.suffix.lower() == ".pdf":
                # Skip if already processed
                if path.name in processed_files:
                    return

                # Wait until file is fully written (check file size stabilization)
                time.sleep(1)  # Initial wait

                # Wait for file size to stabilize (max 30 seconds)
                max_wait = 30
                stable_count = 0
                last_size = -1

                for _ in range(max_wait):
                    if not path.exists():
                        return  # File was moved/deleted

                    try:
                        current_size = path.stat().st_size

                        # File size hasn't changed - consider it stable
                        if current_size == last_size and current_size > 0:
                            stable_count += 1
                            if stable_count >= 2:  # Stable for 2 seconds
                                break
                        else:
                            stable_count = 0

                        last_size = current_size
                        time.sleep(1)
                    except Exception:
                        return  # Error accessing file

                # Check if file still exists and is not empty
                if not path.exists() or path.stat().st_size == 0:
                    return

                click.echo(f"\n{Fore.YELLOW}New PDF detected: {path.name}{Style.RESET_ALL}")
                processed_files.add(path.name)
                process_single_pdf(path, config, dry_run=False, verbose=verbose)

    print_info(f"Watching inbox: {config.inbox_path}")
    print_info("Press Ctrl+C to stop\n")

    # Skip index validation in background mode to avoid file lock conflicts
    # Validation will happen during normal processing when files are added
    if verbose:
        print_info("✓ Watch mode started (index validation skipped)\n")

    # Process any existing PDFs in inbox before starting watch
    existing_pdfs = list(config.inbox_path.glob("*.pdf"))
    if existing_pdfs:
        print_info(f"Found {len(existing_pdfs)} existing PDFs in inbox, processing...\n")
        for pdf_path in existing_pdfs:
            if pdf_path.name not in processed_files:
                click.echo(f"\n{Fore.YELLOW}Processing existing PDF: {pdf_path.name}{Style.RESET_ALL}")
                processed_files.add(pdf_path.name)
                process_single_pdf(pdf_path, config, dry_run=False, verbose=verbose)
        print_info(f"\n✓ Finished processing existing files, now watching for new ones...\n")

    event_handler = PDFHandler()
    observer = Observer()
    observer.schedule(event_handler, str(config.inbox_path), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print_info("\nStopped watching")

    observer.join()


@main.command()
@click.pass_context
def stats(ctx):
    """Show library statistics."""
    config = ctx.obj["config"]

    # Load index
    index = load_index(config.index_path)

    if not index:
        print_info("No papers in library yet")
        return

    total_papers = len(index)

    # Count by topic
    topics = {}
    for entry in index.values():
        topic = entry.get("topic", "unclassified")
        topics[topic] = topics.get(topic, 0) + 1

    # Count by year
    years = {}
    for entry in index.values():
        year = entry.get("year", "unknown")
        years[year] = years.get(year, 0) + 1

    # Count by extraction method
    methods = {}
    for entry in index.values():
        method = entry.get("extraction_method", "unknown")
        methods[method] = methods.get(method, 0) + 1

    # Count papers in recent/unknowables
    recent_count = len(list(config.recent_path.glob("*.pdf"))) if config.recent_path.exists() else 0
    unknowables_count = (
        len(list(config.unknowables_path.glob("*.pdf"))) if config.unknowables_path.exists() else 0
    )

    # Display
    click.echo(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    click.echo(f"{Fore.CYAN}Library Statistics{Style.RESET_ALL}")
    click.echo(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")

    click.echo(f"Total papers: {total_papers}")
    click.echo(f"Papers in recent/: {recent_count}")
    click.echo(f"Papers in unknowables/: {unknowables_count}\n")

    click.echo(f"{Fore.YELLOW}By Topic:{Style.RESET_ALL}")
    for topic, count in sorted(topics.items(), key=lambda x: x[1], reverse=True)[:10]:
        click.echo(f"  {topic}: {count}")

    click.echo(f"\n{Fore.YELLOW}By Year:{Style.RESET_ALL}")
    for year, count in sorted(years.items(), reverse=True)[:10]:
        click.echo(f"  {year}: {count}")

    click.echo(f"\n{Fore.YELLOW}By Extraction Method:{Style.RESET_ALL}")
    for method, count in sorted(methods.items(), key=lambda x: x[1], reverse=True):
        click.echo(f"  {method}: {count}")

    click.echo(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")


@main.command()
@click.pass_context
def review_recent(ctx):
    """Interactive review of papers in recent/."""
    config = ctx.obj["config"]

    recent_path = config.recent_path
    if not recent_path.exists():
        print_info("No recent/ folder found")
        return

    pdf_files = list(recent_path.glob("*.pdf"))
    if not pdf_files:
        print_info("No files in recent/ to review")
        return

    # Load profiles
    profiles = load_topic_profiles(config.topic_profiles_path)
    topic_names = sorted(profiles.keys())

    click.echo(f"\nFound {len(pdf_files)} paper(s) in recent/\n")

    processed_count = 0
    skipped_count = 0

    for i, pdf_path in enumerate(pdf_files, 1):
        click.echo(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
        click.echo(f"{Fore.CYAN}[{i}/{len(pdf_files)}] {pdf_path.name}{Style.RESET_ALL}")
        click.echo(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")

        # Load metadata from index
        index = load_index(config.index_path)
        metadata = None

        # Find this file in index
        for entry in index.values():
            if Path(entry.get("filepath", "")).name == pdf_path.name:
                metadata = entry
                break

        if metadata:
            click.echo(f"Title: {metadata.get('title', 'Unknown')}")
            click.echo(f"Authors: {', '.join(metadata.get('authors', ['Unknown'])[:3])}")
            click.echo(f"Year: {metadata.get('year', 'Unknown')}\n")

            suggested = metadata.get("topic")
            if suggested:
                click.echo(f"Suggested topic: {suggested}\n")

        # Show options
        click.echo("Options:")
        click.echo("  [a] Accept suggested topic")
        click.echo("  [c] Choose different topic")
        click.echo("  [n] Create new topic")
        click.echo("  [s] Skip for now")
        click.echo("  [q] Quit")

        choice = click.prompt("\nYour choice", type=str, default="s").lower()

        if choice == "q":
            break
        elif choice == "s":
            skipped_count += 1
            continue
        elif choice == "a" and metadata and metadata.get("topic"):
            # Accept suggestion
            topic = metadata.get("topic")
        elif choice == "c":
            # Choose from existing topics
            if not topic_names:
                print_warning("No existing topics. Use [n] to create one.")
                continue

            click.echo("\nAvailable topics:")
            for idx, topic in enumerate(topic_names, 1):
                click.echo(f"  [{idx}] {topic}")

            topic_idx = click.prompt("Choose topic number", type=int)
            if 1 <= topic_idx <= len(topic_names):
                topic = topic_names[topic_idx - 1]
            else:
                print_error("Invalid choice")
                continue
        elif choice == "n":
            # Create new topic
            topic = click.prompt("Enter new topic name (kebab-case)").lower().strip()
            if not topic:
                continue
        else:
            print_error("Invalid choice")
            continue

        # Move to topic folder
        from slugify import slugify

        topic_slug = slugify(topic)
        dest_dir = config.by_topic_path / topic_slug
        dest_dir.mkdir(parents=True, exist_ok=True)

        new_path = dest_dir / pdf_path.name
        pdf_path.rename(new_path)

        # Update topic profile if we have metadata
        if metadata:
            profiles = update_topic_profile(topic, metadata, profiles)
            save_topic_profiles(profiles, config.topic_profiles_path)

        print_success(f"Moved to: {dest_dir.relative_to(config.workshop_root)}\n")
        processed_count += 1

    # Summary
    click.echo(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    print_success(f"Processed: {processed_count}")
    print_info(f"Skipped: {skipped_count}")
    click.echo(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")


@main.command()
@click.pass_context
def cleanup(ctx):
    """Clean up recent/ folder (remove papers older than retention period)."""
    config = ctx.obj["config"]

    retention_days = config.get("recent_retention_days", 3)
    cutoff_date = datetime.now() - timedelta(days=retention_days)

    recent_path = config.recent_path
    if not recent_path.exists():
        print_info("No recent/ folder found")
        return

    pdf_files = list(recent_path.glob("*.pdf"))
    if not pdf_files:
        print_info("No files in recent/")
        return

    moved_count = 0
    for pdf_path in pdf_files:
        # Check modification time
        mtime = datetime.fromtimestamp(pdf_path.stat().st_mtime)

        if mtime < cutoff_date:
            # Old file - move to unknowables
            config.unknowables_path.mkdir(parents=True, exist_ok=True)
            dest = config.unknowables_path / pdf_path.name

            if dest.exists():
                # Already exists in unknowables, just delete from recent
                pdf_path.unlink()
            else:
                # Move to unknowables
                pdf_path.rename(dest)

            print_info(f"Moved {pdf_path.name} to unknowables/")
            moved_count += 1

    if moved_count == 0:
        print_success("No old files to clean up")
    else:
        print_success(f"Cleaned up {moved_count} file(s)")


if __name__ == "__main__":
    main()
