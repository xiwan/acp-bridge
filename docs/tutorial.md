[← Getting Started](getting-started.md) | [Configuration →](configuration.md)

> **Docs:** [Getting Started](getting-started.md) · **Tutorial** · [Configuration](configuration.md) · [Agents](agents.md) · [API Reference](api-reference.md) · [Pipelines](pipelines.md) · [Async Jobs](async-jobs.md) · [Webhooks](webhooks.md) · [Client Usage](client-usage.md) · [Tools Proxy](tools-proxy.md) · [Security](security.md) · [Process Pool](process-pool.md) · [Testing](testing.md) · [Troubleshooting](troubleshooting.md)

# Tutorial: Discord → Agent → Discord in 5 Minutes

This walkthrough takes you from zero to a working IM-driven agent workflow: send a message in Discord, an agent executes it, and the result comes back to Discord.

## What you'll build

```
You (Discord) ──message──▶ OpenClaw ──HTTP──▶ ACP Bridge ──ACP──▶ Kiro CLI
                                                                      │
You (Discord) ◀──reply──── OpenClaw ◀──webhook── ACP Bridge ◀────────┘
```

## Prerequisites

- ACP Bridge installed and running (see [Getting Started](getting-started.md))
- At least one agent working (`./skill/scripts/acp-client.sh -a kiro "hello"` returns a response)
- [OpenClaw](https://github.com/AidenYangX/openclaw) or [Hermes](https://github.com/NousResearch/hermes-agent) running with a Discord bot connected

## Step 1: Configure webhook

Tell Bridge where to push results. Edit `config.yaml`:

```yaml
webhook:
  url: "http://<openclaw-ip>:18789/tools/invoke"   # OpenClaw gateway
  token: "${OPENCLAW_TOKEN}"                        # OpenClaw auth token
  format: "openclaw"                                # or "generic" for Hermes
  account_id: "default"                             # OpenClaw bot account name
  target: "channel:<your-discord-channel-id>"       # default push target
```

Restart Bridge after editing:

```bash
./bridge-ctl.sh restart
```

## Step 2: Install the OpenClaw skill

In your Discord channel, tell your OpenClaw bot:

```
Install the skill at https://github.com/xiwan/acp-bridge/tree/main/skill
```

Then set the environment variables in OpenClaw:

```
ACP_TOKEN=<your-bridge-token>
ACP_BRIDGE_URL=http://<bridge-ip>:18010
```

## Step 3: Talk to your agent

In Discord, type:

```
/cli ko Hello, introduce yourself
```

What happens:
1. OpenClaw receives your message
2. OpenClaw calls `POST /runs` on ACP Bridge with agent `kiro`
3. Bridge spawns (or reuses) a Kiro subprocess, sends the prompt
4. Kiro responds, Bridge returns the result to OpenClaw
5. OpenClaw posts the reply back to Discord

## Step 4: Try async (for long tasks)

For tasks that take more than a minute, use async jobs — you get the result pushed to Discord when it's done:

```
/cli ko --async Refactor src/agents.py to reduce duplication
```

Or via HTTP directly:

```bash
curl -s -X POST http://localhost:18010/jobs \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "kiro",
    "prompt": "Refactor src/agents.py to reduce duplication",
    "target": "channel:<your-discord-channel-id>",
    "channel": "discord",
    "callback_meta": {"account_id": "default"}
  }'
```

You'll get a `job_id` immediately. When the agent finishes, the result is pushed to your Discord channel automatically.

## Step 5: Multi-agent pipeline

Have two agents collaborate — Kiro reviews, Claude writes tests:

```bash
curl -s -X POST http://localhost:18010/pipelines \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "sequence",
    "steps": [
      {"agent": "kiro", "prompt": "Review src/agents.py and list issues"},
      {"agent": "claude", "prompt": "Based on the review, write pytest tests"}
    ],
    "target": "channel:<your-discord-channel-id>",
    "channel": "discord"
  }'
```

## Next steps

- [Pipelines](pipelines.md) — all orchestration modes (sequence, parallel, race, conversation)
- [Async Jobs](async-jobs.md) — webhook formats, Feishu support, job monitoring
- [Tools Proxy](tools-proxy.md) — send messages, search the web, control devices via OpenClaw tools
