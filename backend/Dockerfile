FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml ./
COPY app ./app
RUN python -m venv /opt/venv && /opt/venv/bin/pip install .
# Download pinned YuNet + SFace ONNX models into the image.
ENV FACE_MODEL_DIR=/opt/face-models
RUN /opt/venv/bin/python -c "from pathlib import Path; from app.faces.models_fetch import fetch_models; fetch_models(Path('/opt/face-models'))"

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH" FACE_MODEL_DIR=/opt/face-models
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 libgl1 \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 10001 kumbh && useradd --system --uid 10001 --gid kumbh --no-create-home kumbh
WORKDIR /srv
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/face-models /opt/face-models
COPY --chown=root:root alembic.ini ./
COPY --chown=root:root alembic ./alembic
COPY --chown=root:root app ./app

USER kumbh
EXPOSE 8000
# Client addresses come from TRUSTED_PROXIES in the app; uvicorn must not rewrite them from untrusted headers.
# The uvicorn access log is off because it records query strings (which can contain searched names).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-access-log", "--no-server-header", "--timeout-graceful-shutdown", "20"]
