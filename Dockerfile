FROM python:3.12-slim

# System dependencies for sandboxing and git operations
RUN apt-get update && apt-get install -y --no-install-recommends \
    bubblewrap \
    git \
    curl \
    ca-certificates \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22.x (required by Claude Code CLI)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# ruff (used by orchestrator for auto-lint in worktrees)
RUN pip install --no-cache-dir ruff

# semgrep (used by the canonicalizer agent for rule-based convention enforcement)
RUN pip install --no-cache-dir semgrep

# gh CLI (used by orchestrator for PR creation)
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Install jig
COPY . /opt/jig
RUN pip install --no-cache-dir /opt/jig

# Non-root user — Claude Code refuses bypassPermissions as root
RUN useradd -m -s /bin/bash jig

# Pre-create the bwrap mount point for agent worktrees
RUN mkdir /workspace && chown jig:jig /workspace

USER jig

ENV CLAUDE_CODE_PERMISSION_MODE=bypassPermissions
# Writable config dir for the bundled claude CLI (the ro-mounted ~/.claude
# can't be written to; the CLI needs a writable location for logs/state).
ENV CLAUDE_CONFIG_DIR=/tmp/jig-claude-config
# Host gitconfig may enable GPG signing — not available in the container.
# GIT_CONFIG_COUNT/KEY/VALUE override without needing a writable gitconfig.
ENV GIT_CONFIG_COUNT=1
ENV GIT_CONFIG_KEY_0=commit.gpgsign
ENV GIT_CONFIG_VALUE_0=false
ENV JIG_IN_CONTAINER=1
WORKDIR /project
ENTRYPOINT ["jig"]
