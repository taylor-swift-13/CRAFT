#!/usr/bin/env python3
"""Build a per-answer quality ledger for the released CRAFT SFT set.

This audit deliberately distinguishes verified clauses from explanatory ones.
A clause counts as a transition law only when it constrains loop-modified state;
parameter frame equalities and syntactically relational bounds do not qualify.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from paper.scripts._curation_common import record_source_and_answer  # noqa: E402
from paper.scripts.sanitize_training_prompts import (  # noqa: E402
    _constant_integer_bound,
)
from rl_pipeline.common.program import parse_program  # noqa: E402
from rl_pipeline.common.state import extract_invariants, normalize_invariant  # noqa: E402
from rl_pipeline.sampler.example_sampler import ExampleSampler  # noqa: E402


DEFAULT_SFT = ROOT / "traindata" / "craft_sft_clean.json"
DEFAULT_OUTPUT = ROOT / "paper" / "artifacts" / "sft_quality_ledger.json"
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sft", type=Path, default=DEFAULT_SFT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    records = json.loads(args.sft.read_text(encoding="utf-8"))
    items = []
    counts: Counter[str] = Counter()
    for index, record in enumerate(records):
        source, answer = record_source_and_answer(record)
        program = parse_program(source)
        modified = set(ExampleSampler._modified_vars(program))
        clauses = extract_invariants(answer)
        features = [_clause_features(clause, program, modified) for clause in clauses]
        has_transition = any(feature["transition_law"] for feature in features)
        has_progress = any(feature["informative_progress"] for feature in features)
        explained_modified = {
            variable
            for feature in features
            if feature["transition_law"]
            for variable in feature["current_modified"]
        }
        transition_coverage = (
            len(explained_modified) / len(modified) if modified else 1.0
        )
        bounds_only = all(feature["constant_bound"] for feature in features)
        frame_only_count = sum(feature["frame_only"] for feature in features)
        # An answer with no modified state (e.g. a semantically empty loop) does
        # not require an update law. All other answers do.
        quality_band = (
            "strong"
            if (not modified or has_transition) and has_progress
            else "relation_missing"
            if modified and not has_transition
            else "progress_missing"
        )
        counts[quality_band] += 1
        counts["bounds_only"] += bounds_only
        counts["answers_with_frame_only"] += frame_only_count > 0
        counts["frame_only_clauses"] += frame_only_count
        counts["partial_transition_coverage"] += bool(
            modified and has_transition and transition_coverage < 0.5
        )
        items.append(
            {
                "row": index,
                "function": program.func_name,
                "modified_variables": sorted(modified),
                "quality_band": quality_band,
                "bounds_only": bounds_only,
                "has_transition_law": has_transition,
                "explained_modified_variables": sorted(explained_modified),
                "transition_coverage": transition_coverage,
                "has_progress": has_progress,
                "clauses": features,
            }
        )
    report = {
        "schema_version": 1,
        "source": str(args.sft),
        "criteria": {
            "transition_law": (
                "non-bound clause constraining loop-modified state through another "
                "modified value, a labelled entry value, a parameter equality, or a phase law"
            ),
            "frame_only_not_counted": True,
            "strong": "has a transition law and an informative progress fact",
        },
        "summary": dict(sorted(counts.items())),
        "items": items,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
