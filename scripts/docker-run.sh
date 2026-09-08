#!/usr/bin/env bash
#
# Run the AFAS Thuiswerkdag CLI inside the container, with credentials and a
# browser session profile mounted in.
#
#   scripts/docker-run.sh --today --dry-run --headless
#
# Two deliberate choices:
#
# * .env is MOUNTED rather than passed with --env-file, so python-dotenv parses
#   it with exactly the same rules as a host run and the values stay out of the
#   container environment (and so out of `docker inspect`).
#
# * The container gets its OWN profile directory, seeded once from the host's.
#   The container's Chromium is a different build from the host's, and a newer
#   Chromium writes profile state an older one cannot read -- sharing the
#   directory would eventually cost you the logged-in session on the desktop,
#   which is the one thing that is expensive to recreate.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${AFAS_IMAGE:-afas-thuiswerk:local}"
ENGINE="${AFAS_CONTAINER_ENGINE:-docker}"
PROFILE="${AFAS_DOCKER_PROFILE:-$ROOT/.browser-profile-docker}"
HOST_PROFILE="$ROOT/.browser-profile"

[ -f "$ROOT/.env" ] || { echo "error: $ROOT/.env not found" >&2; exit 64; }

if [ ! -d "$PROFILE" ]; then
  if [ -d "$HOST_PROFILE" ]; then
    echo "seeding container profile from host session (one-off copy)..." >&2
    cp -a "$HOST_PROFILE" "$PROFILE"
  else
    mkdir -p "$PROFILE"
  fi
fi
mkdir -p "$ROOT/artifacts"

# --userns=keep-id:uid=1001,gid=1001: rootless podman otherwise maps the
#   container's pwuser to a subuid that can neither read .env (mode 600) nor
#   write the profile. The explicit uid matters -- pwuser is 1001, not 1000,
#   because Ubuntu noble already ships a uid-1000 "ubuntu" user, so a plain
#   keep-id maps you to 1000 while the process runs as 1001. Set
#   AFAS_KEEP_ID=0 under Docker proper, which does not accept the flag.
# --shm-size=1g: Chromium crashes on the default 64 MB /dev/shm.
declare -a ENGINE_ARGS=(--rm --shm-size=1g)
[ -t 0 ] && ENGINE_ARGS+=(-it)
[ "${AFAS_KEEP_ID:-1}" = "1" ] && ENGINE_ARGS+=(--userns=keep-id:uid=1001,gid=1001)

exec "$ENGINE" run "${ENGINE_ARGS[@]}" \
  -v "$ROOT/.env:/app/.env:ro" \
  -v "$PROFILE:/app/.browser-profile" \
  -v "$ROOT/artifacts:/app/artifacts" \
  "$IMAGE" "$@"
