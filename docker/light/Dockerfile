# ---- builder: install Python deps via uv (uses .python-version → 3.14) ----
FROM debian:bookworm-slim AS py-builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    UV_TOOL_DIR=/opt/uv/tools \
    UV_CACHE_DIR=/opt/uv/cache

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev && rm -rf /opt/uv/cache/*

# Trim uv-managed Python: remove tcl/tk, idle, ensurepip, docs, headers (~25MB)
RUN find /opt/uv/python -maxdepth 1 -type d -name 'cpython-*' -exec sh -c ' \
    PYDIR="$1"; \
    rm -rf "$PYDIR"/include "$PYDIR"/share \
           "$PYDIR"/lib/tcl* "$PYDIR"/lib/tk* "$PYDIR"/lib/itcl* \
           "$PYDIR"/lib/thread* "$PYDIR"/lib/pkgconfig \
           "$PYDIR"/lib/libtcl* "$PYDIR"/lib/libtk* \
           "$PYDIR"/lib/python3.14/idlelib \
           "$PYDIR"/lib/python3.14/tkinter \
           "$PYDIR"/lib/python3.14/ensurepip \
           "$PYDIR"/lib/python3.14/pydoc_data \
           "$PYDIR"/lib/python3.14/unittest \
           "$PYDIR"/bin/idle* "$PYDIR"/bin/pip* "$PYDIR"/bin/pydoc* \
    ' _ {} \;

# ---- node builder: fetch Node.js binary ----
FROM debian:bookworm-slim AS node-builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates xz-utils && \
    curl -fsSL https://nodejs.org/dist/v22.16.0/node-v22.16.0-linux-x64.tar.xz \
      | tar -xJ --strip-components=1 -C /usr/local && \
    rm -rf /usr/local/share/doc /usr/local/share/man /usr/local/include \
           /usr/local/lib/node_modules/corepack

# ---- runtime: debian slim (no duplicate Python) ----
FROM debian:bookworm-slim

# Minimal runtime deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy Node.js runtime (binary + npm only)
COPY --from=node-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=node-builder /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm

# uv binary
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Pre-create symlinks for npm-based agents (actual modules mounted at runtime)
RUN ln -s ../lib/node_modules/@zed-industries/claude-agent-acp/dist/index.js /usr/bin/claude-agent-acp && \
    ln -s ../lib/node_modules/@openai/codex/bin/codex.js /usr/bin/codex

# Pre-create placeholder files for kiro-cli binaries (prevents Docker mounting as directories)
RUN touch /usr/local/bin/kiro-cli /usr/local/bin/kiro-cli-chat /usr/local/bin/kiro-cli-term && \
    chmod +x /usr/local/bin/kiro-cli /usr/local/bin/kiro-cli-chat /usr/local/bin/kiro-cli-term

RUN useradd -m -d /home/app app

# uv-managed Python (trimmed) + venv — use --chown to avoid extra layer
COPY --from=py-builder --chown=app:app /opt/uv/python /opt/uv/python
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    UV_TOOL_DIR=/opt/uv/tools \
    UV_CACHE_DIR=/opt/uv/cache

WORKDIR /app
COPY --chown=app:app pyproject.toml uv.lock .python-version ./
COPY --chown=app:app main.py VERSION ./
COPY --chown=app:app src/ src/
COPY --chown=app:app skill/ skill/
COPY --chown=app:app tools/ tools/
COPY --chown=app:app examples/ examples/
COPY --from=py-builder --chown=app:app /app/.venv /app/.venv

USER app

EXPOSE 8010

CMD ["uv", "run", "main.py"]
