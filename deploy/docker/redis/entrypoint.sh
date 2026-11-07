#!/bin/sh
set -eu
if [ -z "${REDIS_PASSWORD:-}" ]; then
  echo "REDIS_PASSWORD is empty" >&2
  exit 1
fi
umask 077
cat > /tmp/redis.conf <<EOF
bind 0.0.0.0
protected-mode yes
port 6379
appendonly yes
appendfsync everysec
aof-use-rdb-preamble yes
save 900 1
save 300 10
save 60 10000
dir /data
dbfilename dump.rdb
requirepass ${REDIS_PASSWORD}
EOF
exec redis-server /tmp/redis.conf
