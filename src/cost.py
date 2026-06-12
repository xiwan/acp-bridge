"""Token estimation and cost calculation for ACP Bridge agents.

v0.23.0 (2026-05-28):
  - Pricing table 重写: 基于 LiteLLM model_prices_and_context_window 数据,
    覆盖 acp-bridge 实际路由的 bedrock 模型 (12 个).
  - 新增 calc_cost_v2(): 4-token-class 精准计算 (input / cache_read /
    cache_create / output), 防止重复计算 cache.
  - 旧 calc_cost(input, output, model) 保留向后兼容 (jobs.py 仍用).
  - 模型名 lookup 自动 strip 'bedrock/' 前缀, 自动尝试 'us./apac./eu.' CRIS prefix.
"""

# ============================================================================
# Pricing table (USD per token; 1e-6 = $1 / 1M tokens).
# Source: LiteLLM model_prices_and_context_window.json snapshot, 2026-05-28
# Coverage: 12 models from ~/.codex/litellm-config.yaml
# ============================================================================

BEDROCK_PRICING = {
    # === Claude (with prompt cache) ===
    "us.anthropic.claude-sonnet-4-6": {
        "input": 3.3e-06,           # $3.30/M (CRIS = base $3.00 + 10%)
        "output": 1.65e-05,         # $16.50/M
        "cache_create": 4.125e-06,  # $4.125/M (1.25x input)
        "cache_read": 3.3e-07,      # $0.33/M (0.1x input)
    },
    "us.anthropic.claude-opus-4-6-v1": {
        "input": 5.5e-06,           # $5.50/M (CRIS)
        "output": 2.75e-05,         # $27.50/M
        "cache_create": 6.875e-06,  # $6.875/M
        "cache_read": 5.5e-07,      # $0.55/M
    },
    "us.anthropic.claude-fable-5": {
        "input": 1e-05,             # $10.00/M
        "output": 5e-05,            # $50.00/M
        "cache_create": 1.25e-05,   # $12.50/M
        "cache_read": 1e-06,        # $1.00/M
    },
    # === Qwen ===
    "qwen.qwen3-coder-next": {
        "input": 5e-07,             # $0.50/M
        "output": 1.2e-06,          # $1.20/M
        "cache_create": None,       # 不支持 cache
        "cache_read": None,
    },
    "converse/qwen.qwen3-235b-a22b-2507-v1:0": {
        "input": 2.2e-07,           # $0.22/M (region-specific apac pricing)
        "output": 8.8e-07,          # $0.88/M
        "cache_create": None,
        "cache_read": None,
    },
    # === DeepSeek ===
    "deepseek.v3.2": {
        "input": 6.2e-07,           # $0.62/M
        "output": 1.85e-06,         # $1.85/M
        "cache_create": None,
        "cache_read": None,
    },
    # === Moonshot ===
    "moonshotai.kimi-k2.5": {
        "input": 6e-07,             # $0.60/M
        "output": 3e-06,            # $3.00/M
        "cache_create": None,
        "cache_read": None,
    },
    "converse/moonshotai.kimi-k2.5": {
        "input": 6e-07,
        "output": 3e-06,
        "cache_create": None,
        "cache_read": None,
    },
    # === Z AI (GLM) ===
    "converse/zai.glm-5": {
        "input": 1e-06,             # $1.00/M
        "output": 3.2e-06,          # $3.20/M
        "cache_create": None,
        "cache_read": None,
    },
    # === MiniMax ===
    "converse/minimax.minimax-m2.5": {
        "input": 3e-07,             # $0.30/M
        "output": 1.2e-06,          # $1.20/M
        "cache_create": None,
        "cache_read": None,
    },
    # === Google Gemma ===
    "converse/google.gemma-3-12b-it": {
        "input": 9e-08,             # $0.09/M
        "output": 2.9e-07,          # $0.29/M
        "cache_create": None,
        "cache_read": None,
    },
    # === Amazon Nova ===
    "converse/amazon.nova-pro-v1:0": {
        "input": 8e-07,             # $0.80/M
        "output": 3.2e-06,          # $3.20/M
        "cache_create": None,
        "cache_read": None,
    },
    "converse/amazon.nova-lite-v1:0": {
        "input": 6e-08,             # $0.06/M
        "output": 2.4e-07,          # $0.24/M
        "cache_create": None,
        "cache_read": None,
    },
    "converse/amazon.nova-micro-v1:0": {
        "input": 3.5e-08,           # $0.035/M
        "output": 1.4e-07,          # $0.14/M
        "cache_create": None,
        "cache_read": None,
    },
}

# Default pricing for unmatched models (放在 generic 中等价位)
_DEFAULT_PRICING = {
    "input": 1.0e-06,    # $1/M
    "output": 3.0e-06,   # $3/M
    "cache_create": None,
    "cache_read": None,
}


def lookup_pricing(model: str):
    """
    Resolve model id → pricing entry.

    acp-bridge `llm_usage.model` 字段是 LiteLLM model_id 去掉 'bedrock/' 前缀.
    本函数额外允许:
      - 直接命中 (我们的 key 已用 acp-bridge 实际记录的形式)
      - 带 'bedrock/' 前缀的输入 (jobs.py legacy 用法可能传)
      - 退化: 不知道的 model 用 _DEFAULT_PRICING

    Returns:
        (pricing_dict, matched_key)
    """
    if not model:
        return _DEFAULT_PRICING, None
    if model in BEDROCK_PRICING:
        return BEDROCK_PRICING[model], model
    # Strip 'bedrock/' prefix
    if model.startswith("bedrock/"):
        bare = model[len("bedrock/"):]
        if bare in BEDROCK_PRICING:
            return BEDROCK_PRICING[bare], bare
    # Try us./apac./eu. CRIS prefix variants
    for prefix in ("us.", "apac.", "eu."):
        k = prefix + model
        if k in BEDROCK_PRICING:
            return BEDROCK_PRICING[k], k
    return _DEFAULT_PRICING, None


def calc_cost_v2(model: str,
                 input_tokens: int,
                 cached_tokens: int = 0,
                 cache_creation_tokens: int = 0,
                 output_tokens: int = 0) -> float:
    """
    精准成本计算, 4 类 token 分别按各自单价算, 防止重复计算 cache.

    重要: `input_tokens` = 全 prompt (含 cache_read + cache_create + 普通 input),
    与 LiteLLM `usage.prompt_tokens` 语义一致 (acp-bridge 存到 `llm_usage.input_tokens`).
    所以"普通 input" = input_tokens - cached_tokens - cache_creation_tokens.

    模型不支持 cache 时, cache_read/cache_create 单价 fallback 到 input 单价.
    """
    p, _ = lookup_pricing(model)
    real_input = max(0, input_tokens - cached_tokens - cache_creation_tokens)
    p_in = p["input"]
    p_out = p["output"]
    p_cw = p["cache_create"] if p["cache_create"] is not None else p_in
    p_cr = p["cache_read"] if p["cache_read"] is not None else p_in
    return (real_input * p_in
            + cached_tokens * p_cr
            + cache_creation_tokens * p_cw
            + output_tokens * p_out)


# ============================================================================
# Backward-compatible legacy API (jobs.py 仍调 calc_cost / model_from_agent /
# estimate_tokens — 保留不动)
# ============================================================================

def estimate_tokens(text: str, model: str = "") -> int:
    """字符级 token 估算, 用于 jobs.py 占位 cost (不基于 LLM 真实回包)."""
    if not text:
        return 0
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    rest_bytes = len(text.encode("utf-8")) - cjk * 3
    return max(1, cjk + rest_bytes // 4)


def calc_cost(input_tokens: int, output_tokens: int, model: str = "") -> float:
    """
    Legacy 2-arg cost, no cache. jobs.py 占位估算用.
    新代码请用 calc_cost_v2.
    """
    p, _ = lookup_pricing(model)
    return input_tokens * p["input"] + output_tokens * p["output"]


def model_from_agent(agent: str) -> str:
    """
    Legacy: agent name → model lookup hint for pricing (jobs.py 占位用).
    返回的字符串能被 lookup_pricing 命中.
    """
    if "claude" in agent:
        return "us.anthropic.claude-sonnet-4-6"
    if "qwen" in agent:
        return "qwen.qwen3-coder-next"
    if agent in ("harness", "opencode", "codex"):
        # 历史上用 haiku 价位估算, 但 BEDROCK_PRICING 已无此 key —
        # 退到默认 pricing (差不多 input $1/M, output $3/M).
        return ""
    if "deepseek" in agent:
        return "deepseek.v3.2"
    return ""
