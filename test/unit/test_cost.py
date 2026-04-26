"""Unit tests for src/cost.py."""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.cost import estimate_tokens, calc_cost, model_from_agent, BEDROCK_PRICING


def test_estimate_tokens_empty():
    assert estimate_tokens("") == 0

def test_estimate_tokens_english():
    t = estimate_tokens("hello world", "claude-sonnet-4")
    assert t > 0

def test_estimate_tokens_qwen():
    # New unified formula: CJK chars count as 1 token each
    text = "你好世界测试"
    t = estimate_tokens(text, "qwen-max")
    assert t == 6  # 6 CJK chars = 6 tokens

def test_estimate_tokens_default():
    t = estimate_tokens("test", "unknown-model")
    assert t >= 1

def test_calc_cost_claude():
    cost = calc_cost(1000, 500, "claude-sonnet-4")
    expected = 1000 * 3.0/1e6 + 500 * 15.0/1e6
    assert abs(cost - expected) < 1e-10

def test_calc_cost_default():
    cost = calc_cost(1000, 1000, "unknown")
    assert cost == 1000 * 1.0/1e6 + 1000 * 3.0/1e6

def test_calc_cost_zero():
    assert calc_cost(0, 0, "claude-sonnet-4") == 0.0

def test_model_from_agent():
    assert model_from_agent("claude") == "claude-sonnet-4"
    assert model_from_agent("qwen") == "qwen-max"
    assert model_from_agent("harness") == "claude-haiku"
    assert model_from_agent("opencode") == "claude-haiku"
    assert model_from_agent("codex") == "claude-haiku"
    assert model_from_agent("kiro") == ""

def test_estimate_tokens_cjk_mixed():
    # "hello你好" = 5 english bytes + 2 CJK chars (6 bytes)
    t = estimate_tokens("hello你好")
    assert t == 2 + 5 // 4  # 2 CJK + 1 rest = 3

def test_pricing_table():
    for model, p in BEDROCK_PRICING.items():
        assert "input" in p and "output" in p
        assert p["input"] > 0 and p["output"] > 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print(f"\n=== All cost tests passed ✅ ===")
