# Agent Compatibility Matrix

> Which CLI agents work with ACP Bridge today?

| Agent | Vendor | ACP | Mode | Status | Tests |
|-------|--------|-----|------|--------|-------|
| [Kiro CLI](https://github.com/aws/kiro-cli) | AWS | ✅ Native | `acp` | ✅ Integrated | 7/7 |
| [Claude Code](https://github.com/anthropics/claude-code) | Anthropic | ✅ Native | `acp` | ✅ Integrated | 5/5 |
| [Qwen Code](https://www.npmjs.com/package/@anthropic-ai/qwen-code) | Alibaba | ✅ `--acp` | `acp` | ✅ Integrated | 6/6 |
| [OpenAI Codex](https://github.com/openai/codex) | OpenAI | ❌ | `pty` | ✅ Integrated | 6/6 |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Google | 🧪 `--experimental-acp` | — | 🟡 Planned | — |
| [Copilot CLI](https://docs.github.com/en/copilot/reference/acp-server) | GitHub | ✅ `--acp` | — | 🟡 Planned | — |
| [OpenCode](https://github.com/opencode-ai/opencode) | Open Source | ✅ `opencode acp` | `acp` | ✅ Integrated | 6/6 |
| [Harness Factory](https://github.com/xiwan/harness-factory) | Open Source | ✅ Native | `acp` | ✅ Integrated | 4/4 |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | Nous Research | ✅ `hermes acp` | `acp` | ✅ Integrated | 8/8 |
| [CoStrict](https://github.com/zgsm-ai/costrict) | Open Source 🇨🇳 | ✅ Native | — | 🟡 Planned | — |
| [Trae Agent](https://github.com/bytedance/trae-agent) | ByteDance 🇨🇳 | ❌ | — | ⚪ No ACP | — |
| [Aider](https://github.com/Aider-AI/aider) | Open Source | ❌ | — | ⚪ No ACP | — |

**Legend:** ✅ Integrated — 🟡 Planned (ACP-ready) — ⚪ No ACP support yet — 🧪 Experimental

> Agents without ACP can still be integrated via PTY mode (like Codex). PRs welcome!
