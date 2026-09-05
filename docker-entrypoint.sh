#!/bin/sh
set -eu
umask 022

if [ "$(id -u)" = "0" ]; then
    case "${PUID}:${PGID}" in
        *[!0-9:]*|:*|*:) echo "PUID and PGID must be numeric" >&2; exit 2 ;;
    esac
    mkdir -p /data /state
    chown "${PUID}:${PGID}" /data /state
    chmod 755 /data
    chmod 700 /state
    exec gosu "${PUID}:${PGID}" "$@"
fi

exec "$@"
