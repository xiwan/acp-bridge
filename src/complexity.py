"""Task complexity prediction — estimate prompt difficulty for dynamic timeout."""

from enum import Enum


class Complexity(Enum):
    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"


TIMEOUT_MAP = {
    Complexity.LIGHT: 30,
    Complexity.MEDIUM: 90,
    Complexity.HEAVY: 180,
}

_HEAVY_KW = ['重构', '实现', '开发', 'implement', 'refactor', 'build', '创建文件', '多个文件']
_MEDIUM_KW = ['修改', '调试', 'fix', 'update', 'debug', '分析']


_NEG_PATTERNS = ['不', '不要', '避免', 'no ', 'avoid', "don't", 'not ']


def estimate_complexity(prompt: str) -> Complexity:
    if not prompt:
        return Complexity.LIGHT
    score = 0
    n = len(prompt)
    if n > 1000:
        score += 3
    elif n > 300:
        score += 2
    else:
        score += 1

    has_neg = any(neg in prompt for neg in _NEG_PATTERNS)
    if not has_neg and any(kw in prompt for kw in _HEAVY_KW):
        score += 2
    elif any(kw in prompt for kw in _MEDIUM_KW):
        score += 1

    fences = prompt.count('```')
    if fences >= 4:
        score += 2
    elif fences >= 2:
        score += 1

    if score >= 5:
        return Complexity.HEAVY
    elif score >= 3:
        return Complexity.MEDIUM
    return Complexity.LIGHT


def should_use_async(complexity: Complexity, explicit_mode: str | None = None) -> bool:
    if explicit_mode:
        return explicit_mode == "async"
    return complexity == Complexity.HEAVY
