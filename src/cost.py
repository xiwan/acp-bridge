"""Token estimation and cost calculation for ACP Bridge agents."""

BEDROCK_PRICING = {
    "claude-sonnet-4": {"input": 3.0 / 1e6, "output": 15.0 / 1e6},
    "claude-haiku":    {"input": 0.25 / 1e6, "output": 1.25 / 1e6},
    "qwen-max":        {"input": 0.4 / 1e6, "output": 1.2 / 1e6},
}

_DEFAULT_PRICING = {"input": 1.0 / 1e6, "output": 3.0 / 1e6}


def estimate_tokens(text: str, model: str = "") -> int:
    if not text:
        return 0
    # 中文字符约 1 token，其余用 utf8 bytes/4 估算
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    rest_bytes = len(text.encode("utf-8")) - cjk * 3
    return max(1, cjk + rest_bytes // 4)


def calc_cost(input_tokens: int, output_tokens: int, model: str = "") -> float:
    pricing = BEDROCK_PRICING.get(model, _DEFAULT_PRICING)
    return input_tokens * pricing["input"] + output_tokens * pricing["output"]


def model_from_agent(agent: str) -> str:
    """Map agent name to model for pricing lookup."""
    if "claude" in agent:
        return "claude-sonnet-4"
    if "qwen" in agent:
        return "qwen-max"
    if agent in ("harness", "opencode", "codex"):
        return "claude-haiku"  # 轻量模型，用 haiku 价格估算
    return ""
