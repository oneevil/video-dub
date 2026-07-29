#!/usr/bin/env bash
# Готовит сертификат Developer ID для подписи в GitHub Actions.
#
#   packaging/macos/export_cert.sh ~/Desktop/developer-id.p12
#
# Файл .p12 экспортируется из «Связки ключей»: найдите «Developer ID
# Application: …», раскройте треугольник (внутри должен быть закрытый ключ),
# правый клик по сертификату → «Экспортировать» → формат «Личный обмен
# информацией (.p12)» → задайте пароль.
#
# CLI-экспорт здесь не используется намеренно: `security export -t identities`
# выгружает разом все идентичности, включая iPhone Developer и Apple
# Development, которым в секретах репозитория делать нечего.
set -euo pipefail

P12="${1:-}"
TEAM_ID_DEFAULT="76KE738FKP"

die() { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
ok()  { printf '\033[32m✓\033[0m %s\n' "$*"; }

[ -n "$P12" ] || die "укажите путь к .p12 — см. комментарий в начале скрипта"
[ -f "$P12" ] || die "файл не найден: $P12"

# Проверяем импортом во временную связку ключей — ровно так же, как это делает
# CI. openssl здесь не годится: «Связка ключей» пишет .p12 старыми алгоритмами
# (PBE-SHA1-3DES/RC2), а OpenSSL 3 без провайдера legacy отвергает их с ошибкой,
# неотличимой от неверного пароля.
read -r -s -p "Пароль от .p12: " P12_PWD
echo

TMP_DIR="$(mktemp -d)"
KEYCHAIN="$TMP_DIR/verify.keychain"
trap 'security delete-keychain "$KEYCHAIN" 2>/dev/null || true; rm -rf "$TMP_DIR"' EXIT

security create-keychain -p verify "$KEYCHAIN" >/dev/null
security unlock-keychain -p verify "$KEYCHAIN" >/dev/null
security import "$P12" -k "$KEYCHAIN" -P "$P12_PWD" -T /usr/bin/codesign >/dev/null 2>&1 \
  || die "не удалось импортировать .p12 — проверьте пароль"

# find-identity показывает только полные идентичности: сертификат + закрытый
# ключ. Если ключ не экспортировали, список окажется пустым — и codesign в CI
# молча не нашёл бы, чем подписывать.
#
# Без -v намеренно: этот флаг дополнительно требует доверенной цепочки, а во
# временной связке промежуточных сертификатов Apple нет, и валидный ключ
# выглядел бы отсутствующим.
#
# `|| true` обязателен: при pipefail пустой grep вернёт 1 и set -e оборвёт
# скрипт молча, не дав показать объяснение ниже
IDENTITY="$(security find-identity -p codesigning "$KEYCHAIN" 2>/dev/null \
            | grep 'Developer ID Application' | head -1 | sed 's/.*"\([^"]*\)".*/\1/' || true)"
[ -n "$IDENTITY" ] || die "в .p12 нет пары «Developer ID Application + закрытый ключ».
   В «Связке ключей» раскройте треугольник у сертификата и экспортируйте его
   вместе с ключом, выбрав именно сертификат, а не ключ."

ok "Сертификат корректен: $IDENTITY"

TEAM_ID="$(sed -n 's/.*(\([A-Z0-9]*\))$/\1/p' <<<"$IDENTITY")"
[ -n "$TEAM_ID" ] || TEAM_ID="$TEAM_ID_DEFAULT"

base64 -i "$P12" | tr -d '\n' | pbcopy
ok "Base64 сертификата скопирован в буфер обмена"

cat <<EOF

Создайте секреты в репозитории:
  Settings → Secrets and variables → Actions → New repository secret

  MACOS_CERTIFICATE       вставьте из буфера (уже там)
  MACOS_CERTIFICATE_PWD   пароль от .p12, который вы только что ввели
  NOTARY_APPLE_ID         e-mail вашего Apple ID
  NOTARY_PASSWORD         app-specific password с appleid.apple.com
  NOTARY_TEAM_ID          $TEAM_ID

После этого удалите .p12 — в репозитории и на диске он больше не нужен:
  rm "$P12"

Проверка: git tag v0.1.1 && git push --tags — сборка macOS должна
завершиться строкой «✓ Нотаризовано».
EOF
