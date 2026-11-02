FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml ./
COPY app ./app
RUN python -m venv /opt/venv && /opt/venv/bin/pip install .

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
RUN groupadd --system --gid 10001 kumbh && useradd --system --uid 10001 --gid kumbh --no-create-home kumbh
WORKDIR /srv
COPY --from=build /opt/venv /opt/venv
COPY --chown=root:root alembic.ini ./
COPY --chown=root:root alembic ./alembic
COPY --chown=root:root app ./app

USER kumbh
EXPOSE 8000
# Client addresses come from TRUSTED_PROXIES in the app; uvicorn must not rewrite them from untrusted headers.
# The uvicorn access log is off because it records query strings (which can contain searched names).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-access-log", "--no-server-header", "--timeout-graceful-shutdown", "20"]
