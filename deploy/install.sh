#!/usr/bin/env bash
# One-shot installer for a fresh Ubuntu 22.04/24.04 VPS (run as root).
#
#   bash install.sh
#
# Asks for the bot token, admin IDs and (for the private repo) a GitHub token,
# then installs Docker, clones the project to /opt/bot_sanya and starts it.
# Without a domain it uses <ip>.sslip.io — a free name that resolves to the server IP,
# so Caddy can still get a real HTTPS certificate. Re-running the script updates the app.
set -euo pipefail
export GIT_TERMINAL_PROMPT=0  # never hang on a git password prompt

REPO="Grossmanban/bot_sanya"
BRANCH="${BRANCH:-claude/schedule-bot-miniapp}"
DIR="/opt/bot_sanya"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mОшибка: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "запустите от root (sudo -i)"
command -v apt-get >/dev/null || die "нужна Ubuntu/Debian"

say "Проверяю доступ к Telegram API"
if ! curl -s -m 15 -o /dev/null https://api.telegram.org; then
  die "сервер не достаёт до api.telegram.org — бот здесь работать не сможет. Нужен другой сервер или прокси."
fi

say "Swap (сборке фронтенда нужно ~1.5 ГБ памяти)"
if [ "$(swapon --show --noheadings | wc -l)" -eq 0 ] && [ "$(awk '/MemTotal/ {print $2}' /proc/meminfo)" -lt 3000000 ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "swap 2G включён"
else
  echo "swap не нужен или уже есть"
fi

say "Устанавливаю Docker и git"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ca-certificates sqlite3 >/dev/null
if ! command -v docker >/dev/null; then
  apt-get install -y -qq docker.io >/dev/null
fi
if ! docker compose version >/dev/null 2>&1; then
  apt-get install -y -qq docker-compose-v2 >/dev/null 2>&1 || apt-get install -y -qq docker-compose-plugin >/dev/null 2>&1 \
    || die "не удалось поставить docker compose"
fi
# Docker Hub is sometimes unreachable from Russian networks: add Google's public mirror as a fallback
if [ ! -f /etc/docker/daemon.json ]; then
  mkdir -p /etc/docker
  echo '{ "registry-mirrors": ["https://mirror.gcr.io"] }' > /etc/docker/daemon.json
fi
systemctl enable --now docker >/dev/null
systemctl restart docker

say "Скачиваю проект"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch -q origin "$BRANCH" && git -C "$DIR" checkout -q "$BRANCH" && git -C "$DIR" reset -q --hard "origin/$BRANCH"
else
  if ! git ls-remote "https://github.com/$REPO.git" >/dev/null 2>&1; then
    echo "Репозиторий приватный. Нужен GitHub-токен только на чтение:"
    echo "  github.com → Settings → Developer settings → Fine-grained tokens → Generate,"
    echo "  Repository access: только $REPO, Permissions → Contents: Read-only."
    read -rsp "GitHub токен: " GH_TOKEN; echo
    URL="https://x-access-token:${GH_TOKEN}@github.com/$REPO.git"
  else
    URL="https://github.com/$REPO.git"
  fi
  git clone -q --branch "$BRANCH" "$URL" "$DIR" || die "не удалось скачать репозиторий (проверьте токен)"
  # don't keep the token in .git/config; updates will ask again
  git -C "$DIR" remote set-url origin "https://github.com/$REPO.git"
fi
cd "$DIR"

say "Настройки"
if [ ! -f .env ]; then
  read -rsp "Токен бота от @BotFather: " BOT_TOKEN; echo
  read -rp "Telegram ID админов через запятую (свой ID: @userinfobot): " ADMIN_IDS
  IP="$(curl -s -m 10 https://api.ipify.org || curl -s -m 10 https://ifconfig.me)"
  [ -n "$IP" ] || die "не удалось определить внешний IP"
  DEFAULT_DOMAIN="$(echo "$IP" | tr '.' '-').sslip.io"
  read -rp "Домен [Enter = $DEFAULT_DOMAIN]: " DOMAIN
  DOMAIN="${DOMAIN:-$DEFAULT_DOMAIN}"
  cat > .env <<EOF
BOT_TOKEN=$BOT_TOKEN
ADMIN_IDS=$ADMIN_IDS
DOMAIN=$DOMAIN
WEBAPP_URL=https://$DOMAIN
TIMEZONE=Europe/Moscow
EOF
  chmod 600 .env
else
  echo ".env уже есть — оставляю как есть"
fi
DOMAIN="$(grep '^DOMAIN=' .env | cut -d= -f2)"

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null
fi

say "Собираю и запускаю (первый раз — несколько минут)"
mkdir -p data
docker compose up -d --build

say "Ежедневный бэкап базы в 04:00 (храним 14 копий в $DIR/data/backups)"
echo "0 4 * * * root cd $DIR && docker compose exec -T app python scripts/backup.py /data/backups >/dev/null 2>&1" \
  > /etc/cron.d/bot_sanya_backup

say "Жду HTTPS-сертификат"
for _ in $(seq 1 30); do
  if curl -s -m 5 -o /dev/null "https://$DOMAIN/healthz"; then
    printf '\n\033[1;32mГотово!\033[0m Mini App: https://%s\nНапишите боту /start.\n' "$DOMAIN"
    exit 0
  fi
  sleep 4
done
echo "Приложение запущено, но https://$DOMAIN пока не отвечает. Логи: cd $DIR && docker compose logs --tail=50"
