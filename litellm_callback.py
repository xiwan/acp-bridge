"""LiteLLM Custom Callback — posts usage (incl. cache tokens) to ACP Bridge."""

import httpx
from litellm.integrations.custom_logger import CustomLogger

CALLBACK_URL = "http://127.0.0.1:18010/internal/llm-callback"


class AcpBridgeLogger(CustomLogger):
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
