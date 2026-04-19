# Client Usage

## acp-client.sh

```bash
export ACP_BRIDGE_URL=http://<bridge-ip>:18010
export ACP_TOKEN=<your-token>

# List available agents
./skill/scripts/acp-client.sh -l

# Sync call
./skill/scripts/acp-client.sh "Explain the project structure"

# Streaming call
./skill/scripts/acp-client.sh --stream "Analyze this code"

# Markdown card output (ideal for IM display)
./skill/scripts/acp-client.sh --card -a kiro "Introduce yourself"

# Specify agent
./skill/scripts/acp-client.sh -a claude "hello"

# Upload a file
./skill/scripts/acp-client.sh --upload data.csv

# Multi-turn conversation
./skill/scripts/acp-client.sh -s 00000000-0000-0000-0000-000000000001 "continue"
```
