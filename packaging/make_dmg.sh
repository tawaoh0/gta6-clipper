#!/bin/bash
# Crée dist/GTA6-Clipper-mac.dmg à partir de dist/GTA6 Clipper.app (glisser l'app dans Applications).
set -euo pipefail
cd "$(dirname "$0")/.."
APP="dist/GTA6 Clipper.app"
OUT="dist/GTA6-Clipper-mac.dmg"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$OUT"
hdiutil create -volname "GTA6 Clipper" -srcfolder "$STAGE" -ov -format UDZO "$OUT"
rm -rf "$STAGE"
echo "✅ $OUT"
