#!/bin/sh
# Однократная подготовка чистого Ubuntu/Debian-сервера: Docker и файрвол.
set -e
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker
if command -v ufw >/dev/null 2>&1; then
  ufw allow OpenSSH
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw --force enable
fi
mkdir -p /opt/date-map
echo "Сервер готов"
