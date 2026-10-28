#!/bin/sh
set -eu

if [ "${INIT_DB:-1}" = "1" ]; then
    echo "[entrypoint] applying schema, views and migrations"
    python scripts/init_db.py --skip-test
    if [ "${SEED_DEMO:-1}" = "1" ]; then
        python scripts/seed_demo.py --if-empty
    fi
fi

exec "$@"
