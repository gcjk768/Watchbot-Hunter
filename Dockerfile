FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DISABLE_AUTOUPDATER=1

# curl, ca-certificates and git are for the Claude Code installer
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md config.yaml rules.yaml ./
COPY watchbot ./watchbot
COPY prompts ./prompts
COPY tests ./tests
RUN pip install -e ".[dev]"

# UID matches the NAS user that owns ./data and the vault, so files stay editable by James
ARG UID=1000
RUN useradd -m -u ${UID} app && mkdir -p /app/data && chown -R app /app
USER app
RUN curl -fsSL https://claude.ai/install.sh | bash -s stable
ENV PATH=/home/app/.local/bin:$PATH
RUN claude --version

HEALTHCHECK --interval=2m --timeout=20s --start-period=2m CMD ["watchbot", "health"]
CMD ["watchbot", "serve"]
