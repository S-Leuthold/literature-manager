"""Native macOS notifications fired inline from the processing pipeline.

This replaces the old menu-bar app: instead of a separate rumps process polling
a status file and shelling out to terminal-notifier, the notification is fired
directly when a paper is filed. The click action opens the library folder the
paper landed in.

Robustness notes:
- terminal-notifier is resolved to an absolute path because launchd's minimal
  PATH does not include Homebrew's arm64 dir (/opt/homebrew/bin), so a bare
  "terminal-notifier" invocation silently no-ops under launchd.
- If terminal-notifier cannot be found, fall back to osascript (banner only, no
  click-to-open).
- Every failure is swallowed: a notification must never break processing.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import quote

NOTIFICATION_GROUP = "literature-manager"
_SUBPROCESS_TIMEOUT = 5


def _resolve_terminal_notifier() -> Optional[str]:
    """Return an absolute path to terminal-notifier, or None if not installed.

    PATH lookup first (works from a shell), then explicit Homebrew locations so
    it resolves under launchd's minimal PATH too.
    """
    found = shutil.which("terminal-notifier")
    if found:
        return found

    for candidate in (
        "/opt/homebrew/bin/terminal-notifier",  # Homebrew on Apple Silicon
        "/usr/local/bin/terminal-notifier",     # Homebrew on Intel
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    return None


def _format_citation(metadata: dict) -> str:
    """Build an 'Author et al., Year' citation, falling back to the title."""
    authors = metadata.get("authors") or []
    year = metadata.get("year") or ""

    if authors:
        first = authors[0]
        # Author strings come in as "Last, First" or "First Last".
        if "," in first:
            last = first.split(",")[0].strip()
        else:
            parts = first.split()
            last = parts[-1] if parts else first
        suffix = " et al." if len(authors) > 1 else ""
        citation = f"{last}{suffix}, {year}".strip().rstrip(",").strip()
        if citation:
            return citation

    return (metadata.get("title") or "New paper")[:80]


def _build_title(metadata: dict) -> str:
    citation = _format_citation(metadata)
    journal = metadata.get("journal") or ""
    return f"{citation} · {journal}" if journal else citation


def _build_message(metadata: dict) -> str:
    summary = metadata.get("summary") or ""
    if len(summary) > 150:
        summary = summary[:147] + "..."
    topics = metadata.get("topics") or []
    tags = " ".join(f"[{t}]" for t in topics[:3])
    return f"{summary} {tags}".strip() or "Processed successfully"


def _notify_via_terminal_notifier(
    binary: str, title: str, message: str, folder_url: Optional[str]
) -> None:
    cmd = [
        binary,
        "-title", title,
        "-subtitle", " ",  # blank subtitle for visual spacing
        "-message", message,
        "-sound", "default",
        "-group", NOTIFICATION_GROUP,
    ]
    if folder_url:
        cmd += ["-open", folder_url]
    subprocess.run(cmd, capture_output=True, timeout=_SUBPROCESS_TIMEOUT)


def _notify_via_osascript(title: str, message: str) -> None:
    """Dependency-free fallback. Banner only — cannot open a folder on click."""
    script = (
        f"display notification {json.dumps(message)} "
        f"with title {json.dumps(title)} sound name \"default\""
    )
    subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        capture_output=True,
        timeout=_SUBPROCESS_TIMEOUT,
    )


def notify_paper_processed(metadata: dict, dest_folder: Path) -> None:
    """Fire a macOS notification for a newly filed paper.

    The notification opens ``dest_folder`` (the topic folder the paper was filed
    into, or recent/) when clicked. Never raises — any failure is logged-and-
    swallowed so a notification problem can't break the pipeline.

    Args:
        metadata: Paper metadata dict (title, authors, year, journal, summary,
            topics) as assembled by the extraction pipeline.
        dest_folder: The folder the paper was filed into.
    """
    try:
        title = _build_title(metadata)
        message = _build_message(metadata)

        folder_url = None
        try:
            if dest_folder and Path(dest_folder).exists():
                folder_url = f"file://{quote(str(dest_folder))}"
        except OSError:
            folder_url = None

        binary = _resolve_terminal_notifier()
        if binary:
            _notify_via_terminal_notifier(binary, title, message, folder_url)
        else:
            _notify_via_osascript(title, message)
    except Exception as e:  # never let a notification failure escape
        print(f"  Notification failed (non-fatal): {type(e).__name__}: {e}")
