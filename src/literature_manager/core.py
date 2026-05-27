"""Core single-PDF processing pipeline.

This is the base layer shared by the `process` (one-shot) and `watch`
(continuous) commands. `process_pdf` takes one PDF from the inbox all the way
through extraction, classification, filing, indexing, optional Zotero sync, and
(optionally) a native notification.

Crash contract: `process_pdf` never raises. The entire body is wrapped so that
one bad PDF, a flaky API, or a malformed taxonomy entry returns False and is
logged — it can never take down a long-lived watcher.
"""

import os
from pathlib import Path

import click
from colorama import Fore, Style

from literature_manager.extractors import extract_metadata
from literature_manager.extractors.exceptions import (
    CorruptedPDFError,
    NetworkError,
    LLMError,
    ConfigurationError,
)
from literature_manager.naming import generate_filename
from literature_manager.notifications import notify_paper_processed
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


def process_pdf(
    pdf_path: Path, config, *, dry_run: bool = False, verbose: bool = True, notify: bool = False
) -> bool:
    """
    Process a single PDF file end to end.

    Returns True if the paper was successfully filed, False otherwise. Never
    raises — every failure path returns False so a watcher loop is never killed.

    Args:
        pdf_path: PDF to process.
        config: Config object.
        dry_run: If True, report actions without moving files or writing state.
        verbose: Verbose stdout.
        notify: If True (and not dry_run), fire a macOS notification on success.
    """
    from literature_manager.extractors.text_parser import is_pdf_readable

    try:
        if verbose:
            print_info(f"Processing: {pdf_path.name}")

        ## ---------------------------------------------------------------------
        ## Check PDF readability before expensive operations
        ## ---------------------------------------------------------------------
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

        # Quick duplicate check BEFORE expensive metadata extraction
        # Try to get DOI quickly from PDF metadata first
        from literature_manager.extractors.doi import extract_doi_from_pdf
        quick_doi = extract_doi_from_pdf(pdf_path)

        if quick_doi:
            # Check if this DOI already exists
            from literature_manager.operations import check_duplicate_by_doi
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
                # generate_fulltext_summary returns the summary dict directly
                # ({main_finding, key_approach, key_results, implication}).
                if summary_result:
                    update_index_fulltext_summary(metadata.get("doi"), summary_result, config)
                    if verbose:
                        click.echo("  Fulltext summary generated")

                    # Push summary to Zotero as note
                    if zot_sync and metadata.get("doi"):
                        try:
                            zot_sync.add_or_update_fulltext_note(
                                doi=metadata["doi"],
                                fulltext_summary=summary_result,
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

        # Fire a native notification (opens the destination folder on click).
        # notify_paper_processed is itself fully guarded; this is belt-and-braces.
        if notify and not dry_run:
            notify_paper_processed(metadata, final_path.parent)

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
        # Catch-all: nothing escapes process_pdf, so a watcher is never killed.
        print_error(f"  Unexpected error: {type(e).__name__}: {e}")
        if verbose:
            import traceback
            traceback.print_exc()
        return False
