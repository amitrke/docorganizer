FROM python:3.12-slim

WORKDIR /app

# Install the package with web extras only (no GPU, no OCR needed on NAS)
COPY pyproject.toml README.md ./
COPY docorg/ ./docorg/

RUN pip install --no-cache-dir -e ".[web]"

# Data directory: mount your Synology share here at runtime
VOLUME ["/data"]

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# The NAS-specific config is expected at /data/config.web.yaml
CMD ["sh", "-c", "docorg web --config /data/config.web.yaml --host 0.0.0.0 --port 8000"]
