FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data PORT=8080

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install .

# Non-root; /data holds gateway.sqlite3 (sealed Bosch sessions + OAuth state)
RUN useradd -r -u 10001 ebike && mkdir -p /data && chown ebike /data && chmod 700 /data
USER ebike
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/healthz'%os.environ.get('PORT','8080'),timeout=2)"

ENTRYPOINT ["bosch-ebike-connect-classic"]
