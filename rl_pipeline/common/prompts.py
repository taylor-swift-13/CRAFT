"""Single source of truth for every LLM prompt: <repo-root>/prompt/*.txt.

Edit the .txt files — never inline prompt text in code. Templates use
str.format placeholders ({program}) filled at call sites.

  generate_prompt.txt  — rollout generation (always closed-book)
  naive_prompt.txt     — deliberately minimal one-shot baseline prompt
  system_prompt.txt    — chat system prompt (vLLM / src.llm.Chatbot)
"""
from __future__ import annotations

import os

PROMPT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "prompt")


def load(name: str) -> str:
    with open(os.path.join(PROMPT_DIR, name), encoding="utf-8") as f:
        return f.read()


GENERATE_PROMPT = load("generate_prompt.txt")
NAIVE_PROMPT = load("naive_prompt.txt")


def system_prompt() -> str:
    """Return the canonical system prompt."""
    try:
        return load("system_prompt.txt")
    except OSError:
        return ""
