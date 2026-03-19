FROM python:3.12-slim

# Node.js for npm-based agents (claude-agent-acp, codex)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# Pre-create symlinks for npm-based agents (actual modules mounted at runtime)
RUN ln -s ../lib/node_modules/@zed-industries/claude-agent-acp/dist/index.js /usr/bin/claude-agent-acp && \
    ln -s ../lib/node_modules/@openai/codex/bin/codex.js /usr/bin/codex

RUN useradd -m -d /home/app app

# Keep ~/.local clean for user mounts (kiro auth, codex config, etc.)
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python
ENV UV_TOOL_DIR=/opt/uv/tools
ENV UV_CACHE_DIR=/opt/uv/cache
RUN mkdir -p /opt/uv && chown -R app:app /opt/uv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY main.py VERSION ./
COPY src/ src/
COPY skill/ skill/
COPY tools/ tools/
COPY examples/ examples/

RUN chown -R app:app /app
USER app

RUN uv sync --frozen --no-dev

EXPOSE 8001

CMD ["uv", "run", "main.py"]
