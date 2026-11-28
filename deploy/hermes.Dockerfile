# Source is supplied by build_runtime_image.py from the exact project pin, without .env/.git.
FROM python:3.14-slim@sha256:22eafb370920ad8b429e0aec3fff433e0669968519f5d60469d83729c138b28b
RUN apt-get update && apt-get install -y --no-install-recommends git gcc libffi-dev && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.11.21
COPY hermes /opt/hermes
RUN uv sync --project /opt/hermes --frozen --no-dev --extra messaging --extra mcp
ENV PATH=/opt/hermes/.venv/bin:$PATH HOME=/home/hermes HERMES_HOME=/home/hermes HERMES_RUNTIME_DIR=/home/hermes/managed PYTHONDONTWRITEBYTECODE=1
RUN mkdir -p /home/hermes && chown 65532:65532 /home/hermes
WORKDIR /home/hermes
USER 65532:65532
CMD ["python", "/opt/hermes/hermes", "gateway", "run"]
