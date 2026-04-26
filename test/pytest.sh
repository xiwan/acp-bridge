#!/bin/bash
# ACP Bridge 单元测试 — pytest
# 用法: bash test/pytest.sh [额外pytest参数]
# 示例: bash test/pytest.sh -v
#       bash test/pytest.sh -k "test_agents"

set -uo pipefail

TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$TEST_DIR/.."

uv run python -m pytest test/unit/ "$@"
