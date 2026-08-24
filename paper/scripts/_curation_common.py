"""Shared record-parsing, ledger, and scoring helpers for curation scripts."""

from __future__ import annotations

import hashlib
import json
import os
import resource
import tempfile
from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence

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


def digest_of(source: str) -> str:
    """The canonical program key every ledger in the pipeline is indexed by."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def latest_rows(path: Path, key: str = "source_sha256") -> Dict[str, dict]:
    """Load an append-only JSONL ledger, last row per key wins; {} if absent."""
    rows: Dict[str, dict] = {}
    if path.is_file():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    rows[row[key]] = row
    return rows


def quantile(values, p: float):
    """Nearest-rank quantile used consistently across curation reports."""
    values = sorted(values)
    if not values:
        return None
    return values[min(len(values) - 1, int(p * len(values)))]


def limit_memory(cap: int) -> None:
    """Worker initializer: bound the address space so a pathological program
    fails with MemoryError instead of taking the whole pool down."""
    if cap:
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))


def atomic_parquet(records, schema, output: Path) -> None:
    """Write a parquet file atomically (tempfile + rename)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        pq.write_table(pa.Table.from_pylist(records, schema=schema), temporary)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
