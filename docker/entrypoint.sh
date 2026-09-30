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

exec "$@"
