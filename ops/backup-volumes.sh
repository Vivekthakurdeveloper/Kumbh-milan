#!/bin/sh
# Read-only archives of the named Docker volumes. Does not start PostgreSQL.
set -eu
if [ "$#" -ne 0 ]; then
  echo "usage: ops/backup-volumes.sh" >&2
  exit 2
fi
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
stamp=$(date -u +%Y%m%dT%H%M%SZ)
dest="$root/.backups/$stamp"
mkdir -p "$dest"
printf '%s\n' "$stamp" > "$dest/stamp.txt"
for volume in kumbh-setu_pgdata kumbh-setu_objectdata; do
  name=${volume#kumbh-setu_}
  docker run --rm -v "$volume":/source:ro -v "$dest":/backup alpine:3.20 \
    tar -C /source -czf "/backup/${name}.tar.gz" .
done
docker run --rm -v "$dest":/backup alpine:3.20 chown -R "$(id -u):$(id -g)" /backup
cd "$dest"
shasum -a 256 pgdata.tar.gz objectdata.tar.gz > SHA256SUMS
shasum -a 256 -c SHA256SUMS
echo "backup=$dest"
