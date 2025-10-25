#!/bin/bash
# Install literature-manager as a background service

set -e

echo "======================================================================"
echo "INSTALLING LITERATURE-MANAGER BACKGROUND SERVICE"
echo "======================================================================"
echo ""

# Paths
PLIST_SOURCE="$(pwd)/com.samleuthold.literature-manager.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.samleuthold.literature-manager.plist"
LOG_DIR="$(pwd)/logs"

# Create logs directory
mkdir -p "$LOG_DIR"
echo "✓ Created logs directory: $LOG_DIR"

# Find literature-manager command
LITMAN_PATH=$(which literature-manager 2>/dev/null || echo "")

if [ -z "$LITMAN_PATH" ]; then
    echo "⚠️  'literature-manager' not found in PATH"
    echo "   Checking common locations..."

    # Check if it's a Python package
    if command -v python3 &> /dev/null; then
        PYTHON_BIN=$(which python3)
        LITMAN_CANDIDATE="$(dirname $PYTHON_BIN)/literature-manager"

        if [ -f "$LITMAN_CANDIDATE" ]; then
            LITMAN_PATH="$LITMAN_CANDIDATE"
            echo "✓ Found at: $LITMAN_PATH"
        fi
    fi

    if [ -z "$LITMAN_PATH" ]; then
        echo "❌ Cannot find literature-manager executable"
        echo "   Please install it or provide the path"
        exit 1
    fi
fi

echo "✓ Using literature-manager at: $LITMAN_PATH"

# Update plist with correct path
sed "s|/usr/local/bin/literature-manager|$LITMAN_PATH|g" "$PLIST_SOURCE" > "$PLIST_SOURCE.tmp"
mv "$PLIST_SOURCE.tmp" "$PLIST_SOURCE"

# Copy plist to LaunchAgents
echo "✓ Installing service definition..."
cp "$PLIST_SOURCE" "$PLIST_DEST"

# Unload if already loaded
launchctl unload "$PLIST_DEST" 2>/dev/null || true

# Load the service
echo "✓ Loading service..."
launchctl load "$PLIST_DEST"

# Give it a second to start
sleep 2

# Check if it's running
if launchctl list | grep -q "com.samleuthold.literature-manager"; then
    echo ""
    echo "======================================================================"
    echo "✅ INSTALLATION COMPLETE - SERVICE RUNNING"
    echo "======================================================================"
    echo ""
    echo "The literature-manager watch service is now running in the background!"
    echo ""
    echo "WHAT THIS MEANS:"
    echo "  • Automatically starts when you log in"
    echo "  • Watches workspace/inbox/ for new PDFs"
    echo "  • Processes them automatically (extracts metadata, assigns topics)"
    echo "  • Organizes to by-topic/ folders"
    echo "  • Uploads to Zotero automatically"
    echo "  • Runs with low priority (won't slow down your system)"
    echo ""
    echo "USEFUL COMMANDS:"
    echo "  Check status:    launchctl list | grep literature-manager"
    echo "  View live logs:  tail -f $LOG_DIR/watch.log"
    echo "  View errors:     tail -f $LOG_DIR/watch.error.log"
    echo "  Stop service:    launchctl unload $PLIST_DEST"
    echo "  Start service:   launchctl load $PLIST_DEST"
    echo "  Restart:         launchctl kickstart -k gui/\$(id -u)/com.samleuthold.literature-manager"
    echo ""
    echo "TO USE:"
    echo "  Just download PDFs to: workspace/inbox/"
    echo "  The system handles the rest automatically!"
    echo ""
    echo "======================================================================"
else
    echo ""
    echo "⚠️  Service installed but not running"
    echo "   Check logs: $LOG_DIR/watch.error.log"
fi
