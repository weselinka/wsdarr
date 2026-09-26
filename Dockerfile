FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CONFIG_DIR=/config \
    DOWNLOAD_DIR=/downloads \
    PUID=1000 \
    PGID=1000 \
    UMASK=002

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . && rm -rf /root/.cache

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 9797
VOLUME ["/config", "/downloads"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9797/health', timeout=4)" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["wsdarr", "serve"]
