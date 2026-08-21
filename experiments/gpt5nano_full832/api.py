from __future__ import annotations

import os
import time
from typing import Optional

import openai

from rl_pipeline.common import prompts

from .common import load_protocol, sha256_text, token_fields


def _craft_env(name: str) -> Optional[str]:
    """Read the CRAFT setting, with the pre-rename key as a compatibility alias."""
    return os.environ.get(f"CRAFT_{name}") or os.environ.get(f"LOOPGYM_{name}")


class RecordingChat:
    """Stateless OpenAI-compatible chat callable with exact usage recording."""

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        system_prompt: Optional[str] = None,
        use_default_system_prompt: bool = True,
        max_completion_tokens: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
        enable_thinking: Optional[bool] = None,
        retries: int = 5,
    ):
        protocol = load_protocol()
        self.model = model or _craft_env("MODEL") or protocol["model"]
        self.base_url = (
            base_url
            or os.environ.get("OPENAI_BASE_URL")
            or "https://yunwu.ai/v1"
        )
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError("OPENAI_API_KEY is required")
        self.client = openai.OpenAI(base_url=self.base_url, api_key=key)
        self.max_completion_tokens = (
            max_completion_tokens
            or protocol["generation"]["max_completion_tokens"]
        )
        configured_reasoning_effort = (
            reasoning_effort
            or _craft_env("REASONING_EFFORT")
            or protocol["generation"].get("reasoning_effort")
        )
        self.reasoning_effort = (
            None
            if str(configured_reasoning_effort).lower() in {"default", "omit", "none"}
            else configured_reasoning_effort
        )
        configured_enable_thinking = _craft_env("ENABLE_THINKING")
        if enable_thinking is not None:
            self.enable_thinking = bool(enable_thinking)
        elif configured_enable_thinking is None:
            self.enable_thinking = None
        elif configured_enable_thinking.strip().lower() in {
            "0", "false", "no", "off",
        }:
            self.enable_thinking = False
        elif configured_enable_thinking.strip().lower() in {
            "1", "true", "yes", "on",
        }:
            self.enable_thinking = True
        else:
            raise ValueError(
                "CRAFT_ENABLE_THINKING must be a boolean value"
            )
        self.system_prompt = (
            prompts.system_prompt()
            if use_default_system_prompt
            else (system_prompt or "")
        )
        self.retries = retries
        self.records: list[dict] = []

    @staticmethod
    def _content(message) -> str:
        content = getattr(message, "content", None)
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif getattr(item, "type", None) == "text":
                    parts.append(str(getattr(item, "text", "") or ""))
            return "\n".join(parts)
        return str(content)

    def chat(self, user_prompt: str) -> str:
        return self.chat_n(user_prompt, 1)[0]

    def chat_n(self, user_prompt: str, n: int) -> list[str]:
        """Return ``n`` choices from one API request and record usage once."""
        if n < 1:
            raise ValueError("n must be at least 1")
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": load_protocol()["generation"]["temperature"],
            "top_p": load_protocol()["generation"]["top_p"],
            "max_tokens": self.max_completion_tokens,
            "n": n,
        }
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if self.enable_thinking is not None:
            extra_body = {"enable_thinking": self.enable_thinking}
            if "deepseek" in self.model.lower():
                # DeepSeek V3.2-family models ignore enable_thinking and
                # default to thinking mode at high effort; the documented
                # control is the `thinking` object.
                # https://api-docs.deepseek.com/guides/thinking_mode/
                extra_body["thinking"] = {
                    "type": "enabled" if self.enable_thinking else "disabled"
                }
            kwargs["extra_body"] = extra_body
        started = time.perf_counter()
        last_error = None
        response = None
        for attempt in range(self.retries):
            try:
                response = self.client.chat.completions.create(**kwargs)
                break
            except openai.AuthenticationError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(min(2 ** attempt, 16))
        if response is None:
            raise RuntimeError(
                f"OpenAI-compatible request failed after {self.retries} attempts: "
                f"{last_error}"
            )

        choices = list(response.choices)
        if len(choices) != n:
            raise RuntimeError(
                f"OpenAI-compatible request asked for {n} choices but returned "
                f"{len(choices)}"
            )
        texts = [self._content(choice.message).strip() for choice in choices]
        finish_reasons = [getattr(choice, "finish_reason", None) for choice in choices]
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
        total_tokens = getattr(usage, "total_tokens", None) if usage else None
        record = {
            "prompt_sha256": sha256_text(user_prompt),
            "system_prompt_present": bool(self.system_prompt),
            "system_prompt_sha256": (
                sha256_text(self.system_prompt) if self.system_prompt else None
            ),
            "reasoning_effort": self.reasoning_effort,
            "enable_thinking": self.enable_thinking,
            "requested_n": n,
            "choice_count": len(texts),
            "responses": texts,
            "finish_reasons": finish_reasons,
            "seconds": time.perf_counter() - started,
            **token_fields(
                prompt=prompt_tokens,
                completion=completion_tokens,
                total=total_tokens,
                calls=1,
                accounting="exact" if usage else "unavailable",
            ),
        }
        # Preserve the historical scalar fields for existing n=1 consumers.
        if n == 1:
            record.update({
                "response": texts[0],
                "finish_reason": finish_reasons[0],
            })
        self.records.append(record)
        return texts

    def usage(self) -> dict:
        exact = bool(self.records) and all(
            record.get("token_accounting") == "exact" for record in self.records
        )

        def total(field):
            values = [record.get(field) for record in self.records]
            return sum(values) if values and all(v is not None for v in values) else None

        return token_fields(
            prompt=total("prompt_tokens"),
            completion=total("completion_tokens"),
            total=total("total_tokens"),
            calls=len(self.records),
            accounting="exact" if exact else "unavailable",
        )
