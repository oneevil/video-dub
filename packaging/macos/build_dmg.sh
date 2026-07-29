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
# Бандл — неизменяемый шаблон: приложение создаёт venv'ы, качает модели и пишет
# runtime/ рядом с кодом, а делать это внутри .app нельзя. Запись сломала бы
# подпись (macOS объявит приложение повреждённым), да и /Applications требует
# админских прав. Поэтому копируем код в Application Support и работаем оттуда.
#
# Terminal открываем потому, что первый запуск идёт минутами (Python, ffmpeg,
# torch) — иначе пользователь смотрит на прыгающую иконку и не знает, живо ли всё.
cat > "$APP/Contents/MacOS/video-dub" <<'LAUNCHER'
#!/bin/bash
set -e
BUNDLE_APP="$(cd "$(dirname "$0")/../Resources/app" && pwd)"
DATA_DIR="$HOME/Library/Application Support/Video-Dub"

mkdir -p "$DATA_DIR"
# Обновляем код, сохраняя пользовательские данные: настройки, голоса, модели,
# проекты и уже собранные окружения переживают установку новой версии.
rsync -a --delete \
  --exclude '.env' --exclude 'voices/' --exclude 'models/' --exclude 'projects/' \
  --exclude 'runtime/' --exclude '.venv' --exclude '.venv-*' --exclude '.latentsync/' \
  "$BUNDLE_APP/" "$DATA_DIR/"

# Голоса из комплекта кладём только при первой установке: дальше это уже
# каталог пользователя, и перезаписывать его обновлением нельзя
[ -d "$DATA_DIR/voices" ] || cp -R "$BUNDLE_APP/voices" "$DATA_DIR/voices"

open -a Terminal "$DATA_DIR/scripts/bootstrap.sh"
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
# С сертификатом Developer ID приложение проходит Gatekeeper без предупреждений.
# Без него откатываемся на ad-hoc — запускается локально, но скачанную копию
# система заблокирует (см. README про снятие карантина).
SIGN_ID="${MACOS_SIGN_IDENTITY:-$(security find-identity -v -p codesigning 2>/dev/null \
  | grep -m1 'Developer ID Application' | sed 's/.*"\(.*\)"/\1/')}"

if [ -n "$SIGN_ID" ]; then
  echo "▸ Подписываю: $SIGN_ID"
  # --options runtime обязателен для нотаризации; --timestamp тоже,
  # иначе подпись «протухнет» вместе с сертификатом
  codesign --force --deep --options runtime --timestamp \
           --sign "$SIGN_ID" "$APP"
  codesign --verify --strict --verbose=2 "$APP"
else
  echo "! Developer ID не найден — подписываю ad-hoc"
  codesign --force --deep --sign - "$APP" 2>/dev/null || echo "! codesign пропущен"
fi

# ── dmg ──────────────────────────────────────────────────────────────────────
STAGE="$DIST/stage"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"      # привычный drag-and-drop

hdiutil create -volname "Video-Dub" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"

# ── нотаризация ──────────────────────────────────────────────────────────────
# Подписи мало: Gatekeeper на скачанном файле требует ещё и билет от Apple.
# Учётные данные берём из keychain-профиля или из переменных окружения (CI).
# Разовая настройка профиля:
#   xcrun notarytool store-credentials video-dub \
#     --apple-id <e-mail> --team-id 76KE738FKP --password <app-specific-password>
if [ -n "$SIGN_ID" ]; then
  codesign --force --timestamp --sign "$SIGN_ID" "$DMG"

  if [ -n "${NOTARY_PROFILE:-}" ]; then
    NOTARY_ARGS=(--keychain-profile "$NOTARY_PROFILE")
  elif [ -n "${NOTARY_APPLE_ID:-}" ] && [ -n "${NOTARY_PASSWORD:-}" ] && [ -n "${NOTARY_TEAM_ID:-}" ]; then
    NOTARY_ARGS=(--apple-id "$NOTARY_APPLE_ID" --password "$NOTARY_PASSWORD" --team-id "$NOTARY_TEAM_ID")
  else
    NOTARY_ARGS=()
  fi

  if [ ${#NOTARY_ARGS[@]} -gt 0 ]; then
    echo "▸ Отправляю на нотаризацию (несколько минут)..."
    xcrun notarytool submit "$DMG" "${NOTARY_ARGS[@]}" --wait
    # Степлер вшивает билет в образ, чтобы он проверялся без интернета
    xcrun stapler staple "$DMG"
    xcrun stapler validate "$DMG"
    echo "✓ Нотаризовано"
  else
    echo "! Учётные данные нотаризации не заданы — образ подписан, но не нотаризован"
    echo "  Настройка: xcrun notarytool store-credentials video-dub --apple-id … --team-id … --password …"
  fi
fi

echo "✓ $DMG"
