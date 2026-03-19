FROM python:3.12-slim

# Install system deps + Node.js
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install CLI agents
RUN npm i -g @openai/codex @anthropic-ai/claude-code

# Install kiro-cli (best-effort)
RUN npm i -g @anthropic-ai/kiro-cli 2>/dev/null || true

# Install uv
RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock .python-version ./

RUN uv sync --frozen --no-dev

# Copy project files
COPY main.py VERSION ./
COPY src/ src/
COPY skill/ skill/
COPY tools/ tools/
COPY examples/ examples/

EXPOSE 8001

CMD ["uv", "run", "main.py"]
