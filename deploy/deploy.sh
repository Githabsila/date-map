#!/bin/sh
# Выкладка с этого компьютера на VPS.
# Использование: sh deploy/deploy.sh <IP сервера> [домен]
# Без домена сайт будет доступен по адресу <ip-через-дефисы>.sslip.io (бесплатно, с HTTPS).
set -e
IP="$1"
[ -n "$IP" ] || { echo "Укажи IP сервера: sh deploy/deploy.sh 1.2.3.4"; exit 1; }
DOMAIN="${2:-$(echo "$IP" | tr . -).sslip.io}"
KEY="${SSH_KEY:-$HOME/.ssh/date_map_vps}"
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new root@$IP"
cd "$(dirname "$0")/.."

$SSH 'sh -s' < deploy/setup-server.sh
tar czf - Dockerfile docker-compose.yml requirements.txt app static deploy scripts/fetch_db.py | $SSH 'tar xzf - -C /opt/date-map'
# База мест: копируем, только если изменилась (сравниваем контрольные суммы)
LOCAL_SUM=$(sha256sum data/places.db | cut -d' ' -f1)
REMOTE_SUM=$($SSH "sha256sum /opt/date-map/data/places.db 2>/dev/null | cut -d' ' -f1" || true)
if [ "$LOCAL_SUM" != "$REMOTE_SUM" ]; then
  echo "Копирую базу мест ($(du -h data/places.db | cut -f1))..."
  $SSH 'mkdir -p /opt/date-map/data'
  gzip -c data/places.db | $SSH 'gunzip > /opt/date-map/data/places.db.new && mv /opt/date-map/data/places.db.new /opt/date-map/data/places.db'
fi
# .env с ключами передаём отдельно; DOMAIN дописываем для Caddy
{ grep -v '^DOMAIN=' .env; echo "DOMAIN=$DOMAIN"; } | $SSH 'cat > /opt/date-map/.env && chmod 600 /opt/date-map/.env'
$SSH "cd /opt/date-map && docker compose --env-file .env up -d --build && docker image prune -f"
echo "Готово: https://$DOMAIN"
