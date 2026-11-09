# GB Grid: a live dashboard and power station map for the Great Britain
# electricity grid. The app is Python standard library only, so the image is
# the official Python base image plus the app's files.
FROM python:3.15-alpine

ARG VERSION=dev

LABEL org.opencontainers.image.title="GB Grid" \
      org.opencontainers.image.description="Self-hosted live dashboard and power station map for the Great Britain electricity grid" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/London \
    DATA_DIR=/data \
    GRID_INTERNAL_PORT=8080 \
    GRID_VERSION=${VERSION}

# Pick up Alpine security fixes released since the base image was built, and
# remove pip and its bundled wheel: the app never installs anything, and
# vulnerability scanners would otherwise report pip's issues as the image's.
RUN apk upgrade --no-cache \
 && python -m pip uninstall --yes --root-user-action=ignore pip \
 && rm -rf "$(python -c 'import ensurepip, os; print(os.path.dirname(ensurepip.__file__))')"/_bundled

# Runs as an unprivileged user (UID 10001). /data is created owned by that
# user, so a new named volume starts out writable.
COPY --chown=10001:10001 docker/data/ /data/
COPY app/ /app/

USER 10001:10001
WORKDIR /app
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=6s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('GRID_INTERNAL_PORT', '8080'), timeout=4)"]

CMD ["python", "/app/server.py"]
