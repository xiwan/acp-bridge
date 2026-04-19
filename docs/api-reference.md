# API Endpoints

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/agents` | List registered agents | Yes |
| POST | `/runs` | Sync/streaming agent call | Yes |
| POST | `/jobs` | Submit async job | Yes |
| GET | `/jobs` | List all jobs + stats | Yes |
| GET | `/jobs/{job_id}` | Query single job | Yes |
| POST | `/pipelines` | Submit multi-agent pipeline | Yes |
| GET | `/pipelines` | List all pipelines | Yes |
| GET | `/pipelines/{id}` | Query single pipeline | Yes |
| GET | `/tools` | List available OpenClaw tools | Yes |
| POST | `/tools/invoke` | Invoke an OpenClaw tool (proxy) | Yes |
| POST | `/chat/messages` | Save a chat message (Web UI) | Yes |
| GET | `/chat/messages` | Load recent chat messages (Web UI) | Yes |
| DELETE | `/chat/messages` | Clear all chat messages (Web UI) | Yes |
| POST | `/chat/fold` | Fold a session's messages (Web UI) | Yes |
| POST | `/files` | Upload a file to Bridge | Yes |
| GET | `/files` | List uploaded files | Yes |
| DELETE | `/files/{filename}` | Delete an uploaded file | Yes |
| POST | `/harness` | Create a dynamic harness agent | Yes |
| GET | `/harness` | List dynamic harness agents | Yes |
| DELETE | `/harness/{name}` | Delete a dynamic harness agent | Yes |
| GET | `/health` | Health check | No |
| GET | `/health/agents` | Agent status | Yes |
| GET | `/stats` | Agent call statistics | Yes |
| GET | `/templates` | List prompt templates | Yes |
| POST | `/templates/render` | Render a template with variables | Yes |
| GET | `/ui` | Web UI chat interface (if enabled) | No |
| DELETE | `/sessions/{agent}/{session_id}` | Close session | Yes |
