"""Unit tests for src/cost.py.

v0.23.0:
  - 旧 calc_cost(input, output, model) / model_from_agent / estimate_tokens
    保留向后兼容, 但 model 名映射到新 pricing 表里的 key.
  - 新增 calc_cost_v2 (4-token-class) 测试.
  - 新增 lookup_pricing 测试.
"""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.cost import (
    estimate_tokens, calc_cost, calc_cost_v2,
    model_from_agent, lookup_pricing,
    BEDROCK_PRICING,
)


# ============================================================================
# Legacy estimate_tokens
# ============================================================================

def test_estimate_tokens_empty():
    assert estimate_tokens("") == 0


def test_estimate_tokens_english():
    t = estimate_tokens("hello world", "us.anthropic.claude-sonnet-4-6")
    assert t > 0


def test_estimate_tokens_qwen():
    text = "你好世界测试"
    t = estimate_tokens(text, "qwen.qwen3-coder-next")
    assert t == 6  # 6 CJK chars = 6 tokens


def test_estimate_tokens_default():
    t = estimate_tokens("test", "unknown-model")
    assert t >= 1


def test_estimate_tokens_cjk_mixed():
    # "hello你好" = 5 ASCII + 2 CJK
    t = estimate_tokens("hello你好")
    assert t == 2 + 5 // 4  # 2 CJK + 1 rest = 3


# ============================================================================
# Pricing table sanity
# ============================================================================

def test_pricing_table_has_all_required_fields():
    for model, p in BEDROCK_PRICING.items():
        for f in ("input", "output", "cache_create", "cache_read"):
            assert f in p, f"{model} missing {f}"
        assert p["input"] > 0 and p["output"] > 0


def test_pricing_table_covers_known_models():
    # acp-bridge /usage 实际记录过的 4 个 model, v0.23.0 必须命中
    for m in [
        "us.anthropic.claude-sonnet-4-6",
        "qwen.qwen3-coder-next",
        "converse/qwen.qwen3-235b-a22b-2507-v1:0",
        "deepseek.v3.2",
    ]:
        assert m in BEDROCK_PRICING, f"missing pricing for {m}"


def test_pricing_table_covers_mantle_openai_models():
    assert BEDROCK_PRICING["openai.gpt-5.5"]["input"] == 2.5e-06
    assert BEDROCK_PRICING["openai.gpt-5.5"]["output"] == 1e-05
    assert BEDROCK_PRICING["openai.gpt-5.6-sol"]["input"] == 5e-06
    assert BEDROCK_PRICING["openai.gpt-5.6-sol"]["output"] == 3e-05


def test_claude_has_cache_pricing():
    # Claude sonnet/opus 必须有 cache 单价
    for m in ("us.anthropic.claude-sonnet-4-6", "us.anthropic.claude-opus-4-6-v1"):
        p = BEDROCK_PRICING[m]
        assert p["cache_create"] is not None
        assert p["cache_read"] is not None
        assert p["cache_read"] < p["input"]  # cache 读一定比普通 input 便宜


def test_non_anthropic_no_cache_pricing():
    # 非 Anthropic 模型 cache 字段 = None (Bedrock 上不支持 prompt cache)
    for m in ("qwen.qwen3-coder-next", "deepseek.v3.2", "converse/zai.glm-5"):
        p = BEDROCK_PRICING[m]
        assert p["cache_create"] is None
        assert p["cache_read"] is None


# ============================================================================
# lookup_pricing
# ============================================================================

def test_lookup_pricing_direct_hit():
    p, k = lookup_pricing("us.anthropic.claude-sonnet-4-6")
    assert k == "us.anthropic.claude-sonnet-4-6"
    assert p["input"] == 3.3e-06


def test_lookup_pricing_strips_bedrock_prefix():
    # jobs.py 可能传 'bedrock/us.anthropic.claude-sonnet-4-6'
    p, k = lookup_pricing("bedrock/us.anthropic.claude-sonnet-4-6")
    assert k == "us.anthropic.claude-sonnet-4-6"


def test_lookup_pricing_mantle_openai_with_bedrock_prefix():
    p, k = lookup_pricing("bedrock/openai.gpt-5.6-sol")
    assert k == "openai.gpt-5.6-sol"
    assert p["output"] == 3e-05


def test_lookup_pricing_falls_back_to_default():
    p, k = lookup_pricing("unknown-model-xyz")
    assert k is None
    assert p["input"] > 0  # fallback default


def test_lookup_pricing_empty_or_none():
    p, k = lookup_pricing("")
    assert k is None
    assert p["input"] > 0


# ============================================================================
# calc_cost_v2 (新 4-token-class API)
# ============================================================================

def test_calc_cost_v2_claude_with_cache():
    # 1M input (含 800K cache_read + 100K cache_create + 100K real input) + 50K output
    cost = calc_cost_v2(
        "us.anthropic.claude-sonnet-4-6",
        input_tokens=1_000_000,
        cached_tokens=800_000,
        cache_creation_tokens=100_000,
        output_tokens=50_000,
    )
    p = BEDROCK_PRICING["us.anthropic.claude-sonnet-4-6"]
    expected = (
        100_000 * p["input"]            # 100K real input × $3.30/M = $0.33
        + 800_000 * p["cache_read"]     # 800K cache read × $0.33/M = $0.264
        + 100_000 * p["cache_create"]   # 100K cache write × $4.125/M = $0.4125
        + 50_000 * p["output"]          # 50K output × $16.50/M = $0.825
    )
    # = $1.83
    assert abs(cost - expected) < 1e-10
    assert cost > 1.8 and cost < 1.9  # sanity: ~$1.83


def test_calc_cost_v2_no_cache_model():
    # qwen 没 cache, cache_read tokens 退化按 input 单价计算
    cost = calc_cost_v2(
        "qwen.qwen3-coder-next",
        input_tokens=1_000_000,
        cached_tokens=200_000,
        cache_creation_tokens=0,
        output_tokens=50_000,
    )
    # real_input = 800K, cached_tokens 仍按 input price 算 (fallback)
    # cost = 800K * 0.5/M + 200K * 0.5/M + 50K * 1.2/M
    #      = 0.40 + 0.10 + 0.06 = $0.56
    assert abs(cost - 0.56) < 1e-6


def test_calc_cost_v2_mantle_openai_model():
    cost = calc_cost_v2(
        "openai.gpt-5.5",
        input_tokens=1_000_000,
        output_tokens=100_000,
    )
    assert abs(cost - 3.5) < 1e-10


def test_calc_cost_v2_zero_tokens():
    assert calc_cost_v2("us.anthropic.claude-sonnet-4-6", 0, 0, 0, 0) == 0.0


def test_calc_cost_v2_unknown_model_uses_default():
    cost = calc_cost_v2("unknown", 1_000_000, 0, 0, 0)
    # default $1/M input
    assert abs(cost - 1.0) < 1e-6


def test_calc_cost_v2_anti_double_counting():
    """input_tokens 包含 cache_read+cache_create, real_input = input - cache_read - cache_create.

    确保 input_tokens=1000, cached=900, cache_create=50 时,
    real_input=50, 而不是 1000+900+50 = 1950 (重复计算)."""
    cost = calc_cost_v2(
        "us.anthropic.claude-sonnet-4-6",
        input_tokens=1000,
        cached_tokens=900,
        cache_creation_tokens=50,
        output_tokens=0,
    )
    p = BEDROCK_PRICING["us.anthropic.claude-sonnet-4-6"]
    expected = 50 * p["input"] + 900 * p["cache_read"] + 50 * p["cache_create"]
    assert abs(cost - expected) < 1e-10


def test_calc_cost_v2_cached_exceeds_input_clamps_to_zero():
    """边界: 如果 cached + cache_create > input (理论上不该发生但防御性),
    real_input 不应变负."""
    cost = calc_cost_v2(
        "us.anthropic.claude-sonnet-4-6",
        input_tokens=100,
        cached_tokens=200,  # 异常: cached > total input
        cache_creation_tokens=0,
        output_tokens=0,
    )
    # real_input clamped to 0, 不会负数 cost
    p = BEDROCK_PRICING["us.anthropic.claude-sonnet-4-6"]
    expected = 200 * p["cache_read"]  # real_input = 0
    assert abs(cost - expected) < 1e-10
    assert cost > 0


# ============================================================================
# Legacy calc_cost (向后兼容, jobs.py 仍用)
# ============================================================================

def test_calc_cost_legacy_claude():
    # 旧 API 不传 cache, 按 input/output 算
    cost = calc_cost(1000, 500, "us.anthropic.claude-sonnet-4-6")
    p = BEDROCK_PRICING["us.anthropic.claude-sonnet-4-6"]
    expected = 1000 * p["input"] + 500 * p["output"]
    assert abs(cost - expected) < 1e-10


def test_calc_cost_legacy_default():
    # unknown model → default pricing
    cost = calc_cost(1000, 1000, "unknown")
    assert cost > 0


def test_calc_cost_legacy_zero():
    assert calc_cost(0, 0, "us.anthropic.claude-sonnet-4-6") == 0.0


# ============================================================================
# model_from_agent (legacy, jobs.py 用)
# ============================================================================

def test_model_from_agent_returns_lookupable_keys():
    """v0.23.0: 返回的 key 必须能被 lookup_pricing 命中."""
    for agent in ("claude", "qwen", "deepseek"):
        m = model_from_agent(agent)
        if m:
            p, k = lookup_pricing(m)
            assert k is not None, f"{agent} → {m} not in pricing table"


def test_model_from_agent_claude():
    assert model_from_agent("claude") == "us.anthropic.claude-sonnet-4-6"


def test_model_from_agent_qwen():
    assert model_from_agent("qwen") == "qwen.qwen3-coder-next"


def test_model_from_agent_deepseek():
    assert model_from_agent("deepseek-v3") == "deepseek.v3.2"


def test_model_from_agent_kiro_returns_empty():
    assert model_from_agent("kiro") == ""


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print(f"\n=== All cost tests passed ✅ ===")
