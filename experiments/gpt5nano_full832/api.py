from __future__ import annotations

import os
import time
from typing import Optional

import openai

from rl_pipeline.common import prompts

from .common import load_protocol, sha256_text, token_fields


class RecordingChat:
    """Stateless OpenAI-compatible chat callable with exact usage recording."""

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        max_completion_tokens: Optional[int] = None,
        retries: int = 5,
    ):
        protocol = load_protocol()
        self.model = model or protocol["model"]
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
        messages = [
            {"role": "system", "content": prompts.system_prompt()},
            {"role": "user", "content": user_prompt},
        ]
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": load_protocol()["generation"]["temperature"],
            "top_p": load_protocol()["generation"]["top_p"],
            "max_tokens": self.max_completion_tokens,
        }
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

        choice = response.choices[0]
        text = self._content(choice.message).strip()
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
        total_tokens = getattr(usage, "total_tokens", None) if usage else None
        self.records.append({
            "prompt_sha256": sha256_text(user_prompt),
            "response": text,
            "finish_reason": getattr(choice, "finish_reason", None),
            "seconds": time.perf_counter() - started,
            **token_fields(
                prompt=prompt_tokens,
                completion=completion_tokens,
                total=total_tokens,
                calls=1,
                accounting="exact" if usage else "unavailable",
            ),
        })
        return text

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
