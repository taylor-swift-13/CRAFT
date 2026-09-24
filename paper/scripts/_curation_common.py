"""Shared record-parsing, ledger, and scoring helpers for curation scripts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import resource
import tempfile
from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence

from rl_pipeline.common.prompts import PROGRAM_MARKER
from rl_pipeline.common.state import _constant_integer_bound, normalize_invariant

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


_AT = re.compile(r"\\at\(\s*([A-Za-z_]\w*)\s*,\s*(Pre|LoopEntry)\s*\)")
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")


def _current_variables(clause: str, program_variables: set[str]) -> set[str]:
    return set(_IDENTIFIER.findall(_AT.sub(" ", clause))) & program_variables


def _labelled_variables(clause: str) -> set[tuple[str, str]]:
    return set(_AT.findall(clause))


def _is_equality(clause: str) -> bool:
    return "==" in clause.replace("==>", "").replace("<==>", "")


def _clause_features(clause: str, program, modified: set[str]) -> dict:
    clause = normalize_invariant(clause)
    variables = set(program.pre_vars)
    current = _current_variables(clause, variables)
    labelled = _labelled_variables(clause)
    current_modified = current & modified
    labelled_modified = {name for name, _label in labelled} & modified
    constant_bound = _constant_integer_bound(clause) is not None
    equality = _is_equality(clause)
    modular = "%" in clause
    implication = "==>" in clause
    polynomial = equality and any(operator in clause for operator in ("*", "/", "%", "<<", ">>"))
    frame_only = bool(labelled) and not current_modified and not labelled_modified
    entry_relation = bool(current_modified & labelled_modified)
    multi_modified_relation = len(current_modified | labelled_modified) >= 2
    modified_parameter_relation = bool(current_modified) and bool(
        (current | {name for name, _ in labelled}) - modified
    )
    phase_relation = bool(current_modified) and (modular or "||" in clause or implication)
    transition_law = (
        not constant_bound
        and not frame_only
        and (
            entry_relation
            or multi_modified_relation
            or (equality and modified_parameter_relation)
            or phase_relation
        )
    )
    informative_progress = bool(current_modified) and (
        constant_bound or (not equality and any(op in clause for op in ("<=", ">=", "<", ">")))
    )
    return {
        "clause": clause,
        "current_variables": sorted(current),
        "labelled_variables": [list(item) for item in sorted(labelled)],
        "current_modified": sorted(current_modified),
        "constant_bound": constant_bound,
        "equality": equality,
        "polynomial": polynomial,
        "modular": modular,
        "implication": implication,
        "frame_only": frame_only,
        "transition_law": transition_law,
        "informative_progress": informative_progress,
    }
