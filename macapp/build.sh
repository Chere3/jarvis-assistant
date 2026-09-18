#!/bin/zsh
# Compila Jarvis.app (app nativa flotante) con SwiftPM y la deja en macapp/dist/Jarvis.app
set -e
cd "$(dirname "$0")/JarvisApp"
swift build -c release 2>&1 | tail -3
APP="../dist/Jarvis.app"
rm -rf "$APP"; mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp .build/release/JarvisApp "$APP/Contents/MacOS/Jarvis"
cp ../Info.plist "$APP/Contents/Info.plist"
[ -f ../AppIcon.icns ] && cp ../AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
codesign --force --deep --sign - "$APP" 2>/dev/null || true
echo "listo: $(cd ..; pwd)/dist/Jarvis.app"
