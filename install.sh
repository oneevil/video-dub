#!/usr/bin/env bash
# Установщик Video-Dub для Linux.
#
#   curl -LsSf https://raw.githubusercontent.com/OneEvil/video-dub/main/install.sh | bash
#
# Кладёт приложение в ~/.local/share/video-dub, создаёт команду video-dub и ярлык
# в меню приложений. Всё в домашней папке — root нужен только если придётся
# доставить ffmpeg через пакетный менеджер.
set -euo pipefail

REPO="${VIDEO_DUB_REPO:-OneEvil/video-dub}"
REF="${VIDEO_DUB_REF:-main}"
PREFIX="${VIDEO_DUB_PREFIX:-$HOME/.local/share/video-dub}"
BIN_DIR="${VIDEO_DUB_BIN:-$HOME/.local/bin}"
DESKTOP_DIR="$HOME/.local/share/applications"

say()  { printf '\033[36m▸\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Linux" ] || die "этот установщик для Linux; на macOS используйте .dmg"

fetch_source() {
  if [ -f "$(dirname "$0")/app.py" ]; then
    # Запуск из уже распакованной копии — просто копируем её
    say "Устанавливаю из локальной папки..."
    mkdir -p "$PREFIX"
    tar -C "$(cd "$(dirname "$0")" && pwd)" \
        --exclude=.git --exclude='.venv*' --exclude=runtime \
        --exclude=models --exclude=projects -cf - . | tar -C "$PREFIX" -xf -
    return
  fi

  say "Скачиваю Video-Dub ($REF)..."
  command -v curl >/dev/null 2>&1 || die "нужен curl (sudo apt install curl)"
  command -v tar  >/dev/null 2>&1 || die "нужен tar"
  local tmp
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  curl -LsSf "https://github.com/$REPO/archive/refs/heads/$REF.tar.gz" \
    | tar -C "$tmp" -xzf - || die "не удалось скачать исходники"
  local src
  src="$(find "$tmp" -maxdepth 1 -mindepth 1 -type d | head -1)"
  [ -n "$src" ] || die "неожиданная структура архива"
  mkdir -p "$PREFIX"
  # Сохраняем .env и проекты, если это обновление поверх существующей установки
  tar -C "$src" -cf - . | tar -C "$PREFIX" -xf -
}

make_launcher() {
  mkdir -p "$BIN_DIR"
  cat > "$BIN_DIR/video-dub" <<EOF
#!/usr/bin/env bash
exec "$PREFIX/scripts/bootstrap.sh" "\$@"
EOF
  chmod +x "$BIN_DIR/video-dub" "$PREFIX/scripts/bootstrap.sh"
  ok "Команда: video-dub"
}

make_desktop_entry() {
  mkdir -p "$DESKTOP_DIR"
  cat > "$DESKTOP_DIR/video-dub.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Video-Dub
Comment=Автоматический перевод и озвучка видео
Exec=$BIN_DIR/video-dub
Terminal=true
Categories=AudioVideo;Video;
EOF
  command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
  ok "Ярлык добавлен в меню приложений"
}

check_path() {
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
      warn "$BIN_DIR не в PATH. Добавьте в ~/.bashrc или ~/.zshrc:"
      printf '\n    export PATH="%s:$PATH"\n\n' "$BIN_DIR"
      ;;
  esac
}

echo
say "Установка Video-Dub"
fetch_source
make_launcher
make_desktop_entry
check_path
echo
ok "Готово. Запуск: video-dub"
say "При первом запуске подтянутся Python, ffmpeg и зависимости — это займёт несколько минут."
echo
