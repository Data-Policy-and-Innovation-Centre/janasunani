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
#
# Base images are pinned to digests, not just floating tags: an image
# built from a re-pulled `python:3.13-slim` (or uv) underneath an unchanged
# api.Dockerfile is exactly the kind of drift the app images' own IMAGE_TAG
# pinning (deploy/docker-compose.yml) exists to prevent. Bump by
# re-resolving: `docker buildx imagetools inspect python:3.13-slim` /
# `... ghcr.io/astral-sh/uv:0.9`.
FROM python:3.13-slim@sha256:eb43ff125d8d58d7449dcba7d336c23bcac412f526d861db493b9994d8010280 AS build
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9@sha256:538e0b39736e7feae937a65983e49d2ab75e1559d35041f9878b7b7e51de91e4 /uv /uvx /bin/
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
RUN mkdir -p -m 0700 /root/.ssh && ssh-keyscan github.com >> /root/.ssh/known_hosts
COPY pyproject.toml uv.lock ./
# Dependency-only sync first (no project code yet) so this layer caches across
# source-only changes; the registry cache (build-push-action cache-from/to
# type=registry) keeps this fast on a cold CI runner.
#
# `demo` resolves torch from PyTorch's CPU-only index (see [tool.uv.sources] in
# pyproject.toml) — this box has no GPU. That drops ~2.5 GB of wheels versus the
# default CUDA build: the nvidia-*/cuda-toolkit/triton payload entirely, plus
# the torch wheel itself going 532 MB -> 192 MB.
RUN --mount=type=ssh uv sync --locked --extra demo --no-dev --no-install-project
COPY README.md alembic.ini ./
COPY janasunani ./janasunani
RUN --mount=type=ssh uv sync --locked --extra demo --no-dev

FROM python:3.13-slim@sha256:eb43ff125d8d58d7449dcba7d336c23bcac412f526d861db493b9994d8010280
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ori poppler-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /app /app
COPY deploy/api-entrypoint.sh /usr/local/bin/api-entrypoint.sh
ENV PATH="/app/.venv/bin:$PATH" JANASUNANI_API_HOST=0.0.0.0 HF_HOME=/hf-cache
EXPOSE 8000
ENTRYPOINT ["api-entrypoint.sh"]
