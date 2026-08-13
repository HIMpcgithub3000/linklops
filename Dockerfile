# LinkOps: one image, two commands.
#
# The API and the analytics worker ship in the same image and differ only by the
# command they are started with. That is the Module 01 project-layout decision
# from System Design carried through to packaging: two entrypoints over one
# dependency tree, so the two processes cannot drift onto different versions of
# the code that defines tenant scoping.
#
# Base is Debian slim, not Alpine, and the reason is measurable rather than
# aesthetic: five dependencies here ship compiled binary wheels -- psycopg-binary,
# uvloop, httptools, watchfiles and pydantic-core. musl needs its own wheels for
# each, and where one is missing pip builds from source. pydantic-core is Rust,
# so that failure is not "apt install gcc", it is "ship a Rust toolchain".
#
# Python is pinned to the same minor version used in development. The lesson
# this module opens with is a service that broke because production ran 3.8 and
# the laptop ran 3.11; choosing a different minor here would reintroduce exactly
# that gap while claiming to have closed it.

# ---- Stage 1: builder -------------------------------------------------------
FROM python:3.14-slim AS builder

WORKDIR /app

# Dependencies before source. Docker caches layers by instruction, so putting
# the rarely-changing requirements above the frequently-changing code means an
# edit to a router does not reinstall the dependency tree.
COPY api/requirements.txt ./

# A venv rather than the system site-packages, purely so the runtime stage has
# one directory to copy instead of having to know where pip scattered things.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir -r requirements.txt

# Strip the installer and the byte-cache out of the venv that ships: 118MB ->
# 79MB. Honest scope, because I checked instead of assuming -- this removes pip
# from /opt/venv, and python:3.14-slim still ships its own pip at
# /usr/local/bin/pip, so `pip` remains on PATH in the final image. The saving is
# real; the "no installer in production" property is not achieved by this line
# alone and would need the base image's copy removed too.
RUN pip uninstall -y pip setuptools wheel 2>/dev/null || true; \
    find /opt/venv -name '__pycache__' -type d -prune -exec rm -rf {} + ; \
    find /opt/venv -name '*.dist-info' -type d -exec rm -rf {}/RECORD \; 2>/dev/null || true

# ---- Stage 2: runtime -------------------------------------------------------
# Starts clean. Nothing from the builder exists here except what is copied
# explicitly, which is the entire point: pip's cache, the build toolchain and
# anything apt pulled in during install are all left behind.
FROM python:3.14-slim

# A system user with no home and no login shell. Defence in depth -- container
# isolation has been escaped before, and the difference between landing on the
# host as root and landing as an unprivileged account is the difference between
# an incident and a footnote.
RUN groupadd -r appgroup && useradd -r -g appgroup -s /bin/false appuser

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# PYTHONUNBUFFERED because the logging built in Debugging Module 02 writes JSON
# to stdout, and Python buffers stdout when it is not a terminal. Without this a
# container that dies takes its last several seconds of logs with it -- exactly
# the window that explains why it died.

COPY api/app ./app
COPY api/alembic ./alembic
COPY api/alembic.ini ./alembic.ini
COPY api/scripts ./scripts
COPY worker ./worker

# The configuration contract, not a secret: app/config.py validates every
# supplied key against this file and refuses to start if they disagree. Real
# values arrive as environment variables at run time.
COPY api/.env.example ./.env.example

USER appuser

EXPOSE 8000

# Liveness only. The module calls it /live; this service calls it /health, and
# the distinction it draws is the same one -- /health is dependency-free so a
# database blip cannot restart every container at once, while /ready checks
# Postgres and is what the load balancer should poll. Docker's HEALTHCHECK
# restarts things, so it gets the liveness endpoint.
#
# urllib rather than curl: Debian slim ships no curl, and adding one would put a
# network client in the runtime image to answer a question Python can already
# answer.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request;urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT','8000')}/health\",timeout=2)"]

# exec form through sh so ${PORT} is expanded, and `exec` so uvicorn becomes
# PID 1 rather than a child of the shell. That matters here specifically:
# app/main.py installs a SIGTERM handler that flips /ready false before uvicorn
# begins shutting down, and a shell parent would swallow the signal and leave
# the load balancer routing into a closing server.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
