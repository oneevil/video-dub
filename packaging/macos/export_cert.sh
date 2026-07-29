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

# Проверяем, что внутри именно Developer ID и что закрытый ключ на месте:
# без ключа codesign в CI молча не найдёт идентичность
read -r -s -p "Пароль от .p12: " P12_PWD
echo
DUMP="$(openssl pkcs12 -in "$P12" -passin pass:"$P12_PWD" -nokeys -clcerts 2>/dev/null \
        | openssl x509 -noout -subject -enddate 2>/dev/null)" \
  || die "не удалось прочитать .p12 — неверный пароль?"

grep -q "Developer ID Application" <<<"$DUMP" \
  || die "в .p12 нет сертификата Developer ID Application:\n$DUMP"
openssl pkcs12 -in "$P12" -passin pass:"$P12_PWD" -nocerts -noout 2>/dev/null \
  || die "в .p12 нет закрытого ключа — экспортируйте сертификат вместе с ним"

ok "Сертификат корректен"
sed 's/^/   /' <<<"$DUMP"

TEAM_ID="$(sed -n 's/.*Developer ID Application: .*(\([A-Z0-9]*\)).*/\1/p' <<<"$DUMP")"
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
