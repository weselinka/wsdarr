#!/bin/sh
# Run as PUID:PGID (linuxserver.io convention) so files in /downloads get the same owner
# as the files Sonarr/Radarr create.
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
umask "${UMASK:-002}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "${CONFIG_DIR:-/config}" "${DOWNLOAD_DIR:-/downloads}/complete" "${DOWNLOAD_DIR:-/downloads}/incomplete"
    chown -R "$PUID:$PGID" "${CONFIG_DIR:-/config}"
    chown "$PUID:$PGID" "${DOWNLOAD_DIR:-/downloads}" "${DOWNLOAD_DIR:-/downloads}/complete" \
        "${DOWNLOAD_DIR:-/downloads}/incomplete"
    export HOME="${CONFIG_DIR:-/config}"
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi

exec "$@"
