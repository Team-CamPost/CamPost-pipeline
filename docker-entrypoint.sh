#!/bin/sh
set -e

mkdir -p /data /ms-playwright

if [ "$(id -u)" = "0" ]; then
    chown -R app:app /data /ms-playwright
    exec gosu app "$@"
fi

exec "$@"
