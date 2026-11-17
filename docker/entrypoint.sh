#!/bin/sh
set -e

python docker/wait_for_db.py

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    python manage.py migrate --noinput
    python manage.py createcachetable
fi

if [ "${COLLECT_STATIC:-1}" = "1" ]; then
    python manage.py collectstatic --noinput
fi

if [ "${WARM_DCR_CACHE:-1}" = "1" ]; then
    # slow (large DCR resource docs); runs beside the server, failures are not fatal
    python manage.py warm_dcr_cache >/dev/null 2>&1 &
fi

exec "$@"
