#!/usr/bin/env bash
# The bot's token (BOT_TOKEN in .env): show which bot is connected, or replace the token after /revoke in @BotFather.
#
#   bash deploy/token.sh         — ask for the new token (typed hidden), check it with Telegram, save, restart
#   bash deploy/token.sh check   — which bot the current token belongs to (the token itself is never shown)
#
# The token is never taken as an argument, so it doesn't stay in the shell history. The database, owners and
# everything else are not touched; the previous .env is kept as .env.bak.
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE=.env
[ -f "$ENV_FILE" ] || { echo "Нет файла .env в $(pwd). Запустите скрипт из папки бота."; exit 1; }

current_token() {
  grep -E '^BOT_TOKEN=' "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d ' "'"'" || true
}

mask() {  # 1234567890:AAE…xYz9 → 1234567890:…xYz9
  local t="$1"
  [ -n "$t" ] && echo "${t%%:*}:…${t: -4}" || echo "не задан"
}

# prints "@username|id" for a working token, nothing for a wrong or revoked one
bot_of() {
  local answer
  answer=$(curl -s -m 15 "https://api.telegram.org/bot$1/getMe" || true)
  case "$answer" in
    *'"ok":true'*)
      local id user
      id=$(printf '%s' "$answer" | grep -o '"id":[0-9]*' | head -n1 | cut -d: -f2)
      user=$(printf '%s' "$answer" | grep -o '"username":"[^"]*"' | head -n1 | cut -d'"' -f4)
      echo "@$user|$id"
      ;;
  esac
}

show() {
  local token info
  token=$(current_token)
  info=$(bot_of "$token")
  if [ -n "$info" ]; then
    echo "Сейчас подключён бот ${info%%|*} (токен $(mask "$token"))."
  else
    echo "Текущий токен ($(mask "$token")) Telegram не принимает: он отозван или неверный."
  fi
}

case "${1:-set}" in
  check)
    show
    ;;
  set|"")
    show
    old_info=$(bot_of "$(current_token)")
    echo
    echo "Новый токен: @BotFather → /mybots → бот → API Token (или /revoke — старый сразу перестанет работать)."
    read -rsp "Вставьте новый токен (ввод не отображается): " NEW; echo
    NEW=$(printf '%s' "$NEW" | tr -d ' "'"'")
    [[ "$NEW" =~ ^[0-9]{5,15}:[A-Za-z0-9_-]{30,}$ ]] || { echo "Это не похоже на токен бота (вид: 1234567890:AAE…). Ничего не меняю."; exit 1; }
    [ "$NEW" != "$(current_token)" ] || { echo "Это тот же токен, что уже стоит. Ничего не меняю."; exit 0; }
    new_info=$(bot_of "$NEW")
    [ -n "$new_info" ] || { echo "Telegram не принял этот токен (неверный или уже отозван). Ничего не меняю."; exit 1; }
    echo "Токен рабочий: бот ${new_info%%|*}."
    old_id=$(current_token); old_id=${old_id%%:*}  # the bot's number is the token's part before «:» — known even after /revoke
    if [ -n "$old_id" ] && [ "$old_id" != "${new_info##*|}" ]; then
      echo "⚠️  Это ДРУГОЙ бот: было ${old_info%%|*}${old_info:+ }(№$old_id), станет ${new_info%%|*}."
      echo "   Люди пишут старому боту — им придётся найти нового. База и владельцы останутся."
      read -rp "Точно переключить на другого бота? Напишите «да»: " yes
      [ "$yes" = "да" ] || { echo "Отменено, ничего не менял."; exit 0; }
    fi
    cp "$ENV_FILE" "$ENV_FILE.bak"
    chmod 600 "$ENV_FILE.bak"
    if grep -qE '^BOT_TOKEN=' "$ENV_FILE"; then
      sed -i -E "s|^BOT_TOKEN=.*|BOT_TOKEN=$NEW|" "$ENV_FILE"
    else
      echo "BOT_TOKEN=$NEW" >> "$ENV_FILE"
    fi
    chmod 600 "$ENV_FILE"
    echo "Сохранено (копия прежнего файла — .env.bak). Перезапускаю бота…"
    docker compose up -d --force-recreate app >/dev/null || {
      echo "Токен сохранён, но бот не перезапустился. Запустите вручную: docker compose up -d --force-recreate app"; exit 1; }
    for _ in $(seq 1 20); do
      sleep 3
      if docker compose logs --since 2m app 2>/dev/null | grep -q "Run polling for bot"; then
        echo "Готово: бот ${new_info%%|*} работает. Ссылки и кнопки приложения остаются прежними."
        exit 0
      fi
    done
    echo "Бот перезапущен, но запуск не подтвердился за минуту. Посмотрите: docker compose logs --tail 30 app"
    exit 1
    ;;
  *)
    echo "Команды: (без аргументов) — заменить токен | check — какой бот подключён"
    exit 1
    ;;
esac
