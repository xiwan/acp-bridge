#!/usr/bin/env bash
# ACP Bridge — one-line installer
# Usage: curl -fsSL https://raw.githubusercontent.com/xiwan/acp-bridge/main/install.sh | bash
set -euo pipefail

REPO="https://github.com/xiwan/acp-bridge.git"
INSTALL_DIR="${ACP_BRIDGE_DIR:-$HOME/acp-bridge}"

info()  { printf "\033[1;34m▸\033[0m %s\n" "$*"; }
ok()    { printf "\033[1;32m✔\033[0m %s\n" "$*"; }
warn()  { printf "\033[1;33m⚠\033[0m %s\n" "$*"; }
fail()  { printf "\033[1;31m✘\033[0m %s\n" "$*"; exit 1; }

# --- Check Python ---
info "Checking Python..."
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 12 ]; then
        ok "Python $PY_VER"
    else
        fail "Python >= 3.12 required (found $PY_VER)"
    fi
else
    fail "Python 3 not found. Install Python >= 3.12 first."
fi

# --- Check/install uv ---
info "Checking uv..."
if command -v uv &>/dev/null; then
    ok "uv $(uv --version 2>/dev/null | head -1)"
else
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    if command -v uv &>/dev/null; then
        ok "uv installed"
    else
        fail "uv installation failed"
    fi
fi

# --- Clone/update repo ---
if [ -d "$INSTALL_DIR/.git" ]; then
    info "Updating existing installation at $INSTALL_DIR..."
    git -C "$INSTALL_DIR" pull --ff-only 2>/dev/null || warn "git pull failed, using existing version"
else
    info "Cloning acp-bridge to $INSTALL_DIR..."
    git clone --depth 1 "$REPO" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# --- Install dependencies ---
info "Installing Python dependencies..."
uv sync --frozen 2>/dev/null || uv sync
ok "Dependencies installed"

# --- Detect agents ---
info "Scanning for agent CLIs..."
AGENTS=()
for cmd in kiro-cli claude-agent-acp codex qwen opencode harness-factory; do
    if command -v "$cmd" &>/dev/null; then
        AGENTS+=("$cmd")
        ok "  $cmd"
    fi
done

if [ ${#AGENTS[@]} -eq 0 ]; then
    warn "No agent CLIs found in PATH."
    warn "Install at least one (kiro-cli, claude-agent-acp, codex, etc.) then run:"
    echo "    cd $INSTALL_DIR && uv run main.py"
    exit 0
fi

# --- Done ---
echo ""
ok "ACP Bridge installed at $INSTALL_DIR"
ok "Detected ${#AGENTS[@]} agent(s): ${AGENTS[*]}"
echo ""
echo "  Start (zero-config):"
echo "    cd $INSTALL_DIR && uv run main.py"
echo ""
echo "  Start (with config):"
echo "    cd $INSTALL_DIR && cp config.yaml.example config.yaml"
echo "    # edit config.yaml"
echo "    uv run main.py"
echo ""
