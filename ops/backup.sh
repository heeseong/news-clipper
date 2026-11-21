#!/usr/bin/env bash
# Back up what cannot be made again (docs/deploy.md "백업"):
#   - the PostgreSQL database (jobs, cue sheets, clips, review history, uploads, encrypted channel tokens)
#   - storage/branding (thumbnail template images)
#   - .env (settings and keys, incl. TOKEN_ENCRYPTION_KEY: without it the channel tokens are useless)
# Recordings, proxies and renders are not backed up (large; the recordings exist elsewhere).
#
# Usage (from the repository directory, where docker-compose.yml is):
#   ops/backup.sh /backup/news-clipper            # keeps 14 days by default
#   KEEP_DAYS=30 ops/backup.sh /mnt/nas/news-clipper
# Daily at 04:30 (crontab -e):
#   30 4 * * * cd /opt/news-clipper && ops/backup.sh /backup/news-clipper >> /var/log/news-clipper-backup.log 2>&1
set -euo pipefail

dest=${1:?usage: ops/backup.sh DEST_DIR}
keep_days=${KEEP_DAYS:-14}
stamp=$(date +%Y%m%d-%H%M%S)
out="$dest/$stamp"
mkdir -p "$out"
chmod 700 "$dest" "$out"

# Variables docker compose uses (POSTGRES_USER, POSTGRES_DB)
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
pg_user=${POSTGRES_USER:-newsclipper}
pg_db=${POSTGRES_DB:-newsclipper}

echo "[$(date -Is)] backup to $out"

# Database: custom format (pg_restore can restore one table if needed)
docker compose exec -T postgres pg_dump -U "$pg_user" -d "$pg_db" --format=custom --no-owner > "$out/db.dump"
test -s "$out/db.dump"

# Thumbnail templates from the storage volume
docker compose exec -T api sh -c 'cd "${STORAGE_ROOT:-/data/storage}" && if [ -d branding ]; then tar czf - branding; fi' \
  > "$out/branding.tar.gz"

# Settings and keys
if [[ -f .env ]]; then
  cp .env "$out/env"
  chmod 600 "$out/env"
fi

(cd "$out" && sha256sum ./* > SHA256SUMS)
echo "[$(date -Is)] done: $(du -sh "$out" | cut -f1)"

# Remove old backups
find "$dest" -mindepth 1 -maxdepth 1 -type d -name '20*' -mtime +"$keep_days" -print -exec rm -rf {} +
