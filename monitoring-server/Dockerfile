# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Axara AIOps monitoring server image, Community edition.
# Only axara/community is copied in; the image never contains or needs axara/enterprise.
#   COMPILE=0 (default): plain Python source.
#   COMPILE=1: the core packages are compiled to native modules with Nuitka (slower build,
#              no application .py in the image except migrations, monitor templates,
#              manage.py and the package docstring file).
#   docker compose build monitoring-server                                  # source
#   docker compose build --build-arg COMPILE=1 monitoring-server            # compiled

# ---------------------------------------------------------------- runtime base
FROM python:3.14-slim AS runtime
WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Australia/Sydney
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        postgresql-client \
        tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt
ENV HF_HOME=/app/models
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
ENV TRANSFORMERS_OFFLINE=1
ENV HF_HUB_OFFLINE=1

# ---------------------------------------------------------------- compile stage
FROM runtime AS compile
ARG COMPILE=0
ARG NUITKA_VERSION=4.3.5
# Nuitka needs the C library headers (only when compiling; the final image is unchanged).
RUN if [ "$COMPILE" = "1" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends libc6-dev \
        && rm -rf /var/lib/apt/lists/*; \
    fi
WORKDIR /src
COPY axara/community/ axara/community/
# Each core package becomes its own native module under /out/axara/community/ (COMPILE=1),
# or is copied as source (COMPILE=0). `axara` is a namespace package (no axara/__init__.py).
RUN set -e; C=axara/community; mkdir -p /out/$C; \
    CPKGS="api processing incident_management database common vector_store utils llm plugins"; \
    if [ "$COMPILE" = "1" ]; then \
        pip install --no-cache-dir "nuitka==${NUITKA_VERSION}"; \
        for p in $CPKGS; do echo "== compiling axara.community.$p"; \
            python -m nuitka --module "$C/$p" --nofollow-imports \
                --nofollow-import-to=axara.community.database.migrations \
                --include-package=axara.community.$p --output-dir=/out/$C \
                --remove-output --quiet --assume-yes-for-downloads; done; \
        rm -f /out/$C/*.pyi; cp $C/__init__.py /out/$C/__init__.py; \
        mkdir -p /out/$C/database && cp -r $C/database/migrations /out/$C/database/migrations; \
    else \
        echo "COMPILE=0: plain source"; cp -r $C/. /out/$C/; \
    fi

# ---------------------------------------------------------------- final image
FROM runtime
ARG COMPILE=0
COPY --from=compile /out/ /app/
COPY static/ /app/static/
COPY config/ /app/config/
COPY alembic.ini /app/alembic.ini
COPY manage.py /app/manage.py
ENV MONITOR_TEMPLATES_DIR=/app/config/monitor_templates
RUN set -e; mkdir -p /app/logs /app/data/agent-config; \
    find /app -name '__pycache__' -prune -exec rm -rf {} +; \
    if [ "$COMPILE" = "1" ]; then \
        leaked=$(find /app -name '*.py' -not -path '/app/axara/community/database/migrations/*' \
                 -not -path '/app/config/monitor_templates/*' -not -path '/app/models/*' \
                 -not -name manage.py -not -path /app/axara/community/__init__.py | head -5); \
        if [ -n "$leaked" ]; then echo "ERROR: application source in image: $leaked"; exit 1; fi; \
    fi; \
    if [ -d /app/axara/enterprise ]; then echo "ERROR: Enterprise code in a Community image"; exit 1; fi; \
    python -c "import axara.community.plugins.loader, axara.community.database.models"
EXPOSE 9090
CMD ["uvicorn", "axara.community.api.main:app", "--host", "0.0.0.0", "--port", "9090", "--workers", "8", \
     "--ssl-certfile", "/app/certs/server.crt", "--ssl-keyfile", "/app/certs/server.key"]
