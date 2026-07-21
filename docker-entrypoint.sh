#!/bin/sh
set -eu

mkdir -p /vault

if [ ! -f /vault/_meta/vault_config.yaml ]; then
  cp -an /opt/memory-vault/template/. /vault/
fi

mkdir -p \
  /vault/memories \
  /vault/tasks \
  /vault/inbox \
  /vault/projects \
  /vault/diary \
  /vault/_archive/retired

exec python /opt/memory-vault/_meta/mcp_server.py \
  --http \
  --host 0.0.0.0 \
  --port 8900
