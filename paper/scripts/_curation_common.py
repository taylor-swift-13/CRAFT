"""Shared record-parsing helpers for SFT curation scripts."""

from __future__ import annotations

from paper.scripts.sanitize_training_prompts import PROGRAM_MARKER


def record_source_and_answer(record: dict) -> tuple[str, str]:
    """Return (target-hidden source, current answer) for one SFT conversation record."""
    human = next(turn["value"] for turn in record["conversations"] if turn["from"] == "human")
    answer = next(turn["value"] for turn in record["conversations"] if turn["from"] == "gpt")
    return human.split(PROGRAM_MARKER, 1)[1], answer
