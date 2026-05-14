#!/usr/bin/env python3
"""
ACP Bridge MCP Server — stdio transport

Exposes ACP Bridge agents as MCP tools for any MCP-compatible client
(Claude Desktop, Kiro, Cursor, VS Code Copilot, etc.)

Usage:
    Set ACP_BRIDGE_URL and ACP_BRIDGE_TOKEN env vars, then run:
    python3 server.py

Environment variables:
    ACP_BRIDGE_URL   — Bridge address (required)
    ACP_BRIDGE_TOKEN — Auth token (required)
    ACP_TIMEOUT      — Sync call timeout in seconds (default: 300)
"""

import os
import sys
import json
import uuid
import asyncio
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# --- Config ---
BRIDGE_URL = os.environ.get("ACP_BRIDGE_URL", "").rstrip("/")
TOKEN = os.environ.get("ACP_BRIDGE_TOKEN", "")
TIMEOUT = int(os.environ.get("ACP_TIMEOUT", "300"))

if not BRIDGE_URL:
    print("ERROR: ACP_BRIDGE_URL is required", file=sys.stderr)
    sys.exit(1)
if not TOKEN:
    print("ERROR: ACP_BRIDGE_TOKEN is required", file=sys.stderr)
    sys.exit(1)

# --- HTTP Client ---
http_client = httpx.AsyncClient(
    base_url=BRIDGE_URL,
    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    timeout=httpx.Timeout(TIMEOUT, connect=10),
)

# --- MCP Server ---
server = Server("acp-bridge")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="acp_list_agents",
            description="List all available agents on the ACP Bridge",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="acp_call",
            description="Call a remote agent synchronously. For long tasks (>60s), use acp_submit_job instead.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "description": "Agent name (e.g. kiro, claude, codex, harness, opengame)"},
                    "prompt": {"type": "string", "description": "The prompt/task to send"},
                    "cwd": {"type": "string", "description": "Working directory for the agent"},
                    "session_id": {"type": "string", "description": "Session ID for multi-turn conversation continuity"},
                },
                "required": ["agent", "prompt"],
            },
        ),
        Tool(
            name="acp_submit_job",
            description="Submit an async background job. Returns job_id for status tracking.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "description": "Agent name"},
                    "prompt": {"type": "string", "description": "The prompt/task"},
                    "cwd": {"type": "string", "description": "Working directory"},
                    "target": {"type": "string", "description": "Delivery target (e.g. channel:id)"},
                    "channel": {"type": "string", "description": "Callback channel (discord, feishu)"},
                },
                "required": ["agent", "prompt"],
            },
        ),
        Tool(
            name="acp_job_status",
            description="Query the status/result of an async job",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Job ID from acp_submit_job"},
                },
                "required": ["job_id"],
            },
        ),
        Tool(
            name="acp_pipeline",
            description="Execute a multi-agent pipeline (sequence, parallel, race, or conversation)",
            inputSchema={
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["sequence", "parallel", "race", "conversation"], "description": "Pipeline mode"},
                    "steps": {
                        "type": "array",
                        "description": "Steps for sequence/parallel/race: [{agent, prompt, timeout?, output_as?}]",
                        "items": {
                            "type": "object",
                            "properties": {
                                "agent": {"type": "string"},
                                "prompt": {"type": "string"},
                                "timeout": {"type": "integer"},
                                "output_as": {"type": "string"},
                            },
                            "required": ["agent", "prompt"],
                        },
                    },
                    "participants": {"type": "array", "items": {"type": "string"}, "description": "For conversation mode: agent names"},
                    "topic": {"type": "string", "description": "For conversation mode: discussion topic"},
                    "config": {"type": "object", "description": "Conversation config: {max_turns, stop_conditions}"},
                    "context": {"type": "object", "description": "Optional: {shared_cwd: '/path'}"},
                },
                "required": ["mode"],
            },
        ),
        Tool(
            name="acp_pipeline_status",
            description="Query pipeline status and results",
            inputSchema={
                "type": "object",
                "properties": {
                    "pipeline_id": {"type": "string", "description": "Pipeline ID"},
                },
                "required": ["pipeline_id"],
            },
        ),
        Tool(
            name="acp_invoke_tool",
            description="Invoke an OpenClaw tool (message, web_search, browser, tts, etc.)",
            inputSchema={
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "description": "Tool name (message, web_search, web_fetch, tts, browser, nodes, cron, image)"},
                    "action": {"type": "string", "description": "Tool action (e.g. send, react, screenshot)"},
                    "args": {"type": "object", "description": "Tool arguments"},
                    "channel": {"type": "string", "description": "IM channel context"},
                },
                "required": ["tool"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        result = await _dispatch(name, arguments)
        return [TextContent(type="text", text=result)]
    except httpx.HTTPStatusError as e:
        return [TextContent(type="text", text=f"HTTP {e.response.status_code}: {e.response.text}")]
    except httpx.ConnectError:
        return [TextContent(type="text", text=f"Connection failed: cannot reach {BRIDGE_URL}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


async def _dispatch(name: str, args: dict[str, Any]) -> str:
    if name == "acp_list_agents":
        resp = await http_client.get("/agents")
        resp.raise_for_status()
        data = resp.json()
        agents = data.get("agents", data)
        lines = ["Available agents:"]
        for a in agents:
            n = a.get("name", "?")
            desc = a.get("description", "")
            lines.append(f"  • {n} — {desc}")
        return "\n".join(lines)

    elif name == "acp_call":
        payload: dict[str, Any] = {
            "agent_name": args["agent"],
            "session_id": args.get("session_id") or str(uuid.uuid4()),
            "input": [{"parts": [{"content": args["prompt"], "content_type": "text/plain"}]}],
        }
        if args.get("cwd"):
            payload["cwd"] = args["cwd"]

        resp = await http_client.post("/runs", json=payload)
        resp.raise_for_status()
        data = resp.json()

        status = data.get("status", "unknown")
        if status == "completed":
            parts = []
            for output in data.get("output", []):
                for part in output.get("parts", []):
                    if part.get("name") not in ("thought", "fallback_info") and part.get("content"):
                        parts.append(part["content"])
            return "\n".join(parts) if parts else "(empty response)"
        elif status == "failed":
            err = data.get("error", {})
            return f"Failed: {err.get('code', 'error')}: {err.get('message', 'unknown')}"
        else:
            return f"Status: {status}\n{json.dumps(data, indent=2)}"

    elif name == "acp_submit_job":
        payload: dict[str, Any] = {
            "agent_name": args["agent"],
            "prompt": args["prompt"],
        }
        if args.get("cwd"):
            payload["cwd"] = args["cwd"]
        if args.get("target"):
            payload["target"] = args["target"]
        if args.get("channel"):
            payload["channel"] = args["channel"]

        resp = await http_client.post("/jobs", json=payload)
        resp.raise_for_status()
        data = resp.json()
        job_id = data.get("job_id", "?")
        return f"Job submitted: {job_id}\nUse acp_job_status(job_id=\"{job_id}\") to check progress."

    elif name == "acp_job_status":
        resp = await http_client.get(f"/jobs/{args['job_id']}")
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "unknown")
        if status == "completed":
            return f"Completed ({data.get('duration', '?')}s):\n\n{data.get('result', '')}"
        elif status == "failed":
            return f"Failed: {data.get('error', 'unknown')}"
        return f"Status: {status}"

    elif name == "acp_pipeline":
        payload: dict[str, Any] = {"mode": args["mode"]}
        for key in ("steps", "participants", "topic", "config", "context"):
            if args.get(key):
                payload[key] = args[key]

        resp = await http_client.post("/pipelines", json=payload)
        resp.raise_for_status()
        data = resp.json()
        pid = data.get("pipeline_id", "?")
        status = data.get("status", "submitted")

        if status == "completed":
            return f"Pipeline completed ({pid}):\n\n{data.get('output', '')}"
        return f"Pipeline submitted: {pid} (mode: {args['mode']})\nUse acp_pipeline_status(pipeline_id=\"{pid}\") to check."

    elif name == "acp_pipeline_status":
        resp = await http_client.get(f"/pipelines/{args['pipeline_id']}")
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "unknown")
        mode = data.get("mode", "?")

        if status == "completed":
            output = data.get("output", "")
            transcript = data.get("transcript", [])
            if transcript:
                lines = [f"Pipeline completed (mode: {mode}, {data.get('duration', '?')}s)\n"]
                for turn in transcript:
                    lines.append(f"[Turn {turn.get('turn', '?')}] {turn.get('agent', '?')}:\n{turn.get('content', '')}\n")
                return "\n".join(lines)
            return f"Pipeline completed (mode: {mode}):\n\n{output}"
        elif status == "failed":
            return f"Pipeline failed: {data.get('error', 'unknown')}"
        steps = data.get("steps", [])
        done = sum(1 for s in steps if s.get("status") == "completed")
        return f"Pipeline {status} (mode: {mode}, {done}/{len(steps)} steps done)"

    elif name == "acp_invoke_tool":
        payload: dict[str, Any] = {"tool": args["tool"]}
        if args.get("action"):
            payload["action"] = args["action"]
        if args.get("args"):
            payload["args"] = args["args"]
        if args.get("channel"):
            payload["channel"] = args["channel"]

        resp = await http_client.post("/tools/invoke", json=payload)
        resp.raise_for_status()
        return json.dumps(resp.json(), indent=2, ensure_ascii=False)

    return f"Unknown tool: {name}"


async def main():
    async with http_client:
        async with stdio_server() as (read_stream, write_stream):
            init_options = server.create_initialization_options()
            await server.run(read_stream, write_stream, init_options)


if __name__ == "__main__":
    asyncio.run(main())
