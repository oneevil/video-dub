#!/usr/bin/env bash
# Собирает Video-Dub.app и упаковывает его в .dmg.
#
# .app здесь — не скомпилированный бинарник, а обёртка: внутри лежат исходники
# и bootstrap.sh, который при первом запуске поднимает Python, ffmpeg и venv'ы.
# Иначе никак: приложение на ходу создаёт изолированные окружения через
# `sys.executable -m venv`, а внутри замороженной сборки это не работает.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${1:-$(grep -m1 '^version' "$ROOT/pyproject.toml" | cut -d'"' -f2)}"
DIST="$ROOT/dist"
APP="$DIST/Video-Dub.app"
DMG="$DIST/Video-Dub-$VERSION.dmg"

rm -rf "$DIST"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# ── исходники ────────────────────────────────────────────────────────────────
# Берём рабочее дерево, а не HEAD: список файлов даёт сам git с учётом
# .gitignore, поэтому venv'ы, модели и проекты не попадают, а ещё не
# закоммиченные правки при локальной сборке — попадают. В CI, где checkout
# чистый, результат идентичен `git archive HEAD`.
PAYLOAD="$APP/Contents/Resources/app"
mkdir -p "$PAYLOAD"
git -C "$ROOT" ls-files --cached --others --exclude-standard -z \
  | tar -C "$ROOT" --null -T - -cf - \
  | tar -C "$PAYLOAD" -xf -

[ -f "$PAYLOAD/scripts/bootstrap.sh" ] || { echo "✗ в сборку не попал scripts/bootstrap.sh" >&2; exit 1; }

# ── Info.plist ───────────────────────────────────────────────────────────────
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>              <string>Video-Dub</string>
  <key>CFBundleDisplayName</key>       <string>Video-Dub</string>
  <key>CFBundleIdentifier</key>        <string>com.oneevil.video-dub</string>
  <key>CFBundleVersion</key>           <string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundlePackageType</key>       <string>APPL</string>
  <key>CFBundleExecutable</key>        <string>video-dub</string>
  <key>CFBundleIconFile</key>          <string>AppIcon</string>
  <key>LSMinimumSystemVersion</key>    <string>12.0</string>
  <key>NSHighResolutionCapable</key>   <true/>
</dict>
</plist>
PLIST

# ── исполняемый файл ─────────────────────────────────────────────────────────
# Первый запуск долгий (качается Python, ffmpeg, torch), поэтому открываем
# Terminal — иначе пользователь несколько минут смотрит на прыгающую иконку и
# не понимает, живо ли приложение.
cat > "$APP/Contents/MacOS/video-dub" <<'LAUNCHER'
#!/bin/bash
APP_ROOT="$(cd "$(dirname "$0")/../Resources/app" && pwd)"
open -a Terminal "$APP_ROOT/scripts/bootstrap.sh"
LAUNCHER
chmod +x "$APP/Contents/MacOS/video-dub" "$PAYLOAD/scripts/bootstrap.sh"

# ── иконка ───────────────────────────────────────────────────────────────────
ICON_SRC="$ROOT/packaging/macos/icon.png"
if [ -f "$ICON_SRC" ]; then
  ICONSET="$DIST/AppIcon.iconset"
  mkdir -p "$ICONSET"
  for sz in 16 32 64 128 256 512; do
    sips -z $sz $sz "$ICON_SRC" --out "$ICONSET/icon_${sz}x${sz}.png" >/dev/null
    sips -z $((sz*2)) $((sz*2)) "$ICON_SRC" --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
  rm -rf "$ICONSET"
else
  echo "! icon.png не найден — собираю без иконки"
fi

# ── подпись ──────────────────────────────────────────────────────────────────
# Без сертификата разработчика ad-hoc подписи достаточно, чтобы приложение
# запускалось локально. Gatekeeper всё равно потребует «Открыть всё равно»
# при первом запуске скачанного .dmg — это ожидаемо и описано в README.
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "! codesign пропущен"

# ── dmg ──────────────────────────────────────────────────────────────────────
STAGE="$DIST/stage"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"      # привычный drag-and-drop

hdiutil create -volname "Video-Dub" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"

echo "✓ $DMG"
