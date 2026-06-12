"""LiteLLM Custom Callback — posts usage (incl. cache tokens) to ACP Bridge."""

import httpx
from litellm.integrations.custom_logger import CustomLogger

CALLBACK_URL = "http://127.0.0.1:18010/internal/llm-callback"

# Models that reject thinking.type="enabled"
_FABLE_MODELS = {"us.anthropic.claude-fable-5", "anthropic.claude-fable-5"}


def _patch_thinking(data: dict) -> dict:
    """Convert thinking.type=enabled to adaptive for Fable 5 models."""
    model = data.get("model", "")
    if not any(m in model for m in _FABLE_MODELS):
        return data
    # Patch top-level thinking
    t = data.get("thinking")
    if isinstance(t, dict) and t.get("type") == "enabled":
        t["type"] = "adaptive"
    # Patch in optional_params
    op = data.get("optional_params")
    if isinstance(op, dict):
        t2 = op.get("thinking")
        if isinstance(t2, dict) and t2.get("type") == "enabled":
            t2["type"] = "adaptive"
    # Patch in kwargs
    kw = data.get("kwargs")
    if isinstance(kw, dict):
        t3 = kw.get("thinking")
        if isinstance(t3, dict) and t3.get("type") == "enabled":
            t3["type"] = "adaptive"
    return data


class AcpBridgeLogger(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        return _patch_thinking(data)

    async def async_log_pre_api_call(self, model, messages, kwargs):
        """Patch thinking right before the actual API call."""
        _patch_thinking(kwargs)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._log(kwargs, response_obj, start_time, end_time)

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._log(kwargs, response_obj, start_time, end_time)

    def _log(self, kwargs, response_obj, start_time, end_time):
        model = kwargs.get("model", "")
        usage = getattr(response_obj, "usage", None)
        if not usage:
            return

        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        total_tokens = getattr(usage, "total_tokens", 0) or 0

        # Bedrock returns cache_read_input_tokens / cache_creation_input_tokens
        # OpenAI returns prompt_tokens_details.cached_tokens
        cached_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0
        if not cached_tokens:
            ptd = getattr(usage, "prompt_tokens_details", None)
            if ptd:
                cached_tokens = getattr(ptd, "cached_tokens", 0) or 0
                if not cached_tokens and isinstance(ptd, dict):
                    cached_tokens = ptd.get("cached_tokens", 0) or 0

        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0

        duration = (end_time - start_time).total_seconds() if start_time and end_time else 0.0

        payload = {
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cached_tokens": cached_tokens,
            "cache_creation_tokens": cache_creation,
            "response_time": duration,
        }

        try:
            httpx.post(CALLBACK_URL, json=payload, timeout=5)
        except Exception:
            pass


proxy_handler_instance = AcpBridgeLogger()
