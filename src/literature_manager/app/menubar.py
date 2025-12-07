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

        # Start timer to poll status (every 2 seconds)
        self._timer = rumps.Timer(self._refresh_status, 2)
        self._timer.start()

        # Initial refresh
        self._refresh_status(None)

    def _build_menu(self):
        """Build the menu structure."""
        self.menu = [
            rumps.MenuItem("📚 0 papers", callback=self._noop),
            rumps.MenuItem("Status: Unknown", callback=self._noop),
            None,  # Separator
            rumps.MenuItem("Open Library", callback=self._open_library),
            rumps.MenuItem("Open Inbox", callback=self._open_inbox),
            None,  # Separator
            rumps.MenuItem("Start Watching", callback=self._toggle_watch),
            rumps.MenuItem("Process Now", callback=self._process_now),
            None,  # Separator
            rumps.MenuItem("View Logs", callback=self._view_logs),
            rumps.MenuItem("Reveal Config", callback=self._reveal_config),
            None,  # Separator
            rumps.MenuItem("Quit", callback=self._quit),
        ]

        # Store references for updates
        self._paper_count_item = self.menu["📚 0 papers"]
        self._status_item = self.menu["Status: Unknown"]
        self._watch_toggle_item = self.menu["Start Watching"]

    def _refresh_status(self, _):
        """Refresh status from status file and index."""
        # Read status file
        status = read_status(self.config)
        state = status.get("state", "paused")
        is_running = is_watch_running(self.config)

        # Update status display
        if is_running and state == "watching":
            self._status_item.title = "Status: Watching"
            self._watch_toggle_item.title = "Pause Watching"
        else:
            self._status_item.title = "Status: Paused"
            self._watch_toggle_item.title = "Start Watching"

        # Update paper count from index
        paper_count = self._get_paper_count()
        self._paper_count_item.title = f"📚 {paper_count} papers"

        # Check for new processed paper (for notifications)
        last_processed = status.get("last_processed")
        if last_processed and last_processed != self._last_known_paper:
            self._last_known_paper = last_processed
            title = last_processed.get("title", "Unknown")
            self._send_notification("Paper Processed", title[:50])

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

    def _noop(self, _):
        """No-op callback to keep menu items enabled but non-functional."""
        pass

    def _send_notification(self, title: str, message: str):
        """Send macOS notification via osascript."""
        script = f'display notification "{message}" with title "{title}"'
        subprocess.run(["osascript", "-e", script], capture_output=True)

    def _open_library(self, _):
        """Open library folder in Finder."""
        subprocess.run(["open", str(self.config.by_topic_path)])

    def _open_inbox(self, _):
        """Open inbox folder in Finder."""
        subprocess.run(["open", str(self.config.inbox_path)])

    def _toggle_watch(self, _):
        """Start or stop the watch process."""
        if is_watch_running(self.config):
            self._stop_watching()
        else:
            self._start_watching()

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
