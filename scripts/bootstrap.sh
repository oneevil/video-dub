#!/usr/bin/env bash
# Единая точка запуска для macOS и Linux.
#
# Задача — довести машину до рабочего состояния без единого ручного шага:
# поставить uv (он же принесёт нужный Python), синхронизировать зависимости,
# найти или скачать ffmpeg, создать .env и поднять сервер.
#
# Всё, что мы доустанавливаем сами, лежит в runtime/ внутри папки приложения —
# система остаётся нетронутой, удаление папки удаляет приложение целиком.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$APP_DIR/runtime"
RUNTIME_BIN="$RUNTIME_DIR/bin"
PORT="${PORT:-5050}"

mkdir -p "$RUNTIME_BIN"
export PATH="$RUNTIME_BIN:$PATH"

# Интерпретаторы, которые качает uv, держим рядом с приложением: удаление папки
# должно уносить с собой всё, что мы доустановили
export UV_PYTHON_INSTALL_DIR="$RUNTIME_DIR/python"

case "$(uname -s)" in
  Darwin) OS=macos ;;
  Linux)  OS=linux ;;
  *) echo "Неподдерживаемая система: $(uname -s)" >&2; exit 1 ;;
esac

say()  { printf '\033[36m▸\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# ── uv ────────────────────────────────────────────────────────────────────────
# Ставим в runtime/, а не в систему: не трогаем уже настроенный у пользователя uv
# и не зависим от того, попал ли ~/.local/bin в PATH.
ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    ok "uv найден: $(uv --version)"
    return
  fi
  say "Устанавливаю uv..."
  command -v curl >/dev/null 2>&1 || die "нужен curl (Ubuntu/Debian: sudo apt install curl)"
  # UV_INSTALL_DIR поддерживается официальным установщиком
  UV_INSTALL_DIR="$RUNTIME_BIN" curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  command -v uv >/dev/null 2>&1 || die "uv не установился, поставьте вручную: https://docs.astral.sh/uv/"
  ok "uv установлен"
}

# ── ffmpeg ────────────────────────────────────────────────────────────────────
# Нужен и сам ffmpeg, и ffprobe. Пробуем в порядке «меньше вмешательства»:
# уже установленный → пакетный менеджер → статическая сборка в runtime/.
ensure_ffmpeg() {
  if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
    ok "ffmpeg найден: $(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f1-3)"
    return
  fi
  say "ffmpeg не найден, устанавливаю..."

  if [ "$OS" = macos ] && command -v brew >/dev/null 2>&1; then
    brew install ffmpeg && return
  fi
  if [ "$OS" = linux ]; then
    if command -v apt-get >/dev/null 2>&1; then
      warn "Требуются права root для установки ffmpeg"
      sudo apt-get update -qq && sudo apt-get install -y ffmpeg && return
    elif command -v dnf >/dev/null 2>&1; then
      sudo dnf install -y ffmpeg && return
    elif command -v pacman >/dev/null 2>&1; then
      sudo pacman -S --noconfirm ffmpeg && return
    fi
  fi

  die "не удалось поставить ffmpeg автоматически.
   macOS:  brew install ffmpeg
   Ubuntu: sudo apt install ffmpeg
   Fedora: sudo dnf install ffmpeg"
}

# ── зависимости проекта ───────────────────────────────────────────────────────
# uv sync сам скачает интерпретатор нужной версии, если в системе его нет,
# поэтому отдельная установка Python не требуется.
ensure_deps() {
  say "Синхронизирую зависимости..."
  (cd "$APP_DIR" && uv sync --quiet)
  ok "Зависимости готовы"
}

ensure_env() {
  if [ ! -f "$APP_DIR/.env" ] && [ -f "$APP_DIR/.env.example" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    ok "Создан .env (ключи API можно задать в настройках приложения)"
  fi
}

main() {
  echo
  say "Video-Dub — подготовка к запуску"
  ensure_uv
  ensure_ffmpeg
  ensure_deps
  ensure_env
  echo
  ok "Открываю http://127.0.0.1:$PORT"
  echo
  cd "$APP_DIR"
  # Браузер откроет сам сервер — только когда действительно начнёт слушать порт
  VIDEO_DUB_OPEN_BROWSER=1 PORT="$PORT" exec uv run python app.py
}

main "$@"
