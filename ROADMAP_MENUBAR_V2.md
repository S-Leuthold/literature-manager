# Literature Manager Menu Bar App v2.0 Roadmap

## Overview

Complete rebuild of the menu bar app using a clean, decoupled architecture. The new design separates the UI shell from processing logic using subprocess-based communication and file-based status exchange.

### Architectural Principles

1. **Subprocess isolation**: CLI commands run in separate processes, avoiding import conflicts and threading issues
2. **File-based communication**: Status updates via `.literature-status.json`, read by menu bar
3. **Index watching**: Menu bar monitors `.literature-index.json` for library changes, not the inbox directly
4. **Thin UI shell**: `menubar.py` under 150 lines, single responsibility (display + subprocess management)

### What Changes

**Files to DELETE:**
- `src/literature_manager/app/watcher.py` - tight coupling via imports
- `src/literature_manager/app/notifications.py` - over-engineered

**Files to REWRITE:**
- `src/literature_manager/app/menubar.py` - rewrite from scratch (~150 lines)

**Files to CREATE:**
- `src/literature_manager/app/status.py` - status file utilities (~50 lines)
- `src/literature_manager/app/icons/icon.png` - 18x18 menu bar icon
- `src/literature_manager/app/icons/icon@2x.png` - 36x36 Retina icon

**Files to MODIFY:**
- `src/literature_manager/cli.py` - add status file writing to watch command
- `src/literature_manager/config.py` - add status_path property

---

## Milestone 1: Status File Infrastructure

**Complexity**: Simple | **Time**: 30 min | **Dependencies**: None

### Goal
Create the communication protocol between CLI and menu bar via a status file.

### Tasks

**M1.1: Create status.py**
```
src/literature_manager/app/status.py
```
- `read_status(config) -> dict` - Read status file, return defaults if missing
- `write_status(config, **updates)` - Atomic write with timestamp
- `get_status_path(config) -> Path` - Returns `.literature-status.json` path

Status file schema:
```json
{
    "state": "watching" | "paused" | "processing",
    "watch_pid": 12345 | null,
    "last_processed": {
        "title": "Paper title...",
        "timestamp": "2025-01-15T10:30:00"
    } | null,
    "processing_queue": 3,
    "last_error": "Error message" | null,
    "updated_at": "2025-01-15T10:30:00"
}
```

**M1.2: Add status writing to CLI watch command**
Modify `cli.py` watch command:
- Write status on start: `{"state": "watching", "watch_pid": os.getpid()}`
- Update `last_processed` after each successful processing
- Write status on exit: `{"state": "paused", "watch_pid": null}`

**M1.3: Add status_path to config**
Add property to `config.py`:
```python
self.status_path = self.tools_path / ".literature-status.json"
```

### Success Criteria
- [ ] Running `literature-manager watch` creates `.literature-status.json`
- [ ] Processing a PDF updates `last_processed` in status file
- [ ] Ctrl+C gracefully updates status to `paused`

---

## Milestone 2: Minimal Menu Bar Shell

**Complexity**: Moderate | **Time**: 1 hour | **Dependencies**: M1

### Goal
Create a minimal working menu bar app that displays status.

### Tasks

**M2.1: Create menu bar icon**
Create `src/literature_manager/app/icons/`:
- `icon.png` - 18x18 pixels, simple book/document silhouette
- `icon@2x.png` - 36x36 pixels for Retina

**M2.2: Rewrite menubar.py from scratch**
New structure (~100 lines):
- NO imports from `cli.py` or `operations.py`
- Only imports: `rumps`, `json`, `subprocess`, `status.py`, `config.py`
- Timer polls status file every 2 seconds
- Static menu structure with stored references

**M2.3: Implement basic callbacks**
- `open_library()` - subprocess open folder
- `open_inbox()` - subprocess open folder
- `view_logs()` - open watch.log
- `reveal_config()` - reveal config.yaml in Finder
- `quit_app()` - clean exit

### Success Criteria
- [ ] Menu bar shows icon
- [ ] Library count updates when index changes
- [ ] Status reflects status file contents
- [ ] Open Library/Inbox work

---

## Milestone 3: Subprocess Watch Management

**Complexity**: Moderate | **Time**: 1 hour | **Dependencies**: M1, M2

### Goal
Menu bar can start/stop the CLI watch subprocess.

### Tasks

**M3.1: Implement subprocess spawning**
- `_auto_start_watch()` - Start on app launch
- `_is_process_running(pid)` - Check if PID alive

**M3.2: Implement toggle_watching**
- `start_watching()` - Spawn `literature-manager watch` subprocess
- `stop_watching()` - Kill subprocess by PID

**M3.3: Clean shutdown**
- Stop subprocess before quitting app

### Success Criteria
- [ ] App auto-starts watch on launch
- [ ] "Pause Watching" terminates subprocess
- [ ] "Start Watching" spawns new subprocess
- [ ] Quit terminates subprocess before exit

---

## Milestone 4: Notifications

**Complexity**: Simple | **Time**: 30 min | **Dependencies**: M3

### Goal
Display macOS notifications when papers are processed.

### Tasks

**M4.1: Add notification function**
- `_send_notification(title, message)` - via osascript

**M4.2: Detect and notify on new papers**
- Track `last_known_paper` to avoid duplicates
- Single paper: Individual notification with title
- Multiple papers (batch): "Processed X papers"

### Success Criteria
- [ ] Single paper shows notification with title
- [ ] Batch processing shows count notification
- [ ] No duplicate notifications

---

## Milestone 5: Process Now Command

**Complexity**: Simple | **Time**: 20 min | **Dependencies**: M3

### Goal
Manual "Process Now" triggers subprocess.

### Tasks

**M5.1: Implement process_now**
- Run `literature-manager process` via subprocess
- Show "Processing..." during execution
- Refresh status after completion

### Success Criteria
- [ ] Button triggers processing
- [ ] Button shows feedback during processing
- [ ] Status updates after completion

---

## Milestone 6: py2app Bundle

**Complexity**: Moderate | **Time**: 1 hour | **Dependencies**: M1-M5

### Goal
Create proper .app bundle.

### Tasks

**M6.1: Update setup_app.py**
- Include icon files in DATA_FILES
- Exclude heavy dependencies (we use subprocess)
- Update version to 2.0.0

**M6.2: Build and test**
```bash
rm -rf build dist
python setup_app.py py2app
open dist/Literature\ Manager.app
```

### Success Criteria
- [ ] Build completes without errors
- [ ] App shows icon in menu bar
- [ ] All menu items work
- [ ] Subprocess spawning works from bundle

---

## Milestone 7: Polish and Error Handling

**Complexity**: Simple | **Time**: 30 min | **Dependencies**: M1-M6

### Tasks

- Handle missing config gracefully
- Handle subprocess failures with notification
- Show error status from status file
- Add About menu item

### Success Criteria
- [ ] Missing config shows helpful error
- [ ] Failed subprocess shows notification
- [ ] No unhandled exceptions crash app

---

## Summary

| Milestone | Description | Time | Dependencies |
|-----------|-------------|------|--------------|
| M1 | Status File Infrastructure | 30 min | None |
| M2 | Minimal Menu Bar Shell | 1 hour | M1 |
| M3 | Subprocess Watch Management | 1 hour | M1, M2 |
| M4 | Notifications | 30 min | M3 |
| M5 | Process Now Command | 20 min | M3 |
| M6 | py2app Bundle | 1 hour | M1-M5 |
| M7 | Polish and Error Handling | 30 min | M1-M6 |

**Total**: ~5 hours

### Incremental Value

- **After M1**: CLI watch writes status
- **After M2**: Menu bar shows status, opens folders
- **After M3**: Can start/stop watching from menu
- **After M4**: Get notified when papers process
- **After M5**: Can manually trigger processing
- **After M6**: Proper macOS app bundle
- **After M7**: Production-ready
