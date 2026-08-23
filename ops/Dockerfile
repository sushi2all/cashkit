# The service image (SPEC §12).
#
# The engine ships inside it: `cashkit` is a workspace member and the service
# calls it in-process (SPEC §2.1), so there is no second container and no
# network hop between the API and the arithmetic.
#
# Built from the repository root:
#   docker build -f ops/Dockerfile -t cashkit-service .
FROM python:3.13-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# `git` is not a convenience: the revision store is a git repository per book
# (ADR-0018), and `pygit2` needs libgit2's runtime.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

WORKDIR /srv

# Dependencies first, so a code change does not re-resolve the world.
COPY pyproject.toml uv.lock README.md ./
COPY apps/service/pyproject.toml apps/service/README.md apps/service/
COPY cashkit/__init__.py cashkit/__init__.py
RUN uv sync --frozen --all-packages --no-dev --no-install-workspace

COPY cashkit/ cashkit/
COPY apps/service/ apps/service/
RUN uv sync --frozen --all-packages --no-dev

# git refuses to operate in a directory it thinks belongs to someone else, and
# a book directory on a mounted volume is exactly that. The books are the
# service's own data; this is not a repository being borrowed.
RUN git config --system --add safe.directory '*' \
 && git config --system user.email "service@cashkit.local" \
 && git config --system user.name "CashKit"

RUN useradd --system --uid 10001 --home /srv cashkit \
 && mkdir -p /var/lib/cashkit/books /var/log/cashkit \
 && chown -R cashkit:cashkit /var/lib/cashkit /var/log/cashkit /srv
USER cashkit

EXPOSE 8000

# Caddy terminates TLS and is the only thing that talks to this port; the
# service binds the container network and never the host (see compose).
CMD ["uvicorn", "cashkit_service.app:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"]
