# syntax=docker/dockerfile:1
#
# Production image for `janasunani-api-live` (janasunani/inference/serve.py),
# the real-inference demo API. Build context is the REPO ROOT (see
# .github/workflows/deploy.yml and the root .dockerignore, which excludes the
# multi-GB DVC data/models — those are host bind-mounted at runtime, never
# baked in; see deploy/docker-compose.yml).
#
# Two-stage build: `build` resolves the `demo` extra with `uv sync` (needs SSH
# for the private `dpic` git dependency — BuildKit `ssh: default`, backed in
# CI by the existing `DPIC_GITHUB_SSH_KEY` secret via webfactory/ssh-agent);
# the runtime stage copies the resulting /app (source + .venv) and adds only
# the OS packages the pipeline needs at runtime (tesseract + poppler).
#
# Editable install + WORKDIR /app in both stages is load-bearing: janasunani/
# config.py derives ROOT_DIR/MODELS_DIR/data paths from `__file__`, and
# alembic.ini uses a relative script_location — the source tree must live at
# the same absolute path it was installed from.
FROM python:3.13-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
RUN mkdir -p -m 0700 /root/.ssh && ssh-keyscan github.com >> /root/.ssh/known_hosts
COPY pyproject.toml uv.lock ./
# Dependency-only sync first (no project code yet) so this layer caches across
# source-only changes; the registry cache (build-push-action cache-from/to
# type=registry) makes this fast even on a cold CI runner given the ~8-12 GB
# CUDA-torch resolve for `demo`.
RUN --mount=type=ssh uv sync --locked --extra demo --no-dev --no-install-project
COPY README.md alembic.ini ./
COPY janasunani ./janasunani
RUN --mount=type=ssh uv sync --locked --extra demo --no-dev

FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ori poppler-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /app /app
COPY deploy/api-entrypoint.sh /usr/local/bin/api-entrypoint.sh
ENV PATH="/app/.venv/bin:$PATH" JANASUNANI_API_HOST=0.0.0.0 HF_HOME=/hf-cache
EXPOSE 8000
ENTRYPOINT ["api-entrypoint.sh"]
