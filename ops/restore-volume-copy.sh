#!/bin/sh
# Restore one archive into a new Docker volume. Refuses the operational volume names.
set -eu
if [ "$#" -ne 2 ]; then
  echo "usage: ops/restore-volume-copy.sh <archive.tar.gz> <new-volume-name>" >&2
  exit 2
fi
archive=$1
volume=$2
case "$volume" in
  kumbh-setu_pgdata|kumbh-setu_objectdata)
    echo "refusing to write operational volume $volume" >&2
    exit 2
    ;;
esac
if [ ! -f "$archive" ]; then
  echo "archive not found" >&2
  exit 2
fi
docker volume create "$volume" >/dev/null
docker run --rm -v "$volume":/dest -v "$(cd "$(dirname "$archive")" && pwd)":/backup:ro alpine:3.20 \
  tar -C /dest -xzf "/backup/$(basename "$archive")"
echo "restored=$volume"
