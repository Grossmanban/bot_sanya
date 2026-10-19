#!/usr/bin/env bash
# Owners of the bot (ADMIN_IDS in .env): list, add, remove. Restarts the app so the change applies.
#
#   bash deploy/owners.sh               — who is an owner now
#   bash deploy/owners.sh add 123456789 — make a Telegram ID an owner (others stay)
#   bash deploy/owners.sh remove 123456789
#
# A Telegram ID is shown in the app: «Админ» → «Команда» → tap the person → «ID …».
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE=.env
[ -f "$ENV_FILE" ] || { echo "Нет файла .env в $(pwd). Запустите скрипт из папки бота."; exit 1; }

current() {
  grep -E '^ADMIN_IDS=' "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d ' "' | tr ',' '\n' | grep -E '^[0-9]+$' || true
}

save() {  # $@ = ids
  local line
  line="ADMIN_IDS=$(printf '%s\n' "$@" | awk 'NF && !seen[$0]++' | paste -sd, -)"
  cp "$ENV_FILE" "$ENV_FILE.bak"
  if grep -qE '^ADMIN_IDS=' "$ENV_FILE"; then
    sed -i -E "s/^ADMIN_IDS=.*/$line/" "$ENV_FILE"
  else
    echo "$line" >> "$ENV_FILE"
  fi
  echo "Сохранено: $line (копия прежнего файла — .env.bak)"
  echo "Перезапускаю бота…"
  if docker compose up -d --force-recreate app >/dev/null; then
    echo "Готово. Новому владельцу достаточно заново открыть приложение."
  else
    echo "Список сохранён, но бот не перезапустился. Запустите вручную: docker compose up -d --force-recreate app"
    exit 1
  fi
}

show() {
  local ids
  ids=$(current)
  if [ -z "$ids" ]; then
    echo "Владельцев нет: строка ADMIN_IDS в .env пустая."
    return
  fi
  echo "Владельцы:"
  # names from the bot's database, when the app is running
  local names=""
  names=$(docker compose exec -T app python - "$ids" <<'PY' 2>/dev/null || true
import os, sqlite3, sys
db = sqlite3.connect(os.environ.get("DB_PATH", "/data/bot.db"))
for tg in sys.argv[1].split():
    row = db.execute("SELECT first_name, last_name, username FROM users WHERE telegram_id = ?", (int(tg),)).fetchone()
    name = " ".join(x for x in row[:2] if x) + (f" (@{row[2]})" if row and row[2] else "") if row else "ещё не открывал бота"
    print(f"{tg}\t{name}")
PY
)
  if [ -n "$names" ]; then
    printf '%s\n' "$names" | while IFS=$'\t' read -r id name; do echo "  • $id — $name"; done
  else
    printf '%s\n' "$ids" | sed 's/^/  • /'
  fi
}

check_id() {
  [[ "${1:-}" =~ ^[0-9]{5,15}$ ]] || { echo "Нужен Telegram ID — только цифры, например: bash deploy/owners.sh add 123456789"; exit 1; }
}

case "${1:-list}" in
  list|"")
    show
    ;;
  add)
    check_id "${2:-}"
    if current | grep -qx "$2"; then
      echo "$2 уже владелец, ничего не меняю."
      exit 0
    fi
    mapfile -t ids < <(current)
    save "${ids[@]}" "$2"
    ;;
  remove)
    check_id "${2:-}"
    current | grep -qx "$2" || { echo "$2 не владелец, ничего не меняю."; exit 0; }
    mapfile -t ids < <(current | grep -vx "$2")
    [ "${#ids[@]}" -gt 0 ] || { echo "Нельзя убрать последнего владельца — сначала добавьте другого."; exit 1; }
    save "${ids[@]}"
    ;;
  *)
    echo "Команды: list | add <ID> | remove <ID>"
    exit 1
    ;;
esac
