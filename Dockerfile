FROM python:3.12-slim

ARG APP_VERSION=dev
LABEL org.opencontainers.image.title="operator-platform-api" \
      org.opencontainers.image.description="OGCR operator platform API (Django REST, PostGIS)" \
      org.opencontainers.image.source="https://github.com/OGCR-GILAB/operator-platform-api" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV APP_VERSION=${APP_VERSION} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl binutils gdal-bin \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --create-home app

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .
RUN chmod +x docker/entrypoint.sh \
    && mkdir -p /app/staticfiles /app/media \
    && chown -R app:app /app

USER app
EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "-c", "docker/gunicorn.conf.py", "config.wsgi:application"]
