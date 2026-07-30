"""Single source of truth for every LLM prompt: <repo-root>/prompt/*.txt.

Edit the .txt files — never inline prompt text in code.  Templates use
str.format placeholders ({program}, {feedback}) filled at call sites.

  generate_prompt.txt  — rollout generation (always closed-book)
  refine_prompt.txt    — m-round refine; STATELESS by design: it sees only
                         program + current pool verdicts (no round number, no
                         history), so "train one round, infer many" stays
                         in-distribution.  Shared VERBATIM by inference and RL
                         training — both must format THIS template.
  system_prompt.txt    — chat system prompt (vLLM / src.llm.Chatbot)
"""
from __future__ import annotations

import os
import random
from typing import Optional

PROMPT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "prompt")


def load(name: str) -> str:
    with open(os.path.join(PROMPT_DIR, name), encoding="utf-8") as f:
        return f.read()


GENERATE_PROMPT = load("generate_prompt.txt")
REFINE_PROMPT = load("refine_prompt.txt")


def system_prompt(
    *,
    shuffle_rules: bool = False,
    seed: Optional[int] = None,
) -> str:
    """Return the system prompt, optionally shuffling only its RULES bullets.

    A shuffled prompt requires an explicit group-level seed. All rollouts in
    one GRPO group must use the same rendered system prompt.
    """
    try:
        prompt = load("system_prompt.txt")
    except OSError:
        return ""
    if not shuffle_rules:
        return prompt
    if seed is None:
        raise ValueError(
            "shuffle_rules=True requires one explicit seed per GRPO group"
        )

    rules_marker = "## RULES"
    output_marker = "## OUTPUT"
    if rules_marker not in prompt or output_marker not in prompt:
        raise ValueError(
            "system_prompt.txt must contain ## RULES and ## OUTPUT sections"
        )
    prefix, rules_and_output = prompt.split(rules_marker, 1)
    rules_section, suffix = rules_and_output.split(output_marker, 1)
    rules = [
        line
        for line in rules_section.strip().splitlines()
        if line.strip()
    ]
    if not rules or any(not line.startswith("- ") for line in rules):
        raise ValueError(
            "every non-empty line in the ## RULES section must be a bullet"
        )
    random.Random(seed).shuffle(rules)
    rendered_rules = "\n".join(rules)
    return (
        f"{prefix}{rules_marker}\n\n"
        f"{rendered_rules}\n\n"
        f"{output_marker}{suffix}"
    )
