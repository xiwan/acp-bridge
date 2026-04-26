"""Unit tests for src/complexity.py."""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.complexity import Complexity, TIMEOUT_MAP, estimate_complexity, should_use_async


def test_light_prompt():
    assert estimate_complexity("hello") == Complexity.LIGHT

def test_medium_keyword():
    # Short prompt + medium keyword = score 2 → LIGHT; need length boost
    prompt = "请修改这个函数的返回值，" + "详细说明" * 80  # >300 chars
    assert estimate_complexity(prompt) == Complexity.MEDIUM

def test_heavy_keyword():
    prompt = "implement a full REST API with authentication, " + "details " * 150  # >1000 chars
    assert estimate_complexity(prompt) == Complexity.HEAVY

def test_long_prompt_heavy():
    prompt = "refactor " + "x" * 1100
    assert estimate_complexity(prompt) == Complexity.HEAVY

def test_code_blocks_boost():
    prompt = "fix this:\n```py\na=1\n```\n```py\nb=2\n```\n```py\nc=3\n```"
    c = estimate_complexity(prompt)
    assert c in (Complexity.MEDIUM, Complexity.HEAVY)

def test_medium_length():
    prompt = "分析" + "a" * 350
    assert estimate_complexity(prompt) == Complexity.MEDIUM

def test_timeout_map():
    assert TIMEOUT_MAP[Complexity.LIGHT] == 30
    assert TIMEOUT_MAP[Complexity.MEDIUM] == 90
    assert TIMEOUT_MAP[Complexity.HEAVY] == 180

def test_should_use_async_heavy():
    assert should_use_async(Complexity.HEAVY) is True

def test_should_use_async_light():
    assert should_use_async(Complexity.LIGHT) is False

def test_should_use_async_explicit():
    assert should_use_async(Complexity.LIGHT, explicit_mode="async") is True
    assert should_use_async(Complexity.HEAVY, explicit_mode="sync") is False


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print(f"\n=== All complexity tests passed ✅ ===")
