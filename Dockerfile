# syntax=docker/dockerfile:1.7
ARG BUILDPLATFORM
ARG TARGETPLATFORM
ARG TARGETARCH

FROM node:22-alpine AS web-build

# Override at build time when a remote server needs an internal/npm mirror:
# docker compose build --build-arg NPM_REGISTRY=https://registry.npmmirror.com
ARG NPM_REGISTRY=https://registry.npmjs.org/
WORKDIR /app/web

# package-lock.json is npm's authoritative lockfile.  bun.lock must not be
# paired with npm install: npm ignores it and resolves dependencies again.
COPY web/package.json web/package-lock.json ./
RUN --mount=type=cache,id=npm-cache-${BUILDPLATFORM},target=/root/.npm \
    npm ci --prefer-offline --no-audit --no-fund --registry="$NPM_REGISTRY"

COPY VERSION /app/VERSION
COPY CHANGELOG.md /app/CHANGELOG.md
COPY web ./
RUN NEXT_PUBLIC_APP_VERSION="$(cat /app/VERSION)" npm run build


FROM python:3.13-slim AS app

ARG TARGETPLATFORM
ARG TARGETARCH

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# 安装系统依赖
# - git: Git 存储后端需要
# - libpq-dev: PostgreSQL 客户端库
# - gcc: 编译 psycopg2-binary 需要
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libpq-dev \
    gcc \
    openssl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY main.py ./
# config.json is a local secret mounted by docker-compose.local.yml; it must
# never be copied into the image or Git repository.
COPY VERSION ./
COPY api ./api
COPY services ./services
COPY utils ./utils
COPY scripts ./scripts
COPY --from=web-build /app/web/out ./web_dist

EXPOSE 80

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80", "--access-log"]
