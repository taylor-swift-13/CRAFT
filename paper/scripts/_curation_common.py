"""Shared record-parsing and scoring helpers for SFT curation scripts."""

from __future__ import annotations

from typing import Iterable, Sequence

from paper.scripts.sanitize_training_prompts import PROGRAM_MARKER

# Curation ledgers label the sampler's ``escape`` family ``post_exit``.
LEDGER_FAMILY = {"relation": "relation", "escape": "post_exit", "range": "range"}
LEDGER_FAMILIES = ("relation", "post_exit", "range", "frame")


def record_source_and_answer(record: dict) -> tuple[str, str]:
    """Return (target-hidden source, current answer) for one SFT conversation record."""
    human = next(turn["value"] for turn in record["conversations"] if turn["from"] == "human")
    answer = next(turn["value"] for turn in record["conversations"] if turn["from"] == "gpt")
    return human.split(PROGRAM_MARKER, 1)[1], answer


def family_rejections(
    examples,
    rejected_groups: Iterable[int],
    families: Sequence[str] = LEDGER_FAMILIES,
) -> dict[str, dict]:
    """Summarize rejected negative trace groups per ledger family.

    Indices are family-local (position among that family's traces).  Every
    requested family is present even when the sampler emitted no trace for it:
    downstream stage scripts index ``families["frame"]`` directly.
    """
    rejected = set(rejected_groups)
    members: dict[str, list[int]] = {family: [] for family in families}
    for group, family in enumerate(examples.group_families(0)):
        members.setdefault(LEDGER_FAMILY.get(family, family), []).append(group)
    out = {}
    for family, groups in members.items():
        indices = [local for local, group in enumerate(groups) if group in rejected]
        out[family] = {"total": len(groups), "rejected": len(indices), "indices": indices}
    return out
