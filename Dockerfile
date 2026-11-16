FROM python:3.13-slim

# If files.pythonhosted.org is unreachable, build with:
#   docker build --build-arg PIP_INDEX_URL=https://<your-pypi-mirror>/simple .
ARG PIP_INDEX_URL
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DASTYAR_HOME=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# The agent runs commands as this unprivileged user, inside the container only.
RUN useradd -m -u 1000 dastyar && mkdir -p /data && chown dastyar:dastyar /data
USER dastyar
VOLUME /data
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8765/api/auth/status', timeout=3)"

# Non-loopback bind: the server refuses to start unless DASTYAR_TOKEN is set.
CMD ["dastyar", "serve", "--host", "0.0.0.0"]
