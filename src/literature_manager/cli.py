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
from slugify import slugify

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
from literature_manager.taxonomy import TopicTaxonomy

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


def update_index_fulltext_summary(doi: str, fulltext_summary: dict, config) -> bool:
    """
    Update the index with a fulltext summary for a paper identified by DOI.

    Args:
        doi: DOI of the paper
        fulltext_summary: Dict with main_finding, key_approach, key_results, implication
        config: Config object

    Returns:
        True if updated, False otherwise
    """
    if not doi:
        return False

    from literature_manager.operations import load_index, save_index
    import json

    index = load_index(config.index_path)

    # Find entry by DOI
    doi_normalized = doi.strip().lower().replace('https://doi.org/', '').replace('http://doi.org/', '')
    updated = False

    for hash_id, entry in index.items():
        entry_doi = entry.get("doi", "")
        if entry_doi:
            entry_doi_normalized = entry_doi.strip().lower().replace('https://doi.org/', '').replace('http://doi.org/', '')
            if entry_doi_normalized == doi_normalized:
                entry["fulltext_summary"] = fulltext_summary
                updated = True
                break

    if updated:
        save_index(index, config.index_path)

    return updated


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
            confidence = config.get("confidence_threshold", 0.85)
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
        zot_sync = None
        if not dry_run and config.get("zotero_sync_enabled", False):
            try:
                from literature_manager.zotero_sync import ZoteroSync
                zot_sync = ZoteroSync(
                    api_key=config.get("zotero_api_key"),
                    user_id=config.get("zotero_user_id"),
                    library_type=config.get("zotero_library_type", "user")
                )
                zot_sync.upload_paper(metadata, final_path, topics)
            except Exception as e:
                print_warning(f"  Zotero upload failed: {e}")
                # Don't fail the whole process if Zotero upload fails

        # Generate fulltext summary (if enabled and API key available)
        api_key = config.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
        if not dry_run and api_key and metadata.get("title"):
            try:
                from literature_manager.extractors.llm import generate_fulltext_summary
                if verbose:
                    click.echo("  Generating fulltext summary...")
                summary_result = generate_fulltext_summary(
                    pdf_path=str(final_path),
                    title=metadata["title"],
                    api_key=api_key
                )
                if summary_result and summary_result.get("fulltext_summary"):
                    # Update index with fulltext summary
                    fulltext_summary = summary_result["fulltext_summary"]
                    update_index_fulltext_summary(metadata.get("doi"), fulltext_summary, config)
                    if verbose:
                        click.echo("  Fulltext summary generated")

                    # Push summary to Zotero as note
                    if zot_sync and metadata.get("doi"):
                        try:
                            zot_sync.add_or_update_fulltext_note(
                                doi=metadata["doi"],
                                fulltext_summary=fulltext_summary,
                                title=metadata.get("title", "")
                            )
                            if verbose:
                                click.echo("  Summary pushed to Zotero")
                        except Exception as e:
                            print_warning(f"  Zotero note failed: {e}")
            except Exception as e:
                if verbose:
                    print_warning(f"  Fulltext summary failed: {e}")
                # Don't fail the whole process if summary generation fails

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
@click.option("--study-type", type=click.Choice(["field", "laboratory", "greenhouse", "modeling", "review", "methods"]), help="Filter by study type")
@click.option("--method", multiple=True, help="Filter by analytical method (can specify multiple)")
@click.option("--fraction", multiple=True, help="Filter by soil fraction (can specify multiple)")
@click.option("--ecosystem", help="Filter by ecosystem context")
@click.option("--property", "soil_property", multiple=True, help="Filter by soil property measured")
@click.option("--topic", help="Filter by topic")
@click.option("--year-min", type=int, help="Minimum publication year")
@click.option("--year-max", type=int, help="Maximum publication year")
@click.option("--text", help="Search in title/abstract")
@click.option("--limit", default=20, help="Maximum results to show")
@click.pass_context
def search(ctx, study_type, method, fraction, ecosystem, soil_property, topic, year_min, year_max, text, limit):
    """Search papers by domain-specific attributes.

    Examples:
      literature-manager search --study-type field --fraction POM
      literature-manager search --method FTIR --method NIR
      literature-manager search --ecosystem agricultural --property SOC
      literature-manager search --text "carbon sequestration" --year-min 2020
    """
    config = ctx.obj["config"]
    index = load_index(config.index_path)

    if not index:
        print_info("No papers in library yet")
        return

    results = []

    for hash_id, entry in index.items():
        # Get domain attributes (may not exist for older entries)
        domain = entry.get("domain_attributes", {})

        # Apply filters
        if study_type and domain.get("study_type") != study_type:
            continue

        if method:
            entry_methods = domain.get("analytical_methods", [])
            if not any(m in entry_methods for m in method):
                continue

        if fraction:
            entry_fractions = domain.get("soil_fractions", [])
            if not any(f in entry_fractions for f in fraction):
                continue

        if ecosystem and domain.get("ecosystem") != ecosystem:
            continue

        if soil_property:
            entry_props = domain.get("soil_properties", [])
            if not any(p in entry_props for p in soil_property):
                continue

        if topic:
            entry_topic = entry.get("topic", "")
            if topic.lower() not in entry_topic.lower():
                continue

        entry_year = entry.get("year")
        if year_min and entry_year and entry_year < year_min:
            continue
        if year_max and entry_year and entry_year > year_max:
            continue

        if text:
            search_text = text.lower()
            title = (entry.get("title") or "").lower()
            abstract = (entry.get("abstract") or "").lower()
            if search_text not in title and search_text not in abstract:
                continue

        results.append(entry)

    # Sort by year (newest first)
    results.sort(key=lambda x: x.get("year") or 0, reverse=True)

    # Display results
    click.echo(f"\n{Fore.CYAN}Found {len(results)} matching paper(s){Style.RESET_ALL}\n")

    for i, entry in enumerate(results[:limit], 1):
        domain = entry.get("domain_attributes", {})

        # Title and basic info
        title = entry.get("title", "Unknown")[:70]
        authors = entry.get("authors", [])
        author_str = authors[0] if authors else "Unknown"
        if len(authors) > 1:
            author_str += " et al."
        year = entry.get("year", "")

        click.echo(f"{Fore.YELLOW}[{i}]{Style.RESET_ALL} {title}...")
        click.echo(f"    {author_str}, {year}")

        # Domain attributes (compact display)
        attrs = []
        if domain.get("study_type"):
            attrs.append(f"[{domain['study_type']}]")
        if domain.get("analytical_methods"):
            attrs.append(", ".join(domain["analytical_methods"][:3]))
        if domain.get("soil_fractions"):
            attrs.append(", ".join(domain["soil_fractions"][:3]))
        if domain.get("ecosystem"):
            attrs.append(domain["ecosystem"])

        if attrs:
            click.echo(f"    {Fore.CYAN}{' | '.join(attrs)}{Style.RESET_ALL}")

        # File path
        filepath = entry.get("filepath", "")
        if filepath:
            click.echo(f"    {Fore.GREEN}{filepath}{Style.RESET_ALL}")
        click.echo()

    if len(results) > limit:
        print_info(f"Showing {limit} of {len(results)} results. Use --limit to see more.")


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

    # Load fixed taxonomy
    taxonomy = TopicTaxonomy()
    topic_names = sorted(taxonomy.get_all_slugs())

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
        topic_slug = slugify(topic)
        dest_dir = config.by_topic_path / topic_slug
        dest_dir.mkdir(parents=True, exist_ok=True)

        new_path = dest_dir / pdf_path.name
        pdf_path.rename(new_path)

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


@main.command()
@click.option("--dry-run", is_flag=True, help="Show duplicates without removing them")
@click.pass_context
def dedup(ctx, dry_run):
    """Find and remove duplicate papers from the library.

    Identifies duplicates by DOI and title similarity, keeping the
    most complete version and removing extras from both the index
    and filesystem.
    """
    from collections import defaultdict
    from literature_manager.operations import save_index
    from literature_manager.utils import fuzzy_match_score

    config = ctx.obj["config"]
    index = load_index(config.index_path)

    if not index:
        print_info("No papers in library yet")
        return

    click.echo(f"\n{Fore.CYAN}Scanning {len(index)} papers for duplicates...{Style.RESET_ALL}\n")

    # Group by DOI
    doi_groups = defaultdict(list)
    for hash_id, entry in index.items():
        doi = entry.get('doi', '').strip().lower()
        if doi:
            doi_groups[doi].append((hash_id, entry))

    # Find DOI duplicates
    doi_duplicates = [(doi, entries) for doi, entries in doi_groups.items() if len(entries) > 1]

    # Group by normalized title for non-DOI entries
    title_groups = defaultdict(list)
    for hash_id, entry in index.items():
        if not entry.get('doi'):  # Only check titles for papers without DOI
            title = entry.get('title', '').strip().lower()
            if title:
                title_groups[title].append((hash_id, entry))

    # Find title duplicates (exact match)
    title_duplicates = [(title, entries) for title, entries in title_groups.items() if len(entries) > 1]

    total_dups = len(doi_duplicates) + len(title_duplicates)

    if total_dups == 0:
        print_success("No duplicates found!")
        return

    click.echo(f"{Fore.YELLOW}Found {len(doi_duplicates)} DOI duplicates and {len(title_duplicates)} title duplicates{Style.RESET_ALL}\n")

    if dry_run:
        print_warning("DRY RUN - No changes will be made\n")

    removed_count = 0
    files_deleted = 0

    # Process DOI duplicates
    for doi, entries in doi_duplicates:
        click.echo(f"{Fore.CYAN}DOI: {doi}{Style.RESET_ALL}")

        # Sort by completeness (prefer entries with more metadata)
        def completeness_score(entry):
            e = entry[1]
            score = 0
            if e.get('abstract'): score += 3
            if e.get('authors'): score += len(e['authors'])
            if e.get('journal'): score += 2
            if e.get('domain_attributes'): score += 2
            return score

        entries_sorted = sorted(entries, key=completeness_score, reverse=True)
        keep = entries_sorted[0]
        remove = entries_sorted[1:]

        click.echo(f"  Keep: {keep[1].get('title', 'Unknown')[:50]}...")
        for hash_id, entry in remove:
            filepath = entry.get('filepath', '')
            click.echo(f"  {Fore.RED}Remove: {filepath}{Style.RESET_ALL}")

            if not dry_run:
                # Remove from index
                if hash_id in index:
                    del index[hash_id]
                    removed_count += 1

                # Delete file if it exists
                full_path = config.workshop_root / filepath
                if full_path.exists():
                    full_path.unlink()
                    files_deleted += 1
                    click.echo(f"    Deleted file")

        click.echo()

    # Process title duplicates
    for title, entries in title_duplicates:
        click.echo(f"{Fore.CYAN}Title: {title[:50]}...{Style.RESET_ALL}")

        # Sort by completeness
        def completeness_score(entry):
            e = entry[1]
            score = 0
            if e.get('doi'): score += 5  # Prefer entries with DOI
            if e.get('abstract'): score += 3
            if e.get('authors'): score += len(e['authors'])
            return score

        entries_sorted = sorted(entries, key=completeness_score, reverse=True)
        keep = entries_sorted[0]
        remove = entries_sorted[1:]

        click.echo(f"  Keep: {keep[1].get('filepath', 'Unknown')}")
        for hash_id, entry in remove:
            filepath = entry.get('filepath', '')
            click.echo(f"  {Fore.RED}Remove: {filepath}{Style.RESET_ALL}")

            if not dry_run:
                if hash_id in index:
                    del index[hash_id]
                    removed_count += 1

                full_path = config.workshop_root / filepath
                if full_path.exists():
                    full_path.unlink()
                    files_deleted += 1
                    click.echo(f"    Deleted file")

        click.echo()

    # Save updated index
    if not dry_run and removed_count > 0:
        save_index(index, config.index_path)

    # Summary
    total_to_remove = sum(len(entries) - 1 for _, entries in doi_duplicates) + sum(len(entries) - 1 for _, entries in title_duplicates)

    click.echo(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    if dry_run:
        print_info(f"Would remove {total_to_remove} duplicate entries")
        print_info(f"Run without --dry-run to delete duplicates")
    else:
        print_success(f"Removed {removed_count} duplicate entries from index")
        print_success(f"Deleted {files_deleted} duplicate files")
    click.echo(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")


@main.command()
@click.option("--limit", default=50, help="Maximum papers to process in one batch")
@click.option("--dry-run", is_flag=True, help="Show what would be processed without making changes")
@click.pass_context
def enrich_summaries(ctx, limit, dry_run):
    """Generate enhanced summaries for existing papers.

    Processes papers that have abstracts but no enhanced_summary.
    Creates detailed summaries with main finding, key approach, and implications.

    Cost: ~$0.003 per paper (Claude Haiku)
    """
    from literature_manager.extractors.llm import generate_paper_summary
    from literature_manager.operations import save_index

    config = ctx.obj["config"]
    index = load_index(config.index_path)

    if not index:
        print_info("No papers in library yet")
        return

    api_key = config.get("anthropic_api_key")
    if not api_key:
        print_error("Anthropic API key not configured")
        return

    model = config.get("llm_model", "claude-haiku-4-5-20251001")

    # Find papers with abstract but no enhanced_summary
    to_process = []
    for hash_id, entry in index.items():
        if entry.get("abstract") and not entry.get("enhanced_summary"):
            to_process.append((hash_id, entry))

    if not to_process:
        print_success("All papers with abstracts already have enhanced summaries!")
        return

    total = len(to_process)
    batch = to_process[:limit]

    click.echo(f"\n{Fore.CYAN}Found {total} papers needing enhanced summaries{Style.RESET_ALL}")
    click.echo(f"Processing batch of {len(batch)} papers...\n")

    if dry_run:
        print_warning("DRY RUN - No changes will be made\n")
        for i, (hash_id, entry) in enumerate(batch[:10], 1):
            title = entry.get("title", "Unknown")[:60]
            click.echo(f"  [{i}] {title}...")
        if len(batch) > 10:
            click.echo(f"  ... and {len(batch) - 10} more")
        return

    success_count = 0
    error_count = 0

    with click.progressbar(batch, label="Generating summaries") as bar:
        for hash_id, entry in bar:
            try:
                metadata = {
                    "title": entry.get("title", ""),
                    "abstract": entry.get("abstract", ""),
                }

                result = generate_paper_summary(metadata, api_key, model)

                if result.get("enhanced_summary"):
                    index[hash_id]["enhanced_summary"] = result["enhanced_summary"]
                    success_count += 1
                else:
                    error_count += 1

            except Exception as e:
                error_count += 1
                click.echo(f"\n  Error: {entry.get('title', 'Unknown')[:40]}: {e}")

    save_index(index, config.index_path)

    click.echo(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    print_success(f"Generated summaries: {success_count} papers")
    if error_count > 0:
        print_error(f"Errors: {error_count}")
    remaining = total - len(batch)
    if remaining > 0:
        print_info(f"Remaining: {remaining} papers (run again to continue)")
    click.echo(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")


@main.command()
@click.option("--limit", default=50, help="Maximum papers to process in one batch")
@click.option("--dry-run", is_flag=True, help="Show what would be processed without making changes")
@click.option("--force", is_flag=True, help="Regenerate summaries even if they exist")
@click.pass_context
def summarize_fulltext(ctx, limit, dry_run, force):
    """Generate high-quality summaries from full PDF text.

    Reads the entire PDF and creates detailed summaries with main findings,
    methods, key results, and implications.

    Cost: ~$0.008 per paper (Claude Haiku with full text)
    """
    from pathlib import Path
    from literature_manager.extractors.llm import generate_fulltext_summary
    from literature_manager.operations import save_index

    config = ctx.obj["config"]
    index = load_index(config.index_path)

    if not index:
        print_info("No papers in library yet")
        return

    api_key = config.get("anthropic_api_key")
    if not api_key:
        print_error("Anthropic API key not configured")
        return

    model = config.get("llm_model", "claude-haiku-4-5-20251001")

    # Find papers needing fulltext summaries
    to_process = []
    for hash_id, entry in index.items():
        # Skip if already has fulltext summary (unless force)
        if not force and entry.get("fulltext_summary"):
            continue

        # Need title and filepath
        if entry.get("title") and entry.get("filepath"):
            pdf_path = config.workshop_root / entry["filepath"]
            if pdf_path.exists() and not pdf_path.is_symlink():
                to_process.append((hash_id, entry, pdf_path))

    if not to_process:
        print_success("All papers already have fulltext summaries!")
        return

    total = len(to_process)
    batch = to_process[:limit]

    click.echo(f"\n{Fore.CYAN}Found {total} papers needing fulltext summaries{Style.RESET_ALL}")
    click.echo(f"Processing batch of {len(batch)} papers...")
    click.echo(f"Estimated cost: ${len(batch) * 0.008:.2f}\n")

    if dry_run:
        print_warning("DRY RUN - No changes will be made\n")
        for i, (hash_id, entry, pdf_path) in enumerate(batch[:10], 1):
            title = entry.get("title", "Unknown")[:60]
            click.echo(f"  [{i}] {title}...")
        if len(batch) > 10:
            click.echo(f"  ... and {len(batch) - 10} more")
        return

    success_count = 0
    error_count = 0
    processed_count = 0

    with click.progressbar(batch, label="Generating fulltext summaries") as bar:
        for hash_id, entry, pdf_path in bar:
            try:
                result = generate_fulltext_summary(
                    str(pdf_path),
                    entry.get("title", ""),
                    api_key,
                    model
                )

                if result:
                    index[hash_id]["fulltext_summary"] = result
                    success_count += 1
                else:
                    error_count += 1

            except Exception as e:
                error_count += 1
                click.echo(f"\n  Error: {entry.get('title', 'Unknown')[:40]}: {e}")

            processed_count += 1

            # Save every 25 papers to avoid losing progress
            if processed_count % 25 == 0:
                save_index(index, config.index_path)

    save_index(index, config.index_path)

    click.echo(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    print_success(f"Generated fulltext summaries: {success_count} papers")
    if error_count > 0:
        print_error(f"Errors: {error_count}")
    remaining = total - len(batch)
    if remaining > 0:
        print_info(f"Remaining: {remaining} papers (run again to continue)")
    click.echo(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")


@main.command()
@click.option("--limit", default=50, help="Maximum papers to process in one batch")
@click.option("--dry-run", is_flag=True, help="Show what would be processed without making changes")
@click.pass_context
def enrich(ctx, limit, dry_run):
    """Enrich existing papers with domain-specific attributes.

    Processes papers in the index that don't have domain_attributes yet.
    Uses the LLM to extract study type, methods, fractions, etc.

    Cost: ~$0.001 per paper (Claude Haiku)
    """
    from literature_manager.extractors.llm import extract_domain_attributes
    from literature_manager.operations import save_index

    config = ctx.obj["config"]
    index = load_index(config.index_path)

    if not index:
        print_info("No papers in library yet")
        return

    api_key = config.get("anthropic_api_key")
    if not api_key:
        print_error("Anthropic API key not configured")
        return

    model = config.get("llm_model", "claude-haiku-4-5-20251001")

    # Find papers without domain_attributes
    to_process = []
    for hash_id, entry in index.items():
        if not entry.get("domain_attributes"):
            # Need title and preferably abstract
            if entry.get("title"):
                to_process.append((hash_id, entry))

    if not to_process:
        print_success("All papers already have domain attributes!")
        return

    total = len(to_process)
    batch = to_process[:limit]

    click.echo(f"\n{Fore.CYAN}Found {total} papers without domain attributes{Style.RESET_ALL}")
    click.echo(f"Processing batch of {len(batch)} papers...\n")

    if dry_run:
        print_warning("DRY RUN - No changes will be made\n")
        for i, (hash_id, entry) in enumerate(batch[:10], 1):
            title = entry.get("title", "Unknown")[:60]
            click.echo(f"  [{i}] {title}...")
        if len(batch) > 10:
            click.echo(f"  ... and {len(batch) - 10} more")
        return

    # Process batch
    success_count = 0
    error_count = 0

    with click.progressbar(batch, label="Enriching papers") as bar:
        for hash_id, entry in bar:
            try:
                metadata = {
                    "title": entry.get("title", ""),
                    "abstract": entry.get("abstract", ""),
                }

                result = extract_domain_attributes(metadata, api_key, model)

                if result.get("domain_attributes"):
                    index[hash_id]["domain_attributes"] = result["domain_attributes"]
                    success_count += 1
                else:
                    error_count += 1

            except Exception as e:
                error_count += 1
                click.echo(f"\n  Error processing {entry.get('title', 'Unknown')[:40]}: {e}")

    # Save updated index
    save_index(index, config.index_path)

    # Summary
    click.echo(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    print_success(f"Enriched: {success_count} papers")
    if error_count > 0:
        print_error(f"Errors: {error_count}")
    remaining = total - len(batch)
    if remaining > 0:
        print_info(f"Remaining: {remaining} papers (run again to continue)")
    click.echo(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")


@main.command()
@click.option("--limit", default=10, help="Maximum papers to sync")
@click.option("--dry-run", is_flag=True, help="Show what would be synced without making changes")
@click.pass_context
def sync_zotero(ctx, limit, dry_run):
    """Sync enhanced summaries to Zotero as notes.

    Finds papers with enhanced_summary in the index and adds/updates
    summary notes in Zotero.
    """
    config = ctx.obj["config"]

    if not config.get("zotero_sync_enabled", False):
        print_error("Zotero sync not enabled in config")
        print_info("Set zotero_sync_enabled: true in config.yaml")
        return

    index = load_index(config.index_path)

    # Find papers with enhanced summaries
    to_sync = []
    for hash_id, entry in index.items():
        if entry.get("enhanced_summary") and entry.get("doi"):
            to_sync.append(entry)

    if not to_sync:
        print_info("No papers with enhanced summaries to sync")
        return

    batch = to_sync[:limit]
    click.echo(f"\n{Fore.CYAN}Found {len(to_sync)} papers with enhanced summaries{Style.RESET_ALL}")
    click.echo(f"Syncing batch of {len(batch)} papers to Zotero...\n")

    if dry_run:
        print_warning("DRY RUN - No changes will be made\n")
        for i, entry in enumerate(batch, 1):
            title = entry.get("title", "Unknown")[:50]
            click.echo(f"  [{i}] {title}...")
        return

    try:
        from literature_manager.zotero_sync import ZoteroSync
        zot_sync = ZoteroSync(
            api_key=config.get("zotero_api_key"),
            user_id=config.get("zotero_user_id"),
            library_type=config.get("zotero_library_type", "user")
        )
    except Exception as e:
        print_error(f"Failed to connect to Zotero: {e}")
        return

    success_count = 0
    skip_count = 0
    error_count = 0

    for entry in batch:
        title = entry.get("title", "Unknown")[:50]
        doi = entry.get("doi")
        click.echo(f"\n{Fore.CYAN}Syncing: {title}...{Style.RESET_ALL}")

        try:
            # Check if item exists in Zotero
            item_key = zot_sync.check_exists(doi=doi)

            if not item_key:
                print_warning(f"  Not in Zotero, skipping")
                skip_count += 1
                continue

            # Add summary note
            zot_sync._add_summary_note(item_key, entry)
            success_count += 1

        except Exception as e:
            print_error(f"  Error: {e}")
            error_count += 1

    click.echo(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    print_success(f"Synced: {success_count} papers")
    if skip_count > 0:
        print_info(f"Skipped (not in Zotero): {skip_count}")
    if error_count > 0:
        print_error(f"Errors: {error_count}")
    click.echo(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")


@main.command()
@click.option("--missing-authors", is_flag=True, help="Reprocess papers with no authors")
@click.option("--missing-doi", is_flag=True, help="Reprocess papers with no DOI")
@click.option("--method", type=str, help="Reprocess papers extracted with specific method (e.g., 'pdf_metadata')")
@click.option("--hash", type=str, help="Reprocess specific paper by hash ID")
@click.option("--limit", default=10, help="Maximum papers to reprocess")
@click.option("--dry-run", is_flag=True, help="Show what would be reprocessed")
@click.pass_context
def reprocess(ctx, missing_authors, missing_doi, method, hash, limit, dry_run):
    """Reprocess papers with poor metadata extraction.

    Re-runs metadata extraction on papers that had incomplete data,
    using the improved DOI extraction. Updates the index and optionally
    renames/moves files.

    Examples:
        literature-manager reprocess --missing-authors --limit 5
        literature-manager reprocess --method pdf_metadata
        literature-manager reprocess --hash abc123...
    """
    config = ctx.obj["config"]
    index = load_index(config.index_path)

    # Find papers to reprocess
    to_reprocess = []

    for hash_id, entry in index.items():
        # Specific hash requested
        if hash and not hash_id.startswith(hash):
            continue

        # Filter by criteria
        if missing_authors:
            authors = entry.get("authors", [])
            if authors and len(authors) > 0:
                continue
        if missing_doi:
            if entry.get("doi"):
                continue
        if method:
            if entry.get("extraction_method") != method:
                continue

        # At least one filter must match (unless specific hash)
        if not hash and not missing_authors and not missing_doi and not method:
            click.echo("Specify at least one filter: --missing-authors, --missing-doi, --method, or --hash")
            return

        to_reprocess.append((hash_id, entry))

    if not to_reprocess:
        print_info("No papers match the criteria")
        return

    batch = to_reprocess[:limit]
    click.echo(f"\n{Fore.CYAN}Found {len(to_reprocess)} papers to reprocess{Style.RESET_ALL}")
    click.echo(f"Processing batch of {len(batch)}...\n")

    if dry_run:
        print_warning("DRY RUN - No changes will be made\n")
        for i, (hash_id, entry) in enumerate(batch, 1):
            title = entry.get("title", "Unknown")[:50]
            method_used = entry.get("extraction_method", "unknown")
            authors = entry.get("authors", [])
            doi = entry.get("doi", "")
            click.echo(f"  [{i}] {title}...")
            click.echo(f"      Method: {method_used}, Authors: {len(authors)}, DOI: {'Yes' if doi else 'No'}")
            click.echo(f"      File: {entry.get('filepath', 'unknown')}")
        return

    success_count = 0
    error_count = 0
    unchanged_count = 0

    for hash_id, entry in batch:
        old_title = entry.get("title", "Unknown")[:50]
        filepath = entry.get("filepath", "")
        click.echo(f"\n{Fore.CYAN}Reprocessing: {old_title}...{Style.RESET_ALL}")

        # Find the actual file
        full_path = config.workshop_root / filepath
        if not full_path.exists():
            print_error(f"  File not found: {filepath}")
            error_count += 1
            continue

        try:
            # Re-extract metadata
            new_metadata = extract_metadata(full_path, config)

            # Check if extraction improved
            old_authors = entry.get("authors", [])
            new_authors = new_metadata.get("authors", [])
            old_doi = entry.get("doi", "")
            new_doi = new_metadata.get("doi", "")
            old_method = entry.get("extraction_method", "")
            new_method = new_metadata.get("extraction_method", "")

            improved = False
            changes = []

            # Check for improvements
            if not old_authors and new_authors:
                improved = True
                changes.append(f"Authors: [] → {new_authors[:3]}")
            if not old_doi and new_doi:
                improved = True
                changes.append(f"DOI: None → {new_doi}")
            if old_method == "pdf_metadata" and new_method in ("doi_lookup", "llm_parsing"):
                improved = True
                changes.append(f"Method: {old_method} → {new_method}")

            # Also check if title improved (from PII/untitled to real title)
            old_title = entry.get("title", "").lower()
            new_title = new_metadata.get("title", "")
            bad_title_indicators = ["pii:", "untitled", ".indd"]
            if any(bad in old_title for bad in bad_title_indicators) and new_title and len(new_title) > 20:
                improved = True
                changes.append(f"Title: {entry.get('title', '')[:30]}... → {new_title[:50]}...")

            if not improved:
                print_info(f"  No improvement found")
                unchanged_count += 1
                continue

            # Log changes
            for change in changes:
                print_success(f"  {change}")

            # Update index entry
            entry.update({
                "title": new_metadata.get("title", entry.get("title")),
                "authors": new_metadata.get("authors", entry.get("authors")),
                "year": new_metadata.get("year", entry.get("year")),
                "doi": new_metadata.get("doi", entry.get("doi")),
                "abstract": new_metadata.get("abstract", entry.get("abstract")),
                "keywords": new_metadata.get("keywords", entry.get("keywords", [])),
                "extraction_method": new_method,
                "extraction_confidence": new_metadata.get("extraction_confidence", 0.0),
                "journal": new_metadata.get("journal"),
                "volume": new_metadata.get("volume"),
                "issue": new_metadata.get("issue"),
                "pages": new_metadata.get("pages"),
                "summary": new_metadata.get("summary", entry.get("summary", "")),
            })

            # Add enhanced summary if generated
            if new_metadata.get("enhanced_summary"):
                entry["enhanced_summary"] = new_metadata["enhanced_summary"]

            # Add domain attributes if extracted
            if new_metadata.get("domain_attributes"):
                entry["domain_attributes"] = new_metadata["domain_attributes"]

            index[hash_id] = entry
            success_count += 1

        except Exception as e:
            print_error(f"  Error: {e}")
            error_count += 1

    # Save updated index
    from literature_manager.operations import save_index
    save_index(index, config.index_path)

    click.echo(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    print_success(f"Improved: {success_count} papers")
    if unchanged_count > 0:
        print_info(f"No improvement: {unchanged_count}")
    if error_count > 0:
        print_error(f"Errors: {error_count}")

    remaining = len(to_reprocess) - len(batch)
    if remaining > 0:
        print_info(f"Remaining: {remaining} papers (run again to continue)")
    click.echo(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")


def parse_author_from_filename(filename: str) -> tuple:
    """
    Extract author and year from filename patterns.

    Common patterns:
    - 'Author et al. - Year - Title.pdf'
    - 'Author and Coauthor - Year - Title.pdf'
    - 'Author - Year - Title.pdf'
    - 'Author, Year - Title.pdf'
    - 'Author et al., Year.pdf'

    Returns:
        Tuple of (author_string, year) or (None, None) if not found
    """
    import re

    # Pattern 1: 'Author(s) - Year - ...' or 'Author(s), Year - ...'
    # Also handles typos like "et l." for "et al."
    match = re.match(
        r'^([A-Z][a-zA-Z\']+(?:\s+(?:et al\.?|et l\.?|and|&)\s*[A-Z]?[a-zA-Z\']*)?)\s*[-_,]\s*((?:19|20)\d{2})',
        filename
    )
    if match:
        return match.group(1).strip(), int(match.group(2))

    # Pattern 2: 'Author and Coauthor, Year' (without dash)
    match = re.match(
        r'^([A-Z][a-zA-Z\']+\s+and\s+[A-Z][a-zA-Z\']+)[,\s]+((?:19|20)\d{2})',
        filename
    )
    if match:
        return match.group(1).strip(), int(match.group(2))

    # Pattern 3: 'Author_Year_...'
    match = re.match(r'^([A-Z][a-zA-Z\']+)_((?:19|20)\d{2})_', filename)
    if match:
        return match.group(1).strip(), int(match.group(2))

    # Pattern 4: Just 'Year - Title' at start (for correction notices, etc.)
    # Skip these - no author info

    return None, None


def format_author_string(author_str: str) -> list:
    """
    Convert 'Author et al.' or 'Author and Coauthor' to list format.

    Args:
        author_str: Author string from filename

    Returns:
        List of author names in 'Last, F.' format
    """
    if not author_str:
        return []

    # Handle 'et al.' - just return first author
    if 'et al' in author_str.lower():
        first_author = author_str.split()[0]
        return [f"{first_author}, et al."]

    # Handle 'Author and Coauthor'
    if ' and ' in author_str:
        parts = author_str.split(' and ')
        return [p.strip() for p in parts if p.strip()]

    # Single author
    return [author_str]


@main.command()
@click.option("--dry-run", is_flag=True, help="Show what would be repaired")
@click.pass_context
def repair_from_filename(ctx, dry_run):
    """Repair missing author/year from original filename.

    Many papers have author and year embedded in their original filename
    (e.g., 'Author et al. - 2020 - Title.pdf'). This command extracts
    that information for papers where other methods failed.
    """
    config = ctx.obj["config"]
    index = load_index(config.index_path)

    # Find papers that can be repaired
    to_repair = []
    for hash_id, entry in index.items():
        authors = entry.get("authors", [])
        if authors and len(authors) > 0:
            continue  # Already has authors

        orig_filename = entry.get("original_filename", "")
        author_str, year = parse_author_from_filename(orig_filename)

        if author_str:
            to_repair.append({
                "hash": hash_id,
                "entry": entry,
                "author_str": author_str,
                "year": year,
                "orig_filename": orig_filename
            })

    if not to_repair:
        print_info("No papers found that can be repaired from filename")
        return

    click.echo(f"\n{Fore.CYAN}Found {len(to_repair)} papers to repair from filename{Style.RESET_ALL}\n")

    if dry_run:
        print_warning("DRY RUN - No changes will be made\n")
        for item in to_repair:
            click.echo(f"  {item['orig_filename'][:60]}...")
            click.echo(f"    → Authors: {item['author_str']}")
            click.echo(f"    → Year: {item['year']}")
            click.echo()
        return

    repaired = 0
    for item in to_repair:
        entry = item["entry"]
        hash_id = item["hash"]

        # Format authors
        authors = format_author_string(item["author_str"])

        # Update entry
        entry["authors"] = authors
        if item["year"] and not entry.get("year"):
            entry["year"] = item["year"]

        index[hash_id] = entry
        repaired += 1

        title = entry.get("title", "")[:40]
        print_success(f"Repaired: {item['author_str']} ({item['year']}) - {title}...")

    # Save index
    from literature_manager.operations import save_index
    save_index(index, config.index_path)

    click.echo(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    print_success(f"Repaired {repaired} papers from filename")
    click.echo(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")


@main.command()
@click.option("--limit", default=50, help="Maximum papers to process")
@click.option("--dry-run", is_flag=True, help="Show what would be updated")
@click.pass_context
def backfill_citations(ctx, limit, dry_run):
    """Backfill missing citation metadata (journal, volume, pages) from CrossRef.

    For papers that have DOIs but are missing publication details needed
    for proper citations, this re-queries CrossRef to get journal, volume,
    issue, and page information.
    """
    import time
    from literature_manager.extractors.doi import lookup_doi_metadata

    config = ctx.obj["config"]
    index = load_index(config.index_path)

    # Find papers with DOI but missing citation fields
    to_update = []
    for hash_id, entry in index.items():
        doi = entry.get("doi")
        if not doi:
            continue

        # Check if missing any citation field
        if not entry.get("journal") or not entry.get("volume") or not entry.get("pages"):
            to_update.append((hash_id, entry))

    if not to_update:
        print_info("All papers with DOIs already have citation metadata")
        return

    batch = to_update[:limit]
    click.echo(f"\n{Fore.CYAN}Found {len(to_update)} papers missing citation metadata{Style.RESET_ALL}")
    click.echo(f"Processing batch of {len(batch)}...\n")

    if dry_run:
        print_warning("DRY RUN - No changes will be made\n")
        for i, (hash_id, entry) in enumerate(batch[:10], 1):
            title = entry.get("title", "Unknown")[:50]
            doi = entry.get("doi", "")
            click.echo(f"  [{i}] {title}...")
            click.echo(f"      DOI: {doi}")
        if len(batch) > 10:
            click.echo(f"  ... and {len(batch) - 10} more")
        return

    success_count = 0
    skip_count = 0
    error_count = 0

    for hash_id, entry in batch:
        doi = entry.get("doi")
        title = entry.get("title", "Unknown")[:40]

        try:
            # Query CrossRef
            metadata = lookup_doi_metadata(doi)

            if not metadata:
                print_warning(f"  {title}... - CrossRef lookup failed")
                skip_count += 1
                continue

            # Update fields
            updated = False
            updates = []

            if metadata.get("journal") and not entry.get("journal"):
                entry["journal"] = metadata["journal"]
                updated = True
                updates.append(f"journal={metadata['journal'][:20]}")

            if metadata.get("volume") and not entry.get("volume"):
                entry["volume"] = metadata["volume"]
                updated = True
                updates.append(f"vol={metadata['volume']}")

            if metadata.get("issue") and not entry.get("issue"):
                entry["issue"] = metadata["issue"]
                updated = True

            if metadata.get("pages") and not entry.get("pages"):
                entry["pages"] = metadata["pages"]
                updated = True
                updates.append(f"pp={metadata['pages']}")

            if metadata.get("issn") and not entry.get("issn"):
                entry["issn"] = metadata["issn"]

            if updated:
                index[hash_id] = entry
                success_count += 1
                print_success(f"  {title}... ({', '.join(updates)})")
            else:
                skip_count += 1

            # Be nice to CrossRef API
            time.sleep(0.1)

        except Exception as e:
            print_error(f"  {title}... - Error: {e}")
            error_count += 1

    # Save index
    from literature_manager.operations import save_index
    save_index(index, config.index_path)

    click.echo(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    print_success(f"Updated: {success_count} papers")
    if skip_count > 0:
        print_info(f"Skipped (no new data): {skip_count}")
    if error_count > 0:
        print_error(f"Errors: {error_count}")

    remaining = len(to_update) - len(batch)
    if remaining > 0:
        print_info(f"Remaining: {remaining} papers (run again to continue)")
    click.echo(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")


@main.command()
@click.option("--limit", default=50, help="Maximum papers to process")
@click.option("--dry-run", is_flag=True, help="Show what would be updated")
@click.pass_context
def zotero_update_citations(ctx, limit, dry_run):
    """Update existing Zotero items with citation metadata from the index.

    For papers already in Zotero that are missing journal, volume, issue, or pages,
    this command pushes the updated metadata from the local index to Zotero.
    Run this after backfill-citations to sync citation data to Zotero.
    """
    import time
    from dotenv import load_dotenv
    from literature_manager.zotero_sync import ZoteroSync

    load_dotenv()

    config = ctx.obj["config"]
    index = load_index(config.index_path)

    # Find papers with citation metadata in index
    candidates = []
    for hash_id, entry in index.items():
        doi = entry.get("doi")
        if not doi:
            continue

        # Must have some citation metadata to push
        if entry.get("journal") or entry.get("volume") or entry.get("pages"):
            candidates.append((hash_id, entry))

    if not candidates:
        print_info("No papers with citation metadata to sync")
        return

    batch = candidates[:limit]
    click.echo(f"\n{Fore.CYAN}Found {len(candidates)} papers with citation metadata{Style.RESET_ALL}")
    click.echo(f"Processing batch of {len(batch)}...\n")

    if dry_run:
        print_warning("DRY RUN - No changes will be made\n")
        for i, (hash_id, entry) in enumerate(batch[:10], 1):
            title = entry.get("title", "Unknown")[:50]
            journal = entry.get("journal", "")[:25] or "none"
            vol = entry.get("volume", "") or "?"
            pp = entry.get("pages", "") or "?"
            click.echo(f"  [{i}] {title}...")
            click.echo(f"      {journal}, {vol}, {pp}")
        if len(batch) > 10:
            click.echo(f"  ... and {len(batch) - 10} more")
        return

    # Initialize Zotero
    try:
        zotero_sync = ZoteroSync()
    except ValueError as e:
        print_error(f"Zotero not configured: {e}")
        return

    success_count = 0
    not_in_zotero = 0
    no_update_needed = 0
    error_count = 0

    for hash_id, entry in batch:
        doi = entry.get("doi")
        title = entry.get("title", "Unknown")[:40]

        try:
            result = zotero_sync.update_citation_metadata(
                doi=doi,
                journal=entry.get("journal"),
                volume=entry.get("volume"),
                issue=entry.get("issue"),
                pages=entry.get("pages"),
            )

            if result:
                journal = entry.get("journal", "")[:20] or "?"
                vol = entry.get("volume", "") or "?"
                pp = entry.get("pages", "") or "?"
                print_success(f"  {title}... ({journal}, {vol}, {pp})")
                success_count += 1
            elif result is False:
                # Item exists but no update needed
                no_update_needed += 1
            else:
                # DOI not found in Zotero
                not_in_zotero += 1

            # Rate limit Zotero API
            time.sleep(0.2)

        except Exception as e:
            print_error(f"  {title}... - Error: {e}")
            error_count += 1

    click.echo(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    print_success(f"Updated in Zotero: {success_count} papers")
    if no_update_needed > 0:
        print_info(f"Already up to date: {no_update_needed}")
    if not_in_zotero > 0:
        print_info(f"Not in Zotero (by DOI): {not_in_zotero}")
    if error_count > 0:
        print_error(f"Errors: {error_count}")

    remaining = len(candidates) - len(batch)
    if remaining > 0:
        print_info(f"Remaining: {remaining} papers (run again to continue)")
    click.echo(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")


@main.command()
@click.option("--dry-run", is_flag=True, help="Show duplicates without deleting")
@click.pass_context
def zotero_dedup(ctx, dry_run):
    """Find and remove duplicate items from Zotero library.

    Identifies duplicates by DOI and title, keeps the most complete version,
    and deletes the extras. Uses metadata completeness scoring to decide
    which version to keep.
    """
    import time
    from collections import defaultdict
    from dotenv import load_dotenv
    from literature_manager.zotero_sync import ZoteroSync

    load_dotenv()

    click.echo(f"\n{Fore.CYAN}Scanning Zotero library for duplicates...{Style.RESET_ALL}\n")

    # Initialize Zotero
    try:
        zotero_sync = ZoteroSync()
    except ValueError as e:
        print_error(f"Zotero not configured: {e}")
        return

    # Fetch all items
    start = 0
    limit = 100
    all_items = []

    while True:
        items = zotero_sync.zot.items(start=start, limit=limit)
        if not items:
            break
        all_items.extend(items)
        start += limit
        if len(items) < limit:
            break

    click.echo(f"Found {len(all_items)} total items in Zotero\n")

    # Group by DOI
    doi_groups = defaultdict(list)
    for item in all_items:
        if item['data'].get('itemType') in ['attachment', 'note']:
            continue
        doi = item['data'].get('DOI', '').strip().lower()
        if doi:
            doi = doi.replace('https://doi.org/', '').replace('http://doi.org/', '')
            doi_groups[doi].append(item)

    # Group by title (for items without DOI)
    title_groups = defaultdict(list)
    items_with_doi = {item['key'] for items in doi_groups.values() for item in items}
    for item in all_items:
        if item['data'].get('itemType') in ['attachment', 'note']:
            continue
        if item['key'] in items_with_doi:
            continue  # Skip items already grouped by DOI
        title = item['data'].get('title', '').strip().lower()
        if title and len(title) > 10:
            title_groups[title].append(item)

    # Find duplicates
    doi_dups = {k: v for k, v in doi_groups.items() if len(v) > 1}
    title_dups = {k: v for k, v in title_groups.items() if len(v) > 1}

    total_dups = len(doi_dups) + len(title_dups)

    if total_dups == 0:
        print_success("No duplicates found!")
        return

    click.echo(f"{Fore.YELLOW}Found {len(doi_dups)} DOI duplicates and {len(title_dups)} title duplicates{Style.RESET_ALL}\n")

    if dry_run:
        print_warning("DRY RUN - No changes will be made\n")

    def completeness_score(item):
        """Score item by metadata completeness."""
        data = item['data']
        score = 0
        if data.get('abstractNote'): score += 3
        if data.get('creators'): score += len(data['creators'])
        if data.get('publicationTitle'): score += 2
        if data.get('volume'): score += 1
        if data.get('pages'): score += 1
        if data.get('DOI'): score += 2
        if data.get('date'): score += 1
        if data.get('tags'): score += len(data['tags'])
        return score

    deleted_count = 0
    error_count = 0

    # Process DOI duplicates
    for doi, items in doi_dups.items():
        # Sort by completeness
        items_sorted = sorted(items, key=completeness_score, reverse=True)
        keep = items_sorted[0]
        to_delete = items_sorted[1:]

        title = keep['data'].get('title', 'Unknown')[:50]
        click.echo(f"{Fore.CYAN}DOI: {doi}{Style.RESET_ALL}")
        click.echo(f"  Keep: {title}... (score: {completeness_score(keep)})")

        for item in to_delete:
            item_title = item['data'].get('title', 'Unknown')[:40]
            click.echo(f"  {Fore.RED}Delete: {item_title}... (score: {completeness_score(item)}){Style.RESET_ALL}")

            if not dry_run:
                try:
                    # Re-fetch item to get current version
                    fresh_item = zotero_sync.zot.item(item['key'])
                    zotero_sync.zot.delete_item(fresh_item)
                    deleted_count += 1
                    time.sleep(0.2)  # Rate limit
                except Exception as e:
                    print_error(f"    Failed to delete: {e}")
                    error_count += 1

    # Process title duplicates
    for title, items in title_dups.items():
        # Sort by completeness
        items_sorted = sorted(items, key=completeness_score, reverse=True)
        keep = items_sorted[0]
        to_delete = items_sorted[1:]

        display_title = title[:50]
        click.echo(f"{Fore.CYAN}Title: {display_title}...{Style.RESET_ALL}")
        click.echo(f"  Keep: score {completeness_score(keep)}")

        for item in to_delete:
            click.echo(f"  {Fore.RED}Delete: score {completeness_score(item)}{Style.RESET_ALL}")

            if not dry_run:
                try:
                    # Re-fetch item to get current version
                    fresh_item = zotero_sync.zot.item(item['key'])
                    zotero_sync.zot.delete_item(fresh_item)
                    deleted_count += 1
                    time.sleep(0.2)  # Rate limit
                except Exception as e:
                    print_error(f"    Failed to delete: {e}")
                    error_count += 1

    click.echo(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    if dry_run:
        total_to_delete = sum(len(v) - 1 for v in doi_dups.values()) + sum(len(v) - 1 for v in title_dups.values())
        print_info(f"Would delete: {total_to_delete} duplicate items")
    else:
        print_success(f"Deleted: {deleted_count} duplicate items")
        if error_count > 0:
            print_error(f"Errors: {error_count}")
    click.echo(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")


@main.command()
@click.option("--limit", default=50, help="Maximum papers to process")
@click.option("--dry-run", is_flag=True, help="Show what would be looked up without making changes")
@click.option("--upload-zotero", is_flag=True, help="Upload newly found DOIs to Zotero")
@click.pass_context
def backfill_dois(ctx, limit, dry_run, upload_zotero):
    """Find DOIs for papers missing them by searching CrossRef.

    Searches CrossRef by title/author/year to find DOIs for papers that
    were processed without DOI extraction working. Updates the local index
    and optionally uploads to Zotero.

    Cost: Free (CrossRef API)
    """
    import time
    import requests
    from urllib.parse import quote

    config = ctx.obj["config"]
    index = load_index(config.index_path)

    if not index:
        print_info("No papers in library yet")
        return

    # Find papers without DOIs
    to_process = []
    for hash_id, entry in index.items():
        if not entry.get("doi"):
            # Need title at minimum
            if entry.get("title"):
                to_process.append((hash_id, entry))

    if not to_process:
        print_success("All papers already have DOIs!")
        return

    batch = to_process[:limit]
    click.echo(f"\n{Fore.CYAN}Found {len(to_process)} papers without DOIs{Style.RESET_ALL}")
    click.echo(f"Processing batch of {len(batch)}...\n")

    if dry_run:
        print_warning("DRY RUN - No changes will be made\n")
        for i, (hash_id, entry) in enumerate(batch[:15], 1):
            title = entry.get("title", "Unknown")[:60]
            year = entry.get("year", "?")
            authors = entry.get("authors", [])
            author_str = authors[0] if authors else "Unknown"
            click.echo(f"  [{i}] {author_str} ({year}): {title}...")
        if len(batch) > 15:
            click.echo(f"  ... and {len(batch) - 15} more")
        return

    def search_crossref(title: str, authors: list = None, year: int = None) -> Optional[str]:
        """Search CrossRef for a DOI matching the given metadata."""
        # Build query
        query_parts = [title]

        # Add first author if available
        if authors and len(authors) > 0:
            first_author = authors[0]
            # Extract last name only (handles "Last, F." format)
            if "," in first_author:
                last_name = first_author.split(",")[0].strip()
            else:
                last_name = first_author.split()[0] if " " in first_author else first_author
            query_parts.append(last_name)

        query = " ".join(query_parts)

        # CrossRef API
        url = f"https://api.crossref.org/works"
        params = {
            "query": query,
            "rows": 5,
            "select": "DOI,title,author,issued"
        }

        # Add year filter if available
        if year:
            params["filter"] = f"from-pub-date:{year-1},until-pub-date:{year+1}"

        headers = {
            "User-Agent": "LiteratureManager/1.0 (mailto:sam.leuthold@gmail.com)"
        }

        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()

            items = data.get("message", {}).get("items", [])
            if not items:
                return None

            # Check each result for title similarity
            title_lower = title.lower().strip()
            for item in items:
                item_titles = item.get("title", [])
                if not item_titles:
                    continue

                item_title = item_titles[0].lower().strip()

                # Simple similarity check - titles should be very similar
                # Remove punctuation for comparison
                import re
                title_clean = re.sub(r'[^\w\s]', '', title_lower)
                item_clean = re.sub(r'[^\w\s]', '', item_title)

                # Check if titles are similar enough (80% character overlap)
                shorter = min(len(title_clean), len(item_clean))
                longer = max(len(title_clean), len(item_clean))

                if shorter == 0:
                    continue

                # Calculate simple overlap
                if longer > 0 and shorter / longer > 0.7:
                    # Check word overlap
                    title_words = set(title_clean.split())
                    item_words = set(item_clean.split())
                    common = title_words & item_words
                    total = title_words | item_words

                    if len(total) > 0 and len(common) / len(total) > 0.6:
                        return item.get("DOI")

            return None

        except Exception as e:
            return None

    success_count = 0
    not_found_count = 0
    error_count = 0

    # Optional: Zotero sync
    zot_sync = None
    if upload_zotero:
        try:
            from literature_manager.zotero_sync import ZoteroSync
            zot_sync = ZoteroSync()
            click.echo("Zotero upload enabled\n")
        except Exception as e:
            print_warning(f"Zotero not available: {e}\n")

    with click.progressbar(batch, label="Searching CrossRef") as bar:
        for hash_id, entry in bar:
            title = entry.get("title", "")
            authors = entry.get("authors", [])
            year = entry.get("year")

            try:
                doi = search_crossref(title, authors, year)

                if doi:
                    # Update index
                    entry["doi"] = doi
                    index[hash_id] = entry

                    title_short = title[:40]
                    click.echo(f"\n  {Fore.GREEN}✓{Style.RESET_ALL} Found: {title_short}... → {doi}")
                    success_count += 1

                    # Optional: Upload to Zotero
                    if zot_sync:
                        try:
                            filepath = entry.get("filepath")
                            if filepath:
                                full_path = config.workshop_root / filepath
                                if full_path.exists():
                                    topics = entry.get("topics", [])
                                    zot_sync.upload_paper(entry, full_path, topics)
                                    click.echo(f"    → Uploaded to Zotero")
                        except Exception as e:
                            click.echo(f"    → Zotero upload failed: {e}")

                else:
                    not_found_count += 1

                # Rate limit CrossRef (be polite)
                time.sleep(0.3)

            except Exception as e:
                error_count += 1

    # Save index
    from literature_manager.operations import save_index
    save_index(index, config.index_path)

    click.echo(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    print_success(f"Found DOIs: {success_count} papers")
    if not_found_count > 0:
        print_info(f"No DOI found: {not_found_count}")
    if error_count > 0:
        print_error(f"Errors: {error_count}")

    remaining = len(to_process) - len(batch)
    if remaining > 0:
        print_info(f"Remaining: {remaining} papers (run again to continue)")
    click.echo(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")


@main.command()
@click.option("--limit", default=1000, help="Maximum papers to process in one batch")
@click.option("--dry-run", is_flag=True, help="Show what would be updated without making changes")
@click.option("--force", is_flag=True, help="Update notes even if they already exist")
@click.pass_context
def zotero_update_summaries(ctx, limit, dry_run, force):
    """Push fulltext summaries to Zotero as notes.

    Finds papers with fulltext_summary in the local index and creates/updates
    corresponding notes in Zotero. Matches papers by DOI.
    """
    import time
    from dotenv import load_dotenv
    from literature_manager.zotero_sync import ZoteroSync

    load_dotenv()

    config = ctx.obj["config"]
    index = load_index(config.index_path)

    click.echo(f"\n{Fore.CYAN}Pushing fulltext summaries to Zotero...{Style.RESET_ALL}\n")

    # Initialize Zotero
    try:
        zotero_sync = ZoteroSync()
    except ValueError as e:
        print_error(f"Zotero not configured: {e}")
        return

    # Build DOI cache to know which papers are in Zotero
    click.echo("Building Zotero DOI cache...")
    zotero_sync._build_doi_cache()
    zotero_dois = set(zotero_sync._doi_cache.keys())
    click.echo(f"Found {len(zotero_dois)} papers in Zotero\n")

    # Find papers with fulltext summaries and DOIs that exist in Zotero
    candidates = []
    skipped_not_in_zotero = 0

    for hash_id, entry in index.items():
        fulltext_summary = entry.get("fulltext_summary")
        doi = entry.get("doi")

        if fulltext_summary and doi:
            # Check all required fields
            if (fulltext_summary.get("main_finding") or
                fulltext_summary.get("key_approach") or
                fulltext_summary.get("key_results")):

                # Normalize DOI and check if in Zotero
                doi_normalized = doi.strip().lower()
                doi_normalized = doi_normalized.replace('https://doi.org/', '').replace('http://doi.org/', '')

                if doi_normalized in zotero_dois:
                    candidates.append((hash_id, entry))
                else:
                    skipped_not_in_zotero += 1

    click.echo(f"Found {len(candidates)} papers with fulltext summaries ready to update")
    if skipped_not_in_zotero > 0:
        click.echo(f"  (Skipping {skipped_not_in_zotero} papers not in Zotero)\n")

    if not candidates:
        print_warning("No papers with fulltext summaries found")
        return

    # Limit batch size
    batch = candidates[:limit]
    if len(candidates) > limit:
        click.echo(f"Processing first {limit} papers (use --limit to adjust)\n")

    if dry_run:
        print_warning("DRY RUN - No changes will be made\n")

    success_count = 0
    skip_count = 0
    error_count = 0

    with click.progressbar(batch, label="Updating Zotero notes") as bar:
        for hash_id, entry in bar:
            doi = entry.get("doi")
            title = entry.get("title", "Unknown")[:50]
            fulltext_summary = entry.get("fulltext_summary")

            if dry_run:
                success_count += 1
                continue

            try:
                result = zotero_sync.add_or_update_fulltext_note(
                    doi=doi,
                    fulltext_summary=fulltext_summary,
                    title=title
                )

                if result:
                    success_count += 1
                else:
                    skip_count += 1

                # Rate limit to avoid API issues
                time.sleep(0.3)

            except Exception as e:
                error_count += 1
                click.echo(f"\n  Error: {title}: {e}")

    click.echo(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    if dry_run:
        print_info(f"Would update: {success_count} Zotero notes")
    else:
        print_success(f"Updated: {success_count} Zotero notes")
        if skip_count > 0:
            print_info(f"Skipped (not in Zotero): {skip_count}")
        if error_count > 0:
            print_error(f"Errors: {error_count}")

    remaining = len(candidates) - limit
    if remaining > 0:
        print_info(f"Remaining: {remaining} papers (run again to continue)")
    click.echo(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")


@main.command()
@click.option("--dry-run", is_flag=True, help="Show what would be repaired without making changes")
@click.pass_context
def repair_metadata(ctx, dry_run):
    """Repair papers with garbled titles or wrong years.

    Finds papers with PII titles, very short titles, or year=1910.
    Extracts DOI from PDF text and looks up CrossRef for proper metadata.
    Updates the index and renames files with correct information.

    Cost: Free (CrossRef API) + small LLM cost for DOI extraction
    """
    import re
    import time
    import shutil
    import requests
    import PyPDF2
    from literature_manager.operations import save_index

    config = ctx.obj["config"]
    index = load_index(config.index_path)

    if not index:
        print_info("No papers in library yet")
        return

    # Find papers needing repair
    to_repair = []
    for hash_id, entry in index.items():
        title = entry.get("title", "")
        year = entry.get("year", 0)

        needs_repair = False
        reason = []

        # PII title
        if title.startswith("PII:") or title.startswith("S00"):
            needs_repair = True
            reason.append("PII title")

        # Year 1910 (likely parsing error)
        if year == 1910:
            needs_repair = True
            reason.append("year=1910")

        # Very short title
        if len(title) < 15 and not title.startswith("PII"):
            needs_repair = True
            reason.append("short title")

        # Garbled titles (journal codes, page numbers)
        if re.match(r'^[\d\s\.]+$', title) or "Ecosystems 2*" in title or ".." in title:
            needs_repair = True
            reason.append("garbled title")

        if needs_repair:
            to_repair.append((hash_id, entry, reason))

    if not to_repair:
        print_success("No papers need metadata repair!")
        return

    click.echo(f"\n{Fore.CYAN}Found {len(to_repair)} papers needing repair{Style.RESET_ALL}\n")

    if dry_run:
        print_warning("DRY RUN - No changes will be made\n")
        for hash_id, entry, reasons in to_repair:
            title = entry.get("title", "Unknown")[:50]
            year = entry.get("year", "?")
            filepath = entry.get("filepath", "")
            reason_str = ", ".join(reasons)
            click.echo(f"  • {title}... ({year})")
            click.echo(f"    Reason: {reason_str}")
            click.echo(f"    File: {filepath[-60:] if filepath else 'N/A'}")
        return

    def extract_doi_from_pdf(pdf_path):
        """Extract DOI from PDF text."""
        try:
            with open(pdf_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                # Check first 3 pages
                text = ""
                for i in range(min(3, len(reader.pages))):
                    text += reader.pages[i].extract_text() or ""

                # DOI patterns
                patterns = [
                    r'(?:doi[:\s]*)?10\.\d{4,}/[^\s\]>]+',
                    r'https?://(?:dx\.)?doi\.org/10\.\d{4,}/[^\s\]>]+',
                ]

                for pattern in patterns:
                    matches = re.findall(pattern, text, re.IGNORECASE)
                    if matches:
                        # Clean up DOI
                        doi = matches[0]
                        doi = re.sub(r'^(?:doi[:\s]*)', '', doi, flags=re.IGNORECASE)
                        doi = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', doi, flags=re.IGNORECASE)
                        # Remove trailing punctuation
                        doi = doi.rstrip('.,;:')
                        return doi

                return None
        except Exception:
            return None

    def lookup_crossref(doi):
        """Look up metadata from CrossRef."""
        url = f"https://api.crossref.org/works/{doi}"
        headers = {"User-Agent": "LiteratureManager/1.0 (mailto:sam.leuthold@gmail.com)"}

        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()

            work = data.get("message", {})

            # Extract metadata
            title = work.get("title", [])
            title = title[0] if title else None

            authors = []
            for author in work.get("author", [])[:5]:
                family = author.get("family", "")
                given = author.get("given", "")
                if family:
                    initial = given[0] if given else ""
                    authors.append(f"{family}, {initial}." if initial else family)

            year = None
            issued = work.get("issued", {}).get("date-parts", [[]])
            if issued and issued[0]:
                year = issued[0][0]

            return {
                "title": title,
                "authors": authors,
                "year": year,
                "doi": doi
            }
        except Exception:
            return None

    repaired_count = 0
    not_found_count = 0
    error_count = 0

    for hash_id, entry, reasons in to_repair:
        filepath = entry.get("filepath", "")
        if not filepath:
            error_count += 1
            continue

        full_path = config.workshop_root / filepath
        if not full_path.exists():
            click.echo(f"\n  {Fore.RED}✗{Style.RESET_ALL} File not found: {filepath[-50:]}")
            error_count += 1
            continue

        title = entry.get("title", "Unknown")[:40]
        click.echo(f"\n  Processing: {title}...")
        click.echo(f"    Reason: {', '.join(reasons)}")

        # Try to extract DOI from PDF
        doi = extract_doi_from_pdf(full_path)

        # If no DOI but title is a PII, convert PII to DOI
        if not doi and (title.startswith("PII:") or title.startswith("S00")):
            pii_match = re.search(r'S[\d\-X\(\)]+', title)
            if pii_match:
                pii = pii_match.group()
                doi = f"10.1016/{pii}"
                click.echo(f"    Converted PII to DOI: {doi}")

        if not doi:
            click.echo(f"    {Fore.YELLOW}⚠{Style.RESET_ALL} No DOI found in PDF")
            not_found_count += 1
            continue

        click.echo(f"    Found DOI: {doi}")

        # Look up CrossRef
        metadata = lookup_crossref(doi)

        if not metadata or not metadata.get("title"):
            click.echo(f"    {Fore.YELLOW}⚠{Style.RESET_ALL} CrossRef lookup failed")
            not_found_count += 1
            time.sleep(0.3)
            continue

        new_title = metadata["title"]
        new_authors = metadata.get("authors", [])
        new_year = metadata.get("year")

        click.echo(f"    {Fore.GREEN}✓{Style.RESET_ALL} Found: {new_title[:50]}...")
        click.echo(f"      Authors: {', '.join(new_authors[:3])}")
        click.echo(f"      Year: {new_year}")

        # Update index entry
        entry["title"] = new_title
        entry["doi"] = doi
        if new_authors:
            entry["authors"] = new_authors
        if new_year:
            entry["year"] = new_year

        # Generate new filename
        from literature_manager.naming import generate_filename
        metadata_for_naming = {
            "title": new_title,
            "authors": new_authors,
            "year": new_year
        }
        new_filename = generate_filename(metadata_for_naming)

        # Rename file if needed
        old_name = full_path.name
        if new_filename != old_name:
            new_path = full_path.parent / new_filename

            # Check for collision
            if new_path.exists():
                click.echo(f"    {Fore.YELLOW}⚠{Style.RESET_ALL} File exists: {new_filename[:40]}...")
            else:
                shutil.move(str(full_path), str(new_path))
                # Update filepath in entry
                entry["filepath"] = str(new_path.relative_to(config.workshop_root))
                click.echo(f"    Renamed to: {new_filename[:50]}...")

        index[hash_id] = entry
        repaired_count += 1

        # Rate limit
        time.sleep(0.3)

    # Save index
    save_index(index, config.index_path)

    click.echo(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    print_success(f"Repaired: {repaired_count} papers")
    if not_found_count > 0:
        print_warning(f"Could not repair: {not_found_count}")
    if error_count > 0:
        print_error(f"Errors: {error_count}")
    click.echo(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
