"""Inbox watcher.

Runs a long-lived process that watches the inbox and files PDFs as they land,
firing a notification per paper. Supervised by launchd.

Design choices (v2):
- Uses watchdog's PollingObserver, not the FSEvents Observer. The FSEvents
  observer exits 78 silently when spawned directly by launchd; polling a single
  small folder every few seconds is cheap and works in every context.
- The event handler is fully guarded: an exception while handling one file can
  never kill the observer thread.
- SIGTERM (sent by launchd on stop) is handled cleanly — observer stopped, PID
  lock released, exit 0 (so KeepAlive does not restart an intentional stop).
- An unexpected error in startup or the run loop logs to disk and exits nonzero,
  so launchd's KeepAlive restarts it (throttled). Disk logging is configured up
  front so startup failures are never invisible the way the exit-78 was.
"""

import fcntl
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import click
from colorama import Fore, Style

from literature_manager.core import process_pdf, print_error, print_info

# Poll the inbox every few seconds. Fine for a single small folder.
_POLL_INTERVAL_SECONDS = 2.0


def run_watch(config, verbose: bool = True) -> None:
    """Watch the inbox and process PDFs as they arrive. Long-lived; runs until
    SIGTERM / KeyboardInterrupt."""
    config.ensure_directories()

    log_dir = config.tools_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Log to stderr so output flows through launchd's StandardErrorPath. The
    # plist points that at ~/Library/Logs (NOT the Desktop tree): launchd opens
    # its redirect files in its own context, which on current macOS is denied
    # access to ~/Desktop — that denial, not FSEvents, was the silent exit-78
    # spawn failure. The watcher process can still write the library under
    # ~/Desktop; only launchd's own log-file open must live elsewhere.
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    logging.info("watcher starting (pid=%s)", os.getpid())

    # Single-instance lock.
    pid_file_path = log_dir / "watch.pid"
    try:
        pid_file = open(pid_file_path, "w")
        fcntl.flock(pid_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        pid_file.write(str(os.getpid()))
        pid_file.flush()
    except (IOError, OSError):
        print_error("Another instance of literature-manager watch is already running")
        print_info("If you're sure no other instance is running, delete:")
        print_info(f"  {pid_file_path}")
        sys.exit(1)

    from watchdog.events import FileSystemEventHandler
    from watchdog.observers.polling import PollingObserver

    processed_files = set()

    class PDFHandler(FileSystemEventHandler):
        def on_created(self, event):
            # Nothing in here may escape: a handler exception would otherwise
            # kill the observer thread and silently stop watching.
            try:
                if event.is_directory:
                    return

                path = Path(event.src_path)
                if path.suffix.lower() != ".pdf":
                    return

                if path.name in processed_files:
                    return

                # Wait until the file is fully written (size stabilization).
                time.sleep(1)  # initial settle
                max_wait = 30
                stable_count = 0
                last_size = -1

                for _ in range(max_wait):
                    if not path.exists():
                        return  # file moved/deleted while settling
                    try:
                        current_size = path.stat().st_size
                        if current_size == last_size and current_size > 0:
                            stable_count += 1
                            if stable_count >= 2:  # stable for ~2s
                                break
                        else:
                            stable_count = 0
                        last_size = current_size
                        time.sleep(1)
                    except OSError:
                        return  # error accessing file

                if not path.exists() or path.stat().st_size == 0:
                    return

                click.echo(f"\n{Fore.YELLOW}New PDF detected: {path.name}{Style.RESET_ALL}")
                processed_files.add(path.name)
                process_pdf(path, config, notify=True, verbose=verbose)
            except Exception as e:
                logging.exception("on_created handler failed for %s", getattr(event, "src_path", "?"))
                print_error(f"Handler error (continuing to watch): {type(e).__name__}: {e}")

    observer = PollingObserver(timeout=_POLL_INTERVAL_SECONDS)
    started = False

    def _shutdown(signum, _frame):
        logging.info("received signal %s, shutting down", signum)
        try:
            if started:
                observer.stop()
        except Exception:
            pass
        try:
            fcntl.flock(pid_file.fileno(), fcntl.LOCK_UN)
            pid_file.close()
        except Exception:
            pass
        try:
            pid_file_path.unlink()
        except OSError:
            pass
        print_info("\nReceived stop signal, stopped watching")
        sys.exit(0)  # clean stop — KeepAlive won't restart

    signal.signal(signal.SIGTERM, _shutdown)

    try:
        print_info(f"Watching inbox: {config.inbox_path}")
        print_info("Press Ctrl+C to stop\n")
        if verbose:
            print_info("✓ Watch mode started (polling observer)\n")

        # Process any PDFs already sitting in the inbox before watching.
        existing_pdfs = list(config.inbox_path.glob("*.pdf"))
        if existing_pdfs:
            print_info(f"Found {len(existing_pdfs)} existing PDFs in inbox, processing...\n")
            for pdf_path in existing_pdfs:
                if pdf_path.name not in processed_files:
                    click.echo(f"\n{Fore.YELLOW}Processing existing PDF: {pdf_path.name}{Style.RESET_ALL}")
                    processed_files.add(pdf_path.name)
                    process_pdf(pdf_path, config, notify=True, verbose=verbose)
            print_info("\n✓ Finished processing existing files, now watching for new ones...\n")

        observer.schedule(PDFHandler(), str(config.inbox_path), recursive=False)
        observer.start()
        started = True

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        if started:
            observer.stop()
        print_info("\nStopped watching")
    except Exception as e:
        # Unexpected startup/loop failure: log and exit nonzero so launchd's
        # KeepAlive restarts us (throttled). The error is now on disk.
        logging.exception("watcher failed")
        print_error(f"Watcher error: {type(e).__name__}: {e}")
        sys.exit(1)
    finally:
        try:
            if started:
                observer.join(timeout=5)
        except Exception:
            pass
