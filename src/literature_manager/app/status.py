"""Status file utilities for menu bar communication.

File-based IPC between CLI watch command and menu bar app.
Status file is written by CLI, read by menu bar.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


def get_status_path(config) -> Path:
    """Get path to .literature-status.json."""
    return config.tools_path / ".literature-status.json"


def read_status(config) -> Dict[str, Any]:
    """
    Read status file, returning defaults if missing.

    Returns:
        Dict with keys: state, watch_pid, last_processed, processing_queue,
        last_error, updated_at
    """
    status_path = get_status_path(config)

    defaults = {
        "state": "paused",
        "watch_pid": None,
        "last_processed": None,
        "processing_queue": 0,
        "last_error": None,
        "updated_at": None,
    }

    if not status_path.exists():
        return defaults

    try:
        with open(status_path, "r") as f:
            data = json.load(f)
        # Merge with defaults
        result = defaults.copy()
        result.update(data)
        return result
    except (json.JSONDecodeError, IOError):
        return defaults


def write_status(config, **updates) -> None:
    """
    Atomically update status file.

    Args:
        config: Config object
        **updates: Fields to update (state, watch_pid, last_processed, etc.)
    """
    status_path = get_status_path(config)
    temp_path = status_path.with_suffix(".tmp")

    # Read current, apply updates
    current = read_status(config)
    current.update(updates)
    current["updated_at"] = datetime.now().isoformat()

    # Atomic write
    try:
        with open(temp_path, "w") as f:
            json.dump(current, f, indent=2)
        temp_path.replace(status_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def is_watch_running(config) -> bool:
    """Check if watch process is actually running."""
    status = read_status(config)
    pid = status.get("watch_pid")

    if pid is None:
        return False

    try:
        os.kill(pid, 0)  # Signal 0 = check existence
        return True
    except (OSError, ProcessLookupError):
        return False
