#!/bin/bash
# Build ChromeRecovery.app bundle
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$SCRIPT_DIR/src"
BUILD="$SCRIPT_DIR/build"
APP="$BUILD/ChromeRecovery.app"

echo "Building ChromeRecovery.app..."

# Clean previous build
rm -rf "$BUILD"

# Create .app structure
mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources"

# Copy Info.plist
cp "$SRC/Info.plist" "$APP/Contents/"

# Copy launcher and make executable
cp "$SRC/MacOS/ChromeRecovery" "$APP/Contents/MacOS/"
chmod +x "$APP/Contents/MacOS/ChromeRecovery"

# Copy main script
cp "$SRC/Resources/recover.py" "$APP/Contents/Resources/"

# Copy vendored library
cp -r "$SRC/Resources/ccl_chromium_reader" "$APP/Contents/Resources/"

echo "Built: $APP"
echo ""
echo "To create a distributable zip:"
echo "  cd $BUILD && zip -r ChromeRecovery.zip ChromeRecovery.app"
