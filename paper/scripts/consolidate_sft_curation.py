#!/usr/bin/env python3
"""Consolidate heterogeneous curation audits under one strict decision rule.

This script never overwrites the authoritative SFT file. It rechecks each
candidate with Frama-C/WP, scores original and candidate on the *same* sampled
example object, and emits a staged candidate dataset plus a uniform audit.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from paper.scripts._curation_common import family_rejections, record_source_and_answer  # noqa: E402
from paper.scripts.sanitize_training_prompts import (  # noqa: E402
    _rejection_reason,
)
from rl_pipeline.common.program import parse_program  # noqa: E402
from rl_pipeline.common.state import (  # noqa: E402
    dedup_normalized,
    eval_predicate,
    extract_invariants,
    normalize_invariant,
)
from rl_pipeline.reward.filters import HoudiniFilter  # noqa: E402
from rl_pipeline.sampler.example_sampler import ExampleSampler  # noqa: E402


DEFAULT_SFT = ROOT / "traindata" / "craft_sft_clean.json"
DEFAULT_OUTPUT = ROOT / "traindata" / "craft_sft_curated_stage1.json"
DEFAULT_AUDIT = ROOT / "paper" / "artifacts" / "sft_curation_stage1.json"
BATCHES = [
    ROOT / "paper" / "artifacts" / f"curation_zero_rej_batch_{index}.json"
    for index in (1, 2, 3)
]


def _items(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, list) else value["items"]


def _rejections(examples, invariants: list[str]) -> dict:
    negatives = examples.neg(0)
    groups = examples.groups(0)
    rejected_states: set[int] = set()
    for invariant in invariants:
        condition = normalize_invariant(invariant)
        for index, state in enumerate(negatives):
            if index not in rejected_states and eval_predicate(condition, state) is False:
                rejected_states.add(index)
    rejected = {
        group for group, indices in enumerate(groups)
        if any(index in rejected_states for index in indices)
    }
    return {
        "n_negative_traces": len(groups),
        "rejected": len(rejected),
        "rejected_indices": sorted(rejected),
        "coverage": len(rejected) / len(groups) if groups else None,
        "families": family_rejections(examples, rejected),
    }


def _validate(job: tuple[int, str, list[str], list[str], int, int]) -> dict:
    row, source, original, proposed, runs, seed = job
    program = parse_program(source)
    candidates = dedup_normalized(proposed)
    interface_failures = {
        clause: reason
        for clause in candidates
        if (reason := _rejection_reason(clause, program)) is not None
    }
    candidates = [clause for clause in candidates if clause not in interface_failures]
    survivors = dedup_normalized(HoudiniFilter().filter(program, 0, candidates, None))
    examples = ExampleSampler(source, n_runs=runs, seed=seed).sample()
    baseline = _rejections(examples, original)
    candidate = _rejections(examples, survivors)
    baseline_set = set(baseline["rejected_indices"])
    candidate_set = set(candidate["rejected_indices"])
    relation_before = set(baseline["families"]["relation"]["indices"])
    relation_after = set(candidate["families"]["relation"]["indices"])
    original_survives = set(dedup_normalized(original)).issubset(set(survivors))
    added = sorted(candidate_set - baseline_set)
    removed = sorted(baseline_set - candidate_set)
    relation_added = sorted(relation_after - relation_before)
    accepted = (
        original_survives
        and not removed
        and bool(relation_added)
        and len(survivors) <= 20
        and not interface_failures
    )
    return {
        "row": row,
        "decision": "accept" if accepted else "reject",
        "original": original,
        "proposed": proposed,
        "interface_failures": interface_failures,
        "wp_survivors": survivors,
        "original_survives": original_survives,
        "baseline": baseline,
        "candidate": candidate,
        "added_rejections": added,
        "removed_rejections": removed,
        "added_relation_rejections": relation_added,
        "accepted_answer": survivors if accepted else original,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sft", type=Path, default=DEFAULT_SFT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--runs", type=int, default=12)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    records = json.loads(args.sft.read_text(encoding="utf-8"))
    proposed_by_row = {}
    source_batch = {}
    for batch_index, path in enumerate(BATCHES, 1):
        for item in _items(path):
            row = int(item["row"])
            if row in proposed_by_row:
                raise ValueError(f"row {row} occurs in multiple batches")
            proposed_by_row[row] = item["wp_survivors"]
            source_batch[row] = batch_index
    jobs = []
    for row, proposed in sorted(proposed_by_row.items()):
        record = records[row]
        source, answer = record_source_and_answer(record)
        jobs.append((row, source, extract_invariants(answer), proposed, args.runs, args.seed))
    os.environ.setdefault("CRAFT_WP_TIMEOUT", "5")
    results = []
    with ProcessPoolExecutor(max_workers=args.jobs) as executor:
        futures = {executor.submit(_validate, job): job[0] for job in jobs}
        for completed, future in enumerate(as_completed(futures), 1):
            result = future.result()
            result["source_batch"] = source_batch[result["row"]]
            results.append(result)
            if completed % 20 == 0 or completed == len(futures):
                print(f"revalidated {completed}/{len(futures)}", flush=True)
    results.sort(key=lambda item: item["row"])
    staged = json.loads(json.dumps(records))
    for result in results:
        if result["decision"] != "accept":
            continue
        answer = "\n".join(
            f"loop invariant {clause};" for clause in result["accepted_answer"]
        )
        turn = next(
            turn for turn in staged[result["row"]]["conversations"]
            if turn["from"] == "gpt"
        )
        turn["value"] = answer
    report = {
        "schema_version": 1,
        "source_sft": str(args.sft),
        "staged_sft": str(args.output),
        "decision_rule": (
            "all original clauses survive WP; no baseline rejection is lost; "
            "at least one relation rejection is added; <=20 clauses; no interface violation"
        ),
        "rows_considered": len(results),
        "rows_accepted": sum(item["decision"] == "accept" for item in results),
        "rows_rejected": sum(item["decision"] == "reject" for item in results),
        "items": results,
    }
    args.output.write_text(json.dumps(staged, indent=2) + "\n", encoding="utf-8")
    args.audit.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("rows_considered", "rows_accepted", "rows_rejected")}, indent=2))


if __name__ == "__main__":
    main()
