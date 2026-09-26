#!/usr/bin/env bash
# Принудительная команда отдельного SSH-ключа CI. Произвольная команда запрещена.
set -euo pipefail

case "${SSH_ORIGINAL_COMMAND:-}" in
  load)
    gzip -d | docker load
    ;;
  activate:*)
    revision=${SSH_ORIGINAL_COMMAND#activate:}
    if [[ ! "$revision" =~ ^[a-f0-9]{40}$ ]]; then
      echo 'Некорректный SHA коммита' >&2
      exit 2
    fi
    ZD_IMAGE="zero-defect:$revision" bash /opt/krendels-final/activate.sh
    ;;
  *)
    echo 'Для этого ключа команда запрещена' >&2
    exit 2
    ;;
esac
