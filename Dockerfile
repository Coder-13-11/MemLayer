# Hosted MemLayer service. The platform in front of it must terminate HTTPS.
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MEMLAYER_DATA_DIR=/data PORT=8000
WORKDIR /app
COPY pyproject.toml README.md ./
COPY memlayer ./memlayer
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 memlayer && mkdir -p /data && chown memlayer /data
USER memlayer
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz')"
# MEMLAYER_PUBLIC_URL must be set to the https:// address users connect to.
CMD ["memlayer-hosted"]
