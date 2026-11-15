#!/bin/bash
# Crée dist/Grand-Clip-Auto-mac.dmg à partir de dist/Grand Clip Auto.app (glisser l'app dans Applications).
set -euo pipefail
cd "$(dirname "$0")/.."
APP="dist/Grand Clip Auto.app"
OUT="dist/Grand-Clip-Auto-mac.dmg"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$OUT"
hdiutil create -volname "Grand Clip Auto" -srcfolder "$STAGE" -ov -format UDZO "$OUT"
rm -rf "$STAGE"
echo "✅ $OUT"
