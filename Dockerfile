FROM node:20-slim AS frontend-build

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim

WORKDIR /app

# Web UI + upload + inbox watcher are all core deps now (no GPU, no OCR needed on NAS)
COPY pyproject.toml README.md ./
COPY docorg/ ./docorg/
COPY --from=frontend-build /frontend/dist ./docorg/static

RUN pip install --no-cache-dir -e .

# Data directory: mount your Synology share here at runtime
VOLUME ["/data"]

ENV PYTHONUNBUFFERED=1
# The NAS-specific config is expected at /data/config.web.yaml
ENV DOCORG_CONFIG=/data/config.web.yaml
ENV DOCORG_HOST=0.0.0.0
ENV DOCORG_PORT=8000

EXPOSE 8000

CMD ["docorg"]
