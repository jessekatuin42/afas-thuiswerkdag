# Headless AFAS Thuiswerkdag runner.
#
# Why the Playwright base image: every NixOS workaround in this repo exists
# because Playwright's *bundled* Chromium will not launch under the Nix
# dynamic loader (see docs/DEVELOPMENT.md > Environment notes). This image
# ships a working Chromium plus its system libraries, so both shims no-op:
#
#   * src/nixshim.py       -> greenlet imports cleanly, no re-exec
#   * config._detect_chromium -> none of its host paths exist, returns "",
#                                and Playwright uses its own browser
#
# Consequence: AFAS_CHROMIUM must stay UNSET in the container. Setting it
# would point Playwright at a host path that does not exist here.
ARG PLAYWRIGHT_VERSION=1.62.0
FROM mcr.microsoft.com/playwright/python:v${PLAYWRIGHT_VERSION}-noble

# Container images default to UTC. That is precisely the fault that let a
# 13:00 CEST run walk straight through the 11:00-11:59 presence window on
# 2026-08-27: the wall-clock guard is only as trustworthy as the caller's TZ.
# Both scripts/daily-run.sh and dates.today() read local time.
ENV TZ=Europe/Amsterdam \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime && echo "$TZ" > /etc/timezone

WORKDIR /app

# The base image ships only the BROWSERS (/ms-playwright, pinned by its tag) --
# not the Python package. requirements.txt says ">=1.47", so letting pip
# resolve that freely installs a client expecting a different Chromium
# revision than the one baked in, which fails at launch with
# "Executable doesn't exist". Re-declaring the ARG after FROM keeps the image
# tag and the pip pin a single source of truth.
ARG PLAYWRIGHT_VERSION
COPY requirements.txt ./
RUN pip install --no-cache-dir --break-system-packages \
      -r requirements.txt "playwright==${PLAYWRIGHT_VERSION}"

COPY afas_thuiswerk.py ./
COPY src/ ./src/
COPY tools/ ./tools/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY pytest.ini ./
COPY web/ ./web/

# src/config.py hard-codes both paths relative to the project root, so these
# are the mount points. Created here so an unmounted run still works.
RUN mkdir -p /app/.browser-profile /app/artifacts /app/data && chown -R pwuser:pwuser /app

# Chromium's sandbox stays enabled, which means not running as root.
# Run with --userns=keep-id (rootless podman) so bind-mounted host files
# owned by uid 1000 are writable by pwuser, which is also uid 1000.
USER pwuser

ENTRYPOINT ["python", "afas_thuiswerk.py"]
