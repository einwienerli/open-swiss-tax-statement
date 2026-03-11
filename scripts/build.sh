#!/usr/bin/env bash
# Build Open Swiss Tax Statement macOS .app bundle.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== Building Open Swiss Tax Statement ==="

# Generate icon if missing
if [ ! -f "assets/icon.icns" ]; then
    echo "--- Generating app icon ---"
    python scripts/generate_icon.py
fi

# Run PyInstaller
echo "--- Running PyInstaller ---"
pyinstaller OpenSwissTaxStatement.spec --noconfirm

APP_PATH="dist/Open Swiss Tax Statement.app"
if [ -d "$APP_PATH" ]; then
    echo "=== Build successful ==="
    echo "App: $APP_PATH"
    du -sh "$APP_PATH"
else
    echo "=== Build FAILED ==="
    exit 1
fi
