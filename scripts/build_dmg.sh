#!/usr/bin/env bash
# Create a polished DMG from built .app bundle.
# Uses hdiutil + AppleScript — no external dependencies.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

APP_NAME="Open Swiss Tax Statement"
APP_PATH="dist/$APP_NAME.app"
VERSION=$(python -c "from src import __version__; print(__version__)")
DMG_NAME="OpenSwissTaxStatement-${VERSION}.dmg"
DMG_PATH="dist/$DMG_NAME"
BG_IMAGE="assets/dmg_background.png"
BG_IMAGE_2X="assets/dmg_background@2x.png"
VOL_NAME="$APP_NAME"
MOUNT_POINT="/Volumes/$VOL_NAME"

if [ ! -d "$APP_PATH" ]; then
    echo "Error: $APP_PATH not found. Run pyinstaller first."
    exit 1
fi

# Generate background images if missing
if [ ! -f "$BG_IMAGE" ]; then
    echo "Generating DMG background..."
    python scripts/generate_dmg_background.py
fi

echo "=== Creating DMG: $DMG_NAME ==="

# Clean previous artifacts
rm -f "$DMG_PATH"
rm -f dist/tmp.dmg

# Detach if already mounted
hdiutil detach "$MOUNT_POINT" 2>/dev/null || true

# Calculate size needed (app size + 20MB buffer)
APP_SIZE_KB=$(du -sk "$APP_PATH" | cut -f1)
DMG_SIZE_KB=$(( APP_SIZE_KB + 20480 ))

# 1. Create temporary R/W DMG
hdiutil create -size "${DMG_SIZE_KB}k" -fs HFS+ -volname "$VOL_NAME" \
    -type UDIF -layout NONE -o dist/tmp.dmg

# 2. Mount it
hdiutil attach dist/tmp.dmg -readwrite -noverify -mountpoint "$MOUNT_POINT"
echo "Mounted at: $MOUNT_POINT"

# 3. Copy app + Applications symlink
cp -R "$APP_PATH" "$MOUNT_POINT/"
ln -s /Applications "$MOUNT_POINT/Applications"

# 4. Copy background image into hidden .background folder
mkdir "$MOUNT_POINT/.background"
cp "$BG_IMAGE" "$MOUNT_POINT/.background/background.png"
if [ -f "$BG_IMAGE_2X" ]; then
    cp "$BG_IMAGE_2X" "$MOUNT_POINT/.background/background@2x.png"
fi

# 5. Configure Finder window via AppleScript
echo "Configuring Finder window..."
osascript <<EOF
tell application "Finder"
    tell disk "$VOL_NAME"
        open
        delay 1
        set current view of container window to icon view
        set toolbar visible of container window to false
        set statusbar visible of container window to false
        set sidebar width of container window to 0
        set the bounds of container window to {200, 120, 740, 410}

        set viewOptions to the icon view options of container window
        set arrangement of viewOptions to not arranged
        set icon size of viewOptions to 80
        set background picture of viewOptions to file ".background:background.png"

        set position of item "$APP_NAME.app" of container window to {135, 130}
        set position of item "Applications" of container window to {405, 130}

        update without registering applications
        delay 1
        close
    end tell
end tell
EOF

# 6. Ensure .DS_Store is written
sync
sleep 1

# 7. Unmount
hdiutil detach "$MOUNT_POINT"

# 8. Convert to compressed read-only DMG
hdiutil convert dist/tmp.dmg -format UDZO -o "$DMG_PATH"
rm -f dist/tmp.dmg

echo "=== DMG created ==="
echo "DMG: $DMG_PATH"
du -sh "$DMG_PATH"
