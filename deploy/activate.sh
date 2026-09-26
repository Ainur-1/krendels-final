#!/usr/bin/env bash
# Запускается на VPS после загрузки образа. Пароль базы остаётся только на сервере.
set -euo pipefail

dir=/opt/krendels-final
cd "$dir"
test -s .env
test -n "${ZD_IMAGE:-}"

previous=''
if test -s current-image; then
  previous=$(cat current-image)
fi

compose() {
  docker compose --env-file "$dir/.env" -f "$dir/compose.vps.yml" "$@"
}

healthy() {
  local attempt container emulator revision
  container=$(compose ps -q app)
  emulator=$(compose ps -q emulator)
  test -n "$container"
  test -n "$emulator"
  for attempt in $(seq 1 90); do
    if test "$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null)" = healthy &&
       test "$(docker inspect -f '{{.State.Health.Status}}' "$emulator" 2>/dev/null)" = healthy; then
      revision=$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$container")
      test "$revision" = "${ZD_IMAGE##*:}"
      curl --fail --silent --show-error http://127.0.0.1:8000/api/health >/dev/null
      return 0
    fi
    sleep 2
  done
  return 1
}

if compose up -d --no-build && healthy; then
  printf '%s\n' "$ZD_IMAGE" > current-image
  printf 'Развёрнут %s\n' "$ZD_IMAGE"
  exit 0
fi

echo 'Новый образ не прошёл проверку здоровья' >&2
compose ps >&2 || true
compose logs --tail=80 app db emulator >&2 || true
if test -n "$previous" && test "$previous" != "$ZD_IMAGE"; then
  echo "Возврат к $previous" >&2
  ZD_IMAGE="$previous" compose up -d --no-build
  ZD_IMAGE="$previous" healthy
fi
exit 1
