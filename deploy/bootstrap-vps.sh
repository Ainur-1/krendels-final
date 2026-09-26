#!/usr/bin/env bash
# Первый запуск на чистом Ubuntu 26.04. Выполнять от root на VPS.
set -euo pipefail

apt-get update
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' \
  "$(dpkg --print-architecture)" "$(. /etc/os-release && echo "$VERSION_CODENAME")" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker

install -d -m 0700 /opt/krendels-final
if ! test -e /opt/krendels-final/.env; then
  password=$(openssl rand -hex 32)
  umask 077
  printf 'ZD_DB_PASSWORD=%s\n' "$password" > /opt/krendels-final/.env
fi
chmod 0600 /opt/krendels-final/.env
docker version --format '{{.Server.Version}}'
docker compose version
