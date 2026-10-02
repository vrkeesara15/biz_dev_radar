# BidRadar backend image (SPEC 10.1 / 12). ONE image for api, worker, beat and every
# per-adapter Cloud Run job; `docker/entrypoint.sh` picks the mode (argument, else
# $BIDRADAR_MODE), REGION picks the residency bucket and DB.
#
# The build CONTEXT is backend/, not the repo root — every COPY below is relative to it:
#
#   docker build -t bidradar-backend backend
#
# (`docker build -f backend/Dockerfile .` fails on `COPY docker ./docker`. On Railway
# that means `railway up` is run from backend/, which is also where railway.json lives.)
#
# Stages: builder resolves the uv lockfile into /opt/venv, runtime carries only the venv
# plus the parsing/scanning binaries the app shells out to (SPEC 10.1 Parsing row).

# ---------------------------------------------------------------- builder
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

RUN \
    pip install --no-cache-dir "uv==0.11.14"

WORKDIR /app

# Dependency layer: unchanged application code must not re-resolve the lockfile.
COPY pyproject.toml uv.lock README.md ./
RUN \
    uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
RUN \
    uv sync --frozen --no-dev

# ---------------------------------------------------------------- runtime
FROM python:3.12-slim-bookworm AS runtime

# Tesseract eng+hin for scanned pages (SPEC 12 India checklist), ClamAV for the upload
# scan (SPEC 11), libmagic for content sniffing, poppler for pdftoppm/pdftotext.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      libmagic1 \
      poppler-utils \
      tesseract-ocr \
      tesseract-ocr-eng \
      tesseract-ocr-hin \
      clamav-daemon \
      clamav-freshclam \
 && rm -rf /var/lib/apt/lists/*

# Non-root (SPEC 11). uid 10001 also owns the ClamAV state dirs so clamd can run as it.
RUN groupadd --gid 10001 bidradar \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin bidradar \
 && mkdir -p /var/lib/clamav /var/log/clamav /run/clamav \
 && chown -R 10001:10001 /var/lib/clamav /var/log/clamav /run/clamav /etc/clamav

# clamd listens on TCP so app/services/scanner.py can reach it with CLAMAV_HOST/PORT.
RUN printf '%s\n' \
      'LogSyslog no' \
      'LogFile /dev/stdout' \
      'DatabaseDirectory /var/lib/clamav' \
      'TCPSocket 3310' \
      'TCPAddr 127.0.0.1' \
      'MaxFileSize 100M' \
      'MaxScanSize 200M' \
      'StreamMaxLength 100M' \
      'Foreground yes' \
    > /etc/clamav/clamd.conf \
 && printf '%s\n' \
      'DatabaseDirectory /var/lib/clamav' \
      'UpdateLogFile /dev/stdout' \
      'DatabaseMirror database.clamav.net' \
    > /etc/clamav/freshclam.conf \
 && chown 10001:10001 /etc/clamav/clamd.conf /etc/clamav/freshclam.conf

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8080 \
    APP_ENV=production \
    STORAGE_BACKEND=gcs \
    SCANNER_BACKEND=clamav \
    OCR_BACKEND=tesseract \
    OCR_LANGUAGES=eng+hin \
    TRUST_PROXY_HEADERS=true

COPY --from=builder --chown=10001:10001 /opt/venv /opt/venv

WORKDIR /app
COPY --chown=10001:10001 app ./app
COPY --chown=10001:10001 migrations ./migrations
COPY --chown=10001:10001 alembic.ini README.md ./
COPY --chown=10001:10001 docker ./docker
RUN chmod +x /app/docker/entrypoint.sh /app/docker/clamd-start.sh

USER 10001:10001
EXPOSE 8080

# Cloud Run runs its own startupProbe/livenessProbe (infra/cloudrun/services/*.yaml);
# this is for `docker run` and any plain container runtime.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1

# No default argument: the mode comes from $1 when a platform passes one (Cloud Run's
# args) and otherwise from $BIDRADAR_MODE, which is how Railway's api/worker/beat
# services share one image, one railway.json and no startCommand. `CMD ["api"]` would
# pin $1 and make that variable dead.
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD []
