"""Literature Manager menu bar app v2.0.

Thin UI shell that communicates with CLI via subprocess and status file.
No direct imports from cli.py or operations.py.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import rumps

from literature_manager.app.status import read_status, is_watch_running
from literature_manager.config import Config


class LiteratureManagerApp(rumps.App):
    """Menu bar app for Literature Manager."""

    def __init__(self):
        super().__init__(
            name="Literature Manager",
            title="📜",  # Emoji in menu bar
            quit_button=None,  # We'll add our own
        )

        # Load config
        try:
            self.config = Config()
        except FileNotFoundError:
            rumps.alert("Config not found", "Please create config.yaml")
            rumps.quit_application()
            return

        # Track last known paper for notification dedup
        self._last_known_paper = None

        # Build menu
        self._build_menu()

        # Auto-start watcher (always watching when app is running)
        self._ensure_watcher_running()

        # Start timer to poll status (every 2 seconds)
        self._timer = rumps.Timer(self._refresh_status, 2)
        self._timer.start()

        # Initial refresh
        self._refresh_status(None)

    def _build_menu(self):
        """Build the menu structure."""
        self.menu = [
            rumps.MenuItem("📚 0 papers", callback=self._noop),
            rumps.MenuItem("Status: Starting...", callback=self._noop),
            rumps.MenuItem("Last: None", callback=self._noop),
            None,  # Separator
            rumps.MenuItem("Open Library", callback=self._open_library),
            rumps.MenuItem("Open Inbox", callback=self._open_inbox),
            None,  # Separator
            rumps.MenuItem("Process Now", callback=self._process_now),
            None,  # Separator
            rumps.MenuItem("View Logs", callback=self._view_logs),
            rumps.MenuItem("Reveal Config", callback=self._reveal_config),
            None,  # Separator
            rumps.MenuItem("Quit", callback=self._quit),
        ]

        # Store references for updates
        self._paper_count_item = self.menu["📚 0 papers"]
        self._status_item = self.menu["Status: Starting..."]
        self._last_processed_item = self.menu["Last: None"]

    def _refresh_status(self, _):
        """Refresh status from status file and index."""
        # Ensure watcher is always running
        self._ensure_watcher_running()

        # Update status display
        if is_watch_running(self.config):
            self._status_item.title = "Status: Watching"
        else:
            self._status_item.title = "Status: Starting..."

        # Update paper count from index
        paper_count = self._get_paper_count()
        self._paper_count_item.title = f"📚 {paper_count} papers"

        # Update last processed display (from index)
        paper_info = self._get_last_processed_info()
        if paper_info and paper_info.get("citation"):
            self._last_processed_item.title = f"Last: {paper_info['citation']}"
        else:
            self._last_processed_item.title = "Last: None"

        # Check for new processed paper (for notifications)
        status = read_status(self.config)
        last_processed = status.get("last_processed")
        if last_processed and last_processed != self._last_known_paper:
            self._last_known_paper = last_processed
            # Show native macOS notification
            if paper_info:
                self._show_paper_notification(paper_info)

    def _get_paper_count(self) -> int:
        """Get paper count from index file."""
        try:
            if self.config.index_path.exists():
                with open(self.config.index_path, "r") as f:
                    index = json.load(f)
                return len(index)
        except Exception:
            pass
        return 0

    def _get_last_processed_info(self) -> dict:
        """Get info about most recently processed paper."""
        try:
            if self.config.index_path.exists():
                with open(self.config.index_path, "r") as f:
                    index = json.load(f)
                if not index:
                    return None

                # Find most recently processed
                latest = max(index.values(), key=lambda x: x.get("processed_date", ""))

                # Build citation: "Author et al., Year"
                authors = latest.get("authors", [])
                year = latest.get("year", "")
                citation = None

                if authors:
                    first_author = authors[0].split(",")[0].split()[-1]  # Get last name
                    if len(authors) > 1:
                        citation = f"{first_author} et al."
                    else:
                        citation = first_author
                    if year:
                        citation += f", {year}"

                # Format author list (first 3 + et al.)
                author_list = ""
                if authors:
                    if len(authors) <= 3:
                        author_list = ", ".join(authors)
                    else:
                        author_list = ", ".join(authors[:3]) + ", et al."

                # Get enhanced summary if available
                enhanced = latest.get("enhanced_summary", {})
                summary = enhanced.get("main_finding") or latest.get("summary", "")

                # Format processed time
                processed_date = latest.get("processed_date", "")
                time_str = ""
                if processed_date:
                    from datetime import datetime
                    try:
                        dt = datetime.fromisoformat(processed_date)
                        time_str = dt.strftime("%I:%M %p")
                    except Exception:
                        pass

                return {
                    "citation": citation,
                    "title": latest.get("title", ""),
                    "authors": author_list,
                    "summary": summary,
                    "topics": latest.get("topics", []),
                    "processed_time": time_str,
                    "filepath": latest.get("filepath", ""),
                }
        except Exception:
            pass
        return None

    def _ensure_watcher_running(self):
        """Start watcher if not already running. Called on init and every refresh."""
        if not is_watch_running(self.config):
            self._start_watching()

    def _noop(self, _):
        """No-op callback to keep menu items enabled but non-functional."""
        pass

    def _show_paper_notification(self, paper_info: dict):
        """Show native macOS notification for newly processed paper."""
        citation = paper_info.get("citation", "New Paper")
        summary = paper_info.get("summary", "")
        topics = paper_info.get("topics", [])
        filepath = paper_info.get("filepath", "")

        # Get journal from index if available
        journal = ""
        try:
            if self.config.index_path.exists():
                with open(self.config.index_path, "r") as f:
                    index = json.load(f)
                latest = max(index.values(), key=lambda x: x.get("processed_date", ""))
                journal = latest.get("journal", "")
        except Exception:
            pass

        # Title: Citation · Journal
        title = citation
        if journal:
            title += f" · {journal}"

        # Subtitle: blank for spacing
        subtitle = " "

        # Message: summary + tags inline
        tags_str = " ".join(f"[{t}]" for t in topics[:3]) if topics else ""
        # Compress summary to ~150 chars
        if len(summary) > 150:
            summary = summary[:147] + "..."
        message = f"{summary} {tags_str}".strip() or "Processed successfully"

        # Build terminal-notifier command
        cmd = [
            "terminal-notifier",
            "-title", title,
            "-subtitle", subtitle,
            "-message", message,
            "-sound", "default",
            "-group", "literature-manager",
        ]

        # Add click action to open PDF
        if filepath:
            # filepath is relative to workshop root, not library_path
            full_path = self.config.workshop_root / filepath
            if full_path.exists():
                # URL encode the path for spaces
                from urllib.parse import quote
                encoded_path = quote(str(full_path))
                cmd.extend(["-open", f"file://{encoded_path}"])

        subprocess.run(cmd, capture_output=True)

    def _open_library(self, _):
        """Open library folder in Finder."""
        subprocess.run(["open", str(self.config.by_topic_path)])

    def _open_inbox(self, _):
        """Open inbox folder in Finder."""
        subprocess.run(["open", str(self.config.inbox_path)])

    def _start_watching(self):
        """Start the CLI watch command as subprocess."""
        log_path = self.config.tools_path / "logs" / "watch.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Build clean environment - don't inherit py2app's broken Python paths
        clean_env = {
            "PATH": "/Users/samleuthold/.pyenv/shims:/usr/local/bin:/usr/bin:/bin",
            "HOME": os.environ.get("HOME", ""),
            "USER": os.environ.get("USER", ""),
            "PYTHONPATH": str(self.config.tools_path / "src"),
            "PYENV_ROOT": "/Users/samleuthold/.pyenv",
        }

        # Start watch command in background
        with open(log_path, "a") as log_file:
            subprocess.Popen(
                ["python3", "-m", "literature_manager.cli", "watch"],
                cwd=str(self.config.tools_path),
                env=clean_env,
                stdout=log_file,
                stderr=log_file,
                start_new_session=True,
            )

        self._status_item.title = "Status: Starting..."

    def _stop_watching(self):
        """Stop the watch process."""
        status = read_status(self.config)
        pid = status.get("watch_pid")

        if pid:
            try:
                os.kill(pid, 15)  # SIGTERM
            except ProcessLookupError:
                pass

        self._status_item.title = "Status: Stopping..."

    def _process_now(self, _):
        """Run process command immediately."""
        self._status_item.title = "Status: Processing..."

        # Build clean environment - don't inherit py2app's broken Python paths
        clean_env = {
            "PATH": "/Users/samleuthold/.pyenv/shims:/usr/local/bin:/usr/bin:/bin",
            "HOME": os.environ.get("HOME", ""),
            "USER": os.environ.get("USER", ""),
            "PYTHONPATH": str(self.config.tools_path / "src"),
            "PYENV_ROOT": "/Users/samleuthold/.pyenv",
        }

        # Run process command
        subprocess.Popen(
            ["python3", "-m", "literature_manager.cli", "process"],
            cwd=str(self.config.tools_path),
            env=clean_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _view_logs(self, _):
        """Open watch log in Console."""
        log_path = self.config.tools_path / "logs" / "watch.log"
        if log_path.exists():
            subprocess.run(["open", "-a", "Console", str(log_path)])
        else:
            rumps.alert("No logs", "Watch log not found")

    def _reveal_config(self, _):
        """Reveal config.yaml in Finder."""
        config_path = self.config.tools_path / "config.yaml"
        subprocess.run(["open", "-R", str(config_path)])

    def _quit(self, _):
        """Quit the app, stopping watch if running."""
        if is_watch_running(self.config):
            self._stop_watching()
        rumps.quit_application()


def main():
    """Entry point for menu bar app."""
    app = LiteratureManagerApp()
    app.run()


if __name__ == "__main__":
    main()
