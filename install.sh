#!/usr/bin/env bash
# ACP Bridge — interactive installer
# Usage: curl -fsSL https://raw.githubusercontent.com/xiwan/acp-bridge/main/install.sh | bash
set -euo pipefail

REPO="https://github.com/xiwan/acp-bridge.git"
HARNESS_REPO="https://github.com/xiwan/harness-factory.git"
INSTALL_DIR="${ACP_BRIDGE_DIR:-$HOME/acp-bridge}"

info()  { printf "\033[1;34m▸\033[0m %s\n" "$*"; }
ok()    { printf "\033[1;32m✔\033[0m %s\n" "$*"; }
warn()  { printf "\033[1;33m⚠\033[0m %s\n" "$*"; }
fail()  { printf "\033[1;31m✘\033[0m %s\n" "$*"; exit 1; }
ask()   { printf "\033[1;36m?\033[0m %s " "$*"; }

# Read a line — works in all modes:
#   - interactive (bash install.sh): read from stdin
#   - curl|bash: read from /dev/tty (stdin is the script itself)
read_input() {
    local var_name="$1" default="$2"
    if [ -t 0 ]; then
        read -r "$var_name" || true
    else
        read -r "$var_name" </dev/tty 2>/dev/null || eval "$var_name='$default'"
    fi
    eval "[ -z \"\$$var_name\" ] && $var_name='$default'" || true
}

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║     _   ___ ___   ___      _    _                            ║"
echo "║    /_\ / __| _ \ | _ )_ __(_)__| |__ _  ___                  ║"
echo "║   / _ \ (__| _/  | _ \ '_|| / _\` / _\` |/ -_)                 ║"
echo "║  /_/ \_\___|_|   |___/|_| |_\__,_\__, \___|                  ║"
echo "║                                   |___/                      ║"
echo "║                                                              ║"
echo "║              Interactive Installer                           ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ============================================================
# Step 0: State detection (first run vs update)
# ============================================================
info "Step 0: Detecting current state..."

BRIDGE_RUNNING=false
CONFIG_EXISTS=false
IS_UPDATE=false
EXISTING_AGENTS=()

# Check if Bridge is already running
if curl -s --max-time 2 http://127.0.0.1:18010/health &>/dev/null; then
    BRIDGE_RUNNING=true
    ok "ACP Bridge is running"
fi

# Check if config.yaml exists (indicates previous install)
if [ -f "$INSTALL_DIR/config.yaml" ]; then
    CONFIG_EXISTS=true
    IS_UPDATE=true
    ok "Existing installation found at $INSTALL_DIR"
    # Parse which agents are already configured
    for name in kiro claude codex trae qwen opencode hermes harness; do
        if grep -q "^  ${name}:" "$INSTALL_DIR/config.yaml" 2>/dev/null; then
            EXISTING_AGENTS+=("$name")
        fi
    done
    if [ ${#EXISTING_AGENTS[@]} -gt 0 ]; then
        ok "Configured agents: ${EXISTING_AGENTS[*]}"
    fi
elif [ -d "$INSTALL_DIR/.git" ] || [ -f "$INSTALL_DIR/main.py" ]; then
    IS_UPDATE=true
    ok "ACP Bridge code found (no config.yaml)"
fi

if $IS_UPDATE; then
    info "Running in UPDATE mode — will preserve existing config"
else
    info "Running in FRESH INSTALL mode"
fi
echo ""

# ============================================================
# Step 1: Prerequisites
# ============================================================
info "Step 1/6: Checking prerequisites..."

# Python — uv manages its own Python, so just check uv can provide >= 3.12
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    ok "System Python $PY_VER (uv will manage its own if needed)"
else
    info "No system Python found — uv will install one automatically"
fi

# uv
if command -v uv &>/dev/null; then
    ok "uv $(uv --version 2>/dev/null | head -1)"
else
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh
    sh /tmp/uv-install.sh
    rm -f /tmp/uv-install.sh
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv &>/dev/null || fail "uv installation failed"
    ok "uv installed"
fi

# git
if command -v git &>/dev/null; then
    ok "git $(git --version 2>/dev/null | awk '{print $3}')"
else
    info "Installing git..."
    if command -v yum &>/dev/null; then
        sudo yum install -y git &>/dev/null
    elif command -v apt-get &>/dev/null; then
        sudo apt-get install -y git &>/dev/null
    fi
    command -v git &>/dev/null && ok "git installed" || warn "Could not install git — will use tarball fallback"
fi

HAS_GIT=false
command -v git &>/dev/null && HAS_GIT=true

echo ""

# ============================================================
# Step 2: Clone / update
# ============================================================
info "Step 2/6: Installing ACP Bridge..."

if [ -d "$INSTALL_DIR/.git" ] && $HAS_GIT; then
    info "Updating existing installation at $INSTALL_DIR..."
    git -C "$INSTALL_DIR" pull --ff-only 2>/dev/null || warn "git pull failed, using existing version"
elif [ -d "$INSTALL_DIR" ]; then
    # Existing dir without .git (tarball install) — update via tarball overlay
    info "Updating existing installation at $INSTALL_DIR (tarball)..."
    curl -fsSL https://github.com/xiwan/acp-bridge/archive/refs/heads/main.tar.gz \
        | tar -xz --strip-components=1 -C "$INSTALL_DIR"
elif $HAS_GIT; then
    git clone --depth 1 "$REPO" "$INSTALL_DIR"
else
    info "git not found, downloading tarball..."
    mkdir -p "$INSTALL_DIR"
    curl -fsSL https://github.com/xiwan/acp-bridge/archive/refs/heads/main.tar.gz \
        | tar -xz --strip-components=1 -C "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"
uv sync --frozen 2>/dev/null || uv sync
ok "ACP Bridge installed at $INSTALL_DIR"
echo ""

# ============================================================
# Harness-factory installer (binary download → Go build → fail)
# ============================================================
HARNESS_RELEASE_URL="https://github.com/xiwan/harness-factory/releases/latest/download"

_install_harness() {
    info "Installing harness-factory..."
    mkdir -p "$HOME/.local/bin"
    local BIN_URL="${HARNESS_RELEASE_URL}/harness-factory"

    # Try 1: download pre-built binary from GitHub Release
    if curl -fsSL -o "$HOME/.local/bin/harness-factory" "$BIN_URL" 2>/dev/null; then
        chmod +x "$HOME/.local/bin/harness-factory"
        export PATH="$HOME/.local/bin:$PATH"
        ok "harness-factory downloaded (pre-built binary)"
        ENABLED+=("harness")
        return
    fi

    # Try 2: build from source if Go is available
    if command -v go &>/dev/null; then
        info "No pre-built binary, building from source..."
        local HARNESS_DIR="${INSTALL_DIR}/../harness-factory"
        if [ -d "$HARNESS_DIR/.git" ] && $HAS_GIT; then
            git -C "$HARNESS_DIR" pull --ff-only 2>/dev/null || true
        elif $HAS_GIT; then
            git clone --depth 1 "$HARNESS_REPO" "$HARNESS_DIR"
        else
            mkdir -p "$HARNESS_DIR"
            curl -fsSL https://github.com/xiwan/harness-factory/archive/refs/heads/master.tar.gz \
                | tar -xz --strip-components=1 -C "$HARNESS_DIR"
        fi
        (cd "$HARNESS_DIR" && make build 2>/dev/null || go build -ldflags="-s -w" -o harness-factory ./cmd/harness-factory/)
        cp "$HARNESS_DIR/harness-factory" "$HOME/.local/bin/"
        chmod +x "$HOME/.local/bin/harness-factory"
        export PATH="$HOME/.local/bin:$PATH"
        ok "harness-factory built from source"
        ENABLED+=("harness")
        return
    fi

    # Neither worked
    warn "Could not install harness-factory."
    warn "Options: publish a GitHub Release, or install Go (https://go.dev/dl/)"
    echo ""
    echo "  After installing an agent, just run:"
    echo "    cd $INSTALL_DIR && uv run main.py"
    exit 0
}

# ============================================================
# Agent install reference (shown to user)
# ============================================================
declare -A AGENT_INSTALL_HINTS=(
    [kiro]="curl -fsSL https://cli.kiro.dev/install | bash && kiro-cli login"
    [claude]="npm i -g @anthropic-ai/claude-code @agentclientprotocol/claude-agent-acp"
    [codex]="npm i -g @openai/codex"
    [qwen]="npm i -g @anthropic-ai/qwen-code"
    [opencode]="See https://github.com/opencode-ai/opencode"
    [hermes]="uv tool install 'hermes-agent[acp] @ git+https://github.com/NousResearch/hermes-agent.git'  (or: pip install 'hermes-agent[acp] @ git+https://github.com/NousResearch/hermes-agent.git')"
    [trae]="cd trae-agent && uv sync --all-extras"
    [harness]="(auto-installed by this script)"
)

# ============================================================
# Step 3: Agent setup
# ============================================================
info "Step 3/6: Agent setup"
echo ""

# Detect what's available
declare -A AGENT_CMDS=(
    [kiro]="kiro-cli"
    [claude]="claude-agent-acp"
    [codex]="codex"
    [trae]="trae-cli"
    [qwen]="qwen"
    [opencode]="opencode"
    [hermes]="hermes"
    [harness]="harness-factory"
)
declare -A AGENT_DESCS=(
    [kiro]="Kiro CLI (AWS)"
    [claude]="Claude Code (Anthropic)"
    [codex]="Codex CLI (OpenAI)"
    [trae]="Trae Agent (ByteDance)"
    [qwen]="Qwen Code (Alibaba)"
    [opencode]="OpenCode (open source)"
    [hermes]="Hermes Agent (Nous Research)"
    [harness]="Harness Factory (lightweight, profile-driven)"
)
AGENT_ORDER=(kiro claude codex trae qwen opencode hermes harness)

FOUND=()
NOT_FOUND=()
for name in "${AGENT_ORDER[@]}"; do
    cmd="${AGENT_CMDS[$name]}"
    if command -v "$cmd" &>/dev/null; then
        FOUND+=("$name")
    else
        NOT_FOUND+=("$name")
    fi
done

ENABLED=()

# Show found agents
if [ ${#FOUND[@]} -gt 0 ]; then
    info "Found ${#FOUND[@]} agent CLI(s) in PATH:"
    echo ""
    for name in "${FOUND[@]}"; do
        cmd="${AGENT_CMDS[$name]}"
        path=$(command -v "$cmd")
        # Mark if already in config
        in_config=""
        for ea in "${EXISTING_AGENTS[@]}"; do
            [[ "$ea" == "$name" ]] && in_config=" [in config]"
        done
        echo "    ✅ ${AGENT_DESCS[$name]}  ($path)${in_config}"
    done
    echo ""
    ask "Enable all detected agents? [Y/n]"
    read_input ENABLE_ALL "y"
    if [[ "$ENABLE_ALL" =~ ^[Yy]$ ]]; then
        ENABLED=("${FOUND[@]}")
    else
        for name in "${FOUND[@]}"; do
            ask "  Enable ${AGENT_DESCS[$name]}? [Y/n]"
            read_input ENABLE_ONE "y"
            [[ "$ENABLE_ONE" =~ ^[Yy]$ ]] && ENABLED+=("$name")
        done
    fi
    echo ""
fi

# Show not-installed agents with install hints
NOT_FOUND_NO_HARNESS=()
for name in "${NOT_FOUND[@]}"; do
    [[ "$name" != "harness" ]] && NOT_FOUND_NO_HARNESS+=("$name")
done
if [ ${#NOT_FOUND_NO_HARNESS[@]} -gt 0 ]; then
    info "Not installed (install manually if needed):"
    echo ""
    for name in "${NOT_FOUND_NO_HARNESS[@]}"; do
        echo "    ❌ ${AGENT_DESCS[$name]}"
        echo "       ${AGENT_INSTALL_HINTS[$name]}"
    done
    echo ""
fi

# If no agents enabled, offer harness-factory
if [ ${#ENABLED[@]} -eq 0 ]; then
    warn "No agents enabled."
    echo ""
    info "harness-factory is a lightweight Go binary (~6MB) that works as a"
    info "profile-driven agent — no API keys needed beyond a LiteLLM proxy."
    echo ""
    ask "Install harness-factory as default agent? [Y/n]"
    read_input INSTALL_HARNESS "y"
    if [[ "$INSTALL_HARNESS" =~ ^[Yy]$ ]]; then
        _install_harness
    else
        warn "No agents to run. Install an agent CLI and run:"
        echo "    cd $INSTALL_DIR && uv run main.py"
        exit 0
    fi
fi

ok "Enabled agents: ${ENABLED[*]}"
echo ""

# ============================================================
# Step 4: Token configuration
# ============================================================
info "Step 4/6: Token configuration"
echo ""

# ACP Bridge token
if [ -n "${ACP_BRIDGE_TOKEN:-}" ]; then
    ok "ACP_BRIDGE_TOKEN already set in environment"
    BRIDGE_TOKEN="$ACP_BRIDGE_TOKEN"
elif $CONFIG_EXISTS && [ -f "$INSTALL_DIR/.env" ] && grep -q '^ACP_BRIDGE_TOKEN=' "$INSTALL_DIR/.env" 2>/dev/null; then
    BRIDGE_TOKEN=$(grep '^ACP_BRIDGE_TOKEN=' "$INSTALL_DIR/.env" | cut -d= -f2-)
    ok "ACP_BRIDGE_TOKEN read from existing .env"
else
    BRIDGE_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    ask "ACP Bridge auth token [auto-generated]:"
    read_input USER_TOKEN "$BRIDGE_TOKEN"
    BRIDGE_TOKEN="$USER_TOKEN"
fi

# IP allowlist
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
DEFAULT_IPS="127.0.0.1"
[ -n "$LOCAL_IP" ] && DEFAULT_IPS="127.0.0.1, $LOCAL_IP, 172.16.0.0/12, 10.0.0.0/8"
echo ""
info "IP allowlist controls which IPs can call the Bridge API."
info "Default: $DEFAULT_IPS"
ask "Additional IPs to allow (comma-separated) [none]:"
read_input EXTRA_IPS ""

ALLOWED_IPS="$DEFAULT_IPS"
[ -n "$EXTRA_IPS" ] && ALLOWED_IPS="$ALLOWED_IPS, $EXTRA_IPS"
ok "Allowed IPs: $ALLOWED_IPS"

# LiteLLM — needed for codex, qwen, harness
NEEDS_LITELLM=false
for name in "${ENABLED[@]}"; do
    [[ "$name" == "codex" || "$name" == "qwen" || "$name" == "trae" || "$name" == "harness" ]] && NEEDS_LITELLM=true
done

LITELLM_KEY=""
LITELLM_URL=""
if $NEEDS_LITELLM; then
    echo ""
    info "Some agents (codex/trae/qwen/harness) need a LiteLLM proxy for LLM access."

    if command -v litellm &>/dev/null; then
        ok "litellm found: $(command -v litellm)"
        ask "LiteLLM proxy URL [http://localhost:4000]:"
        read_input LITELLM_URL "http://localhost:4000"
        ask "LiteLLM API key (LITELLM_API_KEY) [skip]:"
        read_input LITELLM_KEY ""
    else
        warn "litellm not found."
        ask "Install LiteLLM now? [Y/n]"
        read_input INSTALL_LITELLM "y"
        if [[ "$INSTALL_LITELLM" =~ ^[Yy]$ ]]; then
            info "Installing litellm (>=1.83.0, security fix)..."
            uv tool install --python 3.13 "litellm[proxy]>=1.83.0" 2>/dev/null \
                || uv tool install "litellm[proxy]>=1.83.0" 2>/dev/null \
                || { warn "Failed to install litellm. Try: uvx --python 3.13 --from 'litellm[proxy]>=1.83.0' litellm --port 4000"; }
            if command -v litellm &>/dev/null; then
                ok "litellm installed"
            fi
        fi
        LITELLM_URL="http://localhost:4000"
        LITELLM_KEY="sk-litellm-local"
        info "Default config: URL=$LITELLM_URL  key=$LITELLM_KEY"
        info "LiteLLM will be auto-started when you launch ACP Bridge."
        echo ""
    fi
fi

# Webhook (optional)
echo ""
WEBHOOK_URL=""
WEBHOOK_FORMAT=""
WEBHOOK_TOKEN=""
WEBHOOK_SECRET=""
WEBHOOK_ACCOUNT=""
WEBHOOK_TARGET=""
info "Webhook pushes async job results to an IM platform."
info "  1) OpenClaw  — RPC format + Bearer token"
info "  2) Hermes    — generic JSON + HMAC-SHA256"
info "  3) Skip"
ask "Webhook type [3]:"
read_input WEBHOOK_TYPE "3"
case "$WEBHOOK_TYPE" in
    1)
        WEBHOOK_FORMAT="openclaw"
        ask "  OpenClaw URL [http://localhost:18789/tools/invoke]:"
        read_input WEBHOOK_URL "http://localhost:18789/tools/invoke"
        ask "  OpenClaw token (OPENCLAW_TOKEN) [skip]:"
        read_input WEBHOOK_TOKEN ""
        ask "  Account ID [default]:"
        read_input WEBHOOK_ACCOUNT "default"
        ask "  Default target (e.g. channel:123456) [skip]:"
        read_input WEBHOOK_TARGET ""
        ;;
    2)
        WEBHOOK_FORMAT="generic"
        ask "  Hermes webhook URL [http://127.0.0.1:8644/webhooks/acp-result]:"
        read_input WEBHOOK_URL "http://127.0.0.1:8644/webhooks/acp-result"
        ask "  HMAC secret (HERMES_WEBHOOK_SECRET) [skip]:"
        read_input WEBHOOK_SECRET ""
        ask "  Account ID [default]:"
        read_input WEBHOOK_ACCOUNT "default"
        ask "  Default target (e.g. channel:123456) [skip]:"
        read_input WEBHOOK_TARGET ""
        ;;
esac

echo ""

# ============================================================
# Step 5: Generate config and .env
# ============================================================
info "Step 5/6: Generating configuration..."

# Determine which agents are NEW (not already in config)
NEW_AGENTS=()
for name in "${ENABLED[@]}"; do
    is_existing=false
    for ea in "${EXISTING_AGENTS[@]}"; do
        [[ "$ea" == "$name" ]] && is_existing=true
    done
    $is_existing || NEW_AGENTS+=("$name")
done

# Check for existing config — incremental update vs fresh generate
CONFIG_FILE="$INSTALL_DIR/config.yaml"

# Agent block generator — used by both incremental and fresh paths
_gen_agent_block() {
    local name="$1"
    local desc="${AGENT_DESCS[$name]}"
    case "$name" in
        kiro)
            echo '  kiro:'
            echo '    enabled: true'
                    echo '    mode: "acp"'
                    echo '    command: "kiro-cli"'
                    echo '    acp_args: ["acp", "--trust-all-tools"]'
                    echo '    working_dir: "/tmp"'
                    echo "    description: \"$desc\""
                    ;;
                claude)
                    echo '  claude:'
                    echo '    enabled: true'
                    echo '    mode: "acp"'
                    echo '    command: "claude-agent-acp"'
                    echo '    acp_args: []'
                    echo '    working_dir: "/tmp"'
                    echo "    description: \"$desc\""
                    ;;
                codex)
                    echo '  codex:'
                    echo '    enabled: true'
                    echo '    mode: "pty"'
                    echo '    command: "codex"'
                    echo '    args: ["exec", "--full-auto", "--skip-git-repo-check"]'
                    echo '    working_dir: "/tmp"'
                    echo "    description: \"$desc\""
                    ;;
                trae)
                    echo '  trae:'
                    echo '    enabled: true'
                    echo '    mode: "pty"'
                    echo '    command: "trae-cli"'
                    echo '    args: ["run", "--working-dir", "/tmp/trae"]'
                    echo '    working_dir: "/tmp/trae"'
                    echo "    description: \"$desc\""
                    ;;
                qwen)
                    echo '  qwen:'
                    echo '    enabled: true'
                    echo '    mode: "acp"'
                    echo '    command: "qwen"'
                    echo '    acp_args: ["--acp"]'
                    echo '    working_dir: "/tmp"'
                    echo "    description: \"$desc\""
                    ;;
                opencode)
                    echo '  opencode:'
                    echo '    enabled: true'
                    echo '    mode: "acp"'
                    echo '    command: "opencode"'
                    echo '    acp_args: ["acp"]'
                    echo '    working_dir: "/tmp"'
                    echo "    description: \"$desc\""
                    ;;
                hermes)
                    echo '  hermes:'
                    echo '    enabled: true'
                    echo '    mode: "acp"'
                    echo '    command: "hermes"'
                    echo '    acp_args: ["acp"]'
                    echo '    working_dir: "/tmp"'
                    echo "    description: \"$desc\""
                    ;;
                harness)
                    echo '  harness:'
                    echo '    enabled: true'
                    echo '    mode: "acp"'
                    echo '    command: "harness-factory"'
                    echo '    acp_args: []'
                    echo '    working_dir: "/tmp"'
                    echo "    description: \"$desc\""
                    echo '    profile:'
                    echo '      tools:'
                    echo '        fs: { permissions: [read, write, list, search] }'
                    echo '        git: { permissions: [diff, log, show] }'
                    echo '        shell: { allowlist: [ls, cat, grep, find, wc] }'
                    echo '        web: { permissions: [fetch] }'
                    echo '      orchestration: free'
                    echo '      resources:'
                    echo '        timeout: 300s'
                    echo '        max_turns: 20'
                    echo '      agent:'
                    echo '        model: "auto"'
                    echo '        temperature: 0.3'
            ;;
    esac
}

if $CONFIG_EXISTS; then
    if [ ${#NEW_AGENTS[@]} -eq 0 ]; then
        ok "config.yaml up to date — no new agents to add"
    else
        info "Adding ${#NEW_AGENTS[@]} new agent(s) to config.yaml: ${NEW_AGENTS[*]}"
        # Ensure agents: section exists
        if ! grep -q '^agents:' "$CONFIG_FILE" 2>/dev/null; then
            echo '' >> "$CONFIG_FILE"
            echo 'agents:' >> "$CONFIG_FILE"
        fi
        for name in "${NEW_AGENTS[@]}"; do
            _gen_agent_block "$name" >> "$CONFIG_FILE"
        done
        ok "Appended ${#NEW_AGENTS[@]} agent(s) to config.yaml"
    fi
    # Update .env with any new tokens
    ENV_FILE="$INSTALL_DIR/.env"
    if [ -f "$ENV_FILE" ]; then
        grep -q "ACP_BRIDGE_TOKEN" "$ENV_FILE" || echo "ACP_BRIDGE_TOKEN=$BRIDGE_TOKEN" >> "$ENV_FILE"
        if [ -n "$LITELLM_KEY" ]; then
            grep -q "LITELLM_API_KEY" "$ENV_FILE" || echo "LITELLM_API_KEY=$LITELLM_KEY" >> "$ENV_FILE"
        fi
        if [ -n "$WEBHOOK_TOKEN" ]; then
            grep -q "OPENCLAW_TOKEN" "$ENV_FILE" || echo "OPENCLAW_TOKEN=$WEBHOOK_TOKEN" >> "$ENV_FILE"
        fi
        if [ -n "$WEBHOOK_SECRET" ]; then
            grep -q "HERMES_WEBHOOK_SECRET" "$ENV_FILE" || echo "HERMES_WEBHOOK_SECRET=$WEBHOOK_SECRET" >> "$ENV_FILE"
        fi
        ok "Updated .env (preserved existing values)"
    else
        {
            echo "# Generated by install.sh — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
            echo "ACP_BRIDGE_TOKEN=$BRIDGE_TOKEN"
            echo "CLAUDE_CODE_USE_BEDROCK=1"
            echo "ANTHROPIC_MODEL=global.anthropic.claude-sonnet-4-6"
            [ -n "$LITELLM_KEY" ] && echo "LITELLM_API_KEY=$LITELLM_KEY"
            [ -n "$WEBHOOK_TOKEN" ] && echo "OPENCLAW_TOKEN=$WEBHOOK_TOKEN"
            [ -n "$WEBHOOK_SECRET" ] && echo "HERMES_WEBHOOK_SECRET=$WEBHOOK_SECRET"
        } > "$ENV_FILE"
        ok "Created .env"
    fi
else
    # --- Fresh install: generate everything ---

    # --- .env ---
    ENV_FILE="$INSTALL_DIR/.env"
    {
        echo "# Generated by install.sh — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "ACP_BRIDGE_TOKEN=$BRIDGE_TOKEN"
        echo "CLAUDE_CODE_USE_BEDROCK=1"
        echo "ANTHROPIC_MODEL=global.anthropic.claude-sonnet-4-6"
        [ -n "$LITELLM_KEY" ] && echo "LITELLM_API_KEY=$LITELLM_KEY"
        [ -n "$WEBHOOK_TOKEN" ] && echo "OPENCLAW_TOKEN=$WEBHOOK_TOKEN"
        [ -n "$WEBHOOK_SECRET" ] && echo "HERMES_WEBHOOK_SECRET=$WEBHOOK_SECRET"
    } > "$ENV_FILE"
    ok "Created .env"

    # --- litellm-config.yaml ---
    if $NEEDS_LITELLM; then
        LITELLM_CFG="$INSTALL_DIR/litellm-config.yaml"
        {
            echo "# Generated by install.sh — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
            echo 'model_list:'
            echo '  - model_name: "bedrock/anthropic.claude-sonnet-4-6"'
            echo '    litellm_params:'
            echo '      model: "bedrock/anthropic.claude-sonnet-4-6"'
            echo '  - model_name: "bedrock/anthropic.claude-opus-4-6-v1"'
            echo '    litellm_params:'
            echo '      model: "bedrock/anthropic.claude-opus-4-6-v1"'
            echo '  - model_name: "bedrock/converse/moonshotai.kimi-k2.5"'
            echo '    litellm_params:'
            echo '      model: "bedrock/converse/moonshotai.kimi-k2.5"'
            echo '  - model_name: "bedrock/deepseek.v3.2"'
            echo '    litellm_params:'
            echo '      model: "bedrock/deepseek.v3.2"'
            echo '  - model_name: "bedrock/converse/qwen.qwen3-235b-a22b-2507-v1:0"'
            echo '    litellm_params:'
            echo '      model: "bedrock/converse/qwen.qwen3-235b-a22b-2507-v1:0"'
            echo '  - model_name: "bedrock/converse/minimax.minimax-m2.5"'
            echo '    litellm_params:'
            echo '      model: "bedrock/converse/minimax.minimax-m2.5"'
            echo '  - model_name: "bedrock/converse/google.gemma-3-12b-it"'
            echo '    litellm_params:'
            echo '      model: "bedrock/converse/google.gemma-3-12b-it"'
            echo '  - model_name: "bedrock/converse/zai.glm-5"'
            echo '    litellm_params:'
            echo '      model: "bedrock/converse/zai.glm-5"'
            echo ''
            echo 'general_settings:'
            echo "  master_key: \"$LITELLM_KEY\""
            echo ''
            echo 'litellm_settings:'
            echo '  drop_params: true'
        } > "$LITELLM_CFG"
        ok "Created litellm-config.yaml"
    fi

    # --- config.yaml ---
    _gen_config() {
        echo "# Generated by install.sh — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo 'server:'
        echo '  host: "0.0.0.0"'
        echo '  port: 18010'
        echo '  session_ttl_hours: 24'
        echo '  shutdown_timeout: 30'
        echo ''
        echo 'pool:'
        echo '  max_processes: 8'
        echo '  max_per_agent: 4'
        echo ''
        echo 'security:'
        echo '  auth_token: "${ACP_BRIDGE_TOKEN}"'
        echo '  allowed_ips:'
        IFS=',' read -ra IP_ARRAY <<< "$ALLOWED_IPS"
        for ip in "${IP_ARRAY[@]}"; do
            ip=$(echo "$ip" | xargs)
            [ -n "$ip" ] && echo "    - \"$ip\""
        done

        if $NEEDS_LITELLM && [ -n "$LITELLM_URL" ]; then
            LITELLM_REQUIRED=()
            for name in "${ENABLED[@]}"; do
                [[ "$name" == "codex" || "$name" == "qwen" ]] && LITELLM_REQUIRED+=("$name")
            done
            echo ''
            echo 'litellm:'
            echo "  url: \"$LITELLM_URL\""
            echo "  required_by: [$(printf '"%s", ' "${LITELLM_REQUIRED[@]}" | sed 's/, $//')]"
            echo '  env:'
            echo '    LITELLM_API_KEY: "${LITELLM_API_KEY}"'
        fi

        if [ -n "$WEBHOOK_URL" ]; then
            echo ''
            echo 'webhook:'
            echo "  url: \"$WEBHOOK_URL\""
            echo "  format: \"$WEBHOOK_FORMAT\""
            if [ "$WEBHOOK_FORMAT" = "openclaw" ]; then
                echo '  token: "${OPENCLAW_TOKEN}"'
            else
                echo '  secret: "${HERMES_WEBHOOK_SECRET}"'
            fi
            echo "  account_id: \"$WEBHOOK_ACCOUNT\""
            echo "  target: \"$WEBHOOK_TARGET\""
        fi

        echo ''
        echo 'agents:'
        for name in "${ENABLED[@]}"; do
            _gen_agent_block "$name"
        done
    }
    _gen_config > "$CONFIG_FILE"
    ok "Created config.yaml"
fi

# ============================================================
# Step 5.5: systemd unit installation
# ============================================================
HAS_SYSTEMD=false
if command -v systemctl &>/dev/null && systemctl --version &>/dev/null 2>&1; then
    HAS_SYSTEMD=true
fi

SYSTEMD_INSTALLED=false
if $HAS_SYSTEMD; then
    CURRENT_USER=$(whoami)
    UV_BIN=$(command -v uv)
    LITELLM_BIN=$(command -v litellm 2>/dev/null || echo "")

    # --- acp-bridge.service ---
    if systemctl cat acp-bridge.service &>/dev/null 2>&1; then
        ok "acp-bridge.service already installed"
        SYSTEMD_INSTALLED=true
    else
        info "Installing systemd unit: acp-bridge.service"
        UNIT_FILE="/etc/systemd/system/acp-bridge.service"
        sudo tee "$UNIT_FILE" >/dev/null <<UNIT
[Unit]
Description=ACP Bridge — ACP protocol gateway for CLI agents
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$UV_BIN run main.py
KillMode=control-group
KillSignal=SIGTERM
TimeoutStopSec=35
Restart=always
RestartSec=3
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=HOME=$HOME

[Install]
WantedBy=multi-user.target
UNIT
        sudo systemctl daemon-reload
        sudo systemctl enable acp-bridge.service
        ok "acp-bridge.service installed and enabled"
        SYSTEMD_INSTALLED=true
    fi

    # --- litellm.service (if needed) ---
    if $NEEDS_LITELLM && [ -n "$LITELLM_BIN" ]; then
        if systemctl cat litellm.service &>/dev/null 2>&1; then
            ok "litellm.service already installed"
        else
            LITELLM_CFG_PATH="$INSTALL_DIR/litellm-config.yaml"
            LITELLM_ARGS="--port 4000"
            [ -f "$LITELLM_CFG_PATH" ] && LITELLM_ARGS="--config $LITELLM_CFG_PATH --port 4000"
            info "Installing systemd unit: litellm.service"
            sudo tee /etc/systemd/system/litellm.service >/dev/null <<UNIT
[Unit]
Description=LiteLLM Proxy — OpenAI-compatible gateway to Bedrock
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$HOME
EnvironmentFile=$INSTALL_DIR/.env
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=HOME=$HOME
ExecStart=$LITELLM_BIN $LITELLM_ARGS
Restart=on-failure
RestartSec=5
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
UNIT
            sudo systemctl daemon-reload
            sudo systemctl enable litellm.service
            ok "litellm.service installed and enabled"
        fi
    fi
fi

# ============================================================
# Step 6: Done
# ============================================================
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✅ ACP Bridge is ready!                                    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Agents:  ${ENABLED[*]}"
echo "  Token:   ${BRIDGE_TOKEN:0:12}..."
echo "  Config:  $CONFIG_FILE"
if $IS_UPDATE && [ ${#NEW_AGENTS[@]} -gt 0 ]; then
    echo "  Added:   ${NEW_AGENTS[*]}"
fi
echo ""
echo "  Start now:"
if $SYSTEMD_INSTALLED; then
    echo "    ./bridge-ctl.sh restart      (systemd)"
else
    echo "    cd $INSTALL_DIR && bash start.sh"
fi
echo ""
if [ -n "$WEBHOOK_URL" ]; then
    echo "  Async jobs will push results to: $WEBHOOK_URL"
    echo ""
fi
# --- OpenClaw skill setup hint (shown once) ---
PORT=$(grep -E '^\s*port:' "$CONFIG_FILE" 2>/dev/null | head -1 | awk '{print $2}')
PORT=${PORT:-18010}
PRIVATE_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
PUBLIC_IP=$(curl -s --max-time 3 http://checkip.amazonaws.com 2>/dev/null || curl -s --max-time 3 http://ifconfig.me 2>/dev/null || echo "")

echo "┌──────────────────────────────────────────────────────────────┐"
echo "│  🦞 OpenClaw Skill Setup                                     │"
echo "│                                                              │"
echo "│  Tell your OpenClaw bot to install the ACP Bridge skill:     │"
echo "│                                                              │"
echo "│  Skill URL:                                                  │"
echo "│    https://github.com/xiwan/acp-bridge/tree/main/skill       │"
echo "│                                                              │"
echo "│  Then set these env vars in OpenClaw:                        │"
echo "│                                                              │"
printf "│    ACP_TOKEN=%s\n" "$BRIDGE_TOKEN"
if [ -n "$PRIVATE_IP" ]; then
printf "│    ACP_BRIDGE_URL=http://%s:%s  (private)\n" "$PRIVATE_IP" "$PORT"
fi
if [ -n "$PUBLIC_IP" ]; then
printf "│    ACP_BRIDGE_URL=http://%s:%s  (public)\n" "$PUBLIC_IP" "$PORT"
fi
echo "│                                                              │"
echo "└──────────────────────────────────────────────────────────────┘"
echo ""

ask "Start ACP Bridge now? [Y/n]"
read_input START_NOW "y"
if [[ "$START_NOW" =~ ^[Yy]$ ]]; then
    echo ""

    # --- AWS credentials check (needed for Bedrock) ---
    if $NEEDS_LITELLM; then
        info "Checking AWS credentials (needed for Bedrock models)..."
        if aws sts get-caller-identity &>/dev/null; then
            ok "AWS credentials valid: $(aws sts get-caller-identity --query 'Arn' --output text 2>/dev/null)"
        elif [ -n "${AWS_ACCESS_KEY_ID:-}" ]; then
            ok "AWS_ACCESS_KEY_ID is set (assuming valid)"
        else
            warn "No AWS credentials found. Bedrock models will fail."
            info "Fix: run 'aws configure' or attach an IAM Role to this EC2 instance."
            ask "Continue anyway? [Y/n]"
            read_input CONTINUE_NO_AWS "y"
            [[ ! "$CONTINUE_NO_AWS" =~ ^[Yy]$ ]] && exit 0
        fi
    fi

    if $SYSTEMD_INSTALLED; then
        if $NEEDS_LITELLM; then
            info "Starting LiteLLM + ACP Bridge via systemd..."
            sudo systemctl start litellm.service 2>/dev/null || true
            sleep 3
        fi
        sudo systemctl start acp-bridge.service
        sleep 2
        if curl -s --max-time 3 "http://127.0.0.1:$PORT/health" &>/dev/null; then
            ok "ACP Bridge is running (systemd)"
            echo "  Manage with: ./bridge-ctl.sh {status|restart|stop|logs|health}"
        else
            warn "Bridge started but /health not responsive yet"
            echo "  Check logs: ./bridge-ctl.sh logs 50"
        fi
    else
        exec bash "$INSTALL_DIR/start.sh"
    fi
fi
