# NEXUS: the site, the app, the REST API, the gateway and the MCP endpoint in one process.
# The platform in front of it must terminate HTTPS.
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 NEXUS_DATA_DIR=/data PORT=8000
WORKDIR /app
# Dependencies first, at the exact versions the test suite ran against, so the layer caches and builds repeat.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml README.md ./
COPY nexus ./nexus
COPY memlayer ./memlayer
RUN pip install --no-cache-dir --no-deps . \
    && useradd --create-home --uid 10001 nexus && mkdir -p /data && chown nexus /data
USER nexus
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/readyz')"
# NEXUS_PUBLIC_URL must be set to the https:// address people connect to. See .env.example.
CMD ["nexus-hosted"]
