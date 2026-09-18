"""Compare random/structured coverage on the frozen gold-augmented cohort.

Run: python3 -m experiments.paired_sampler_coverage_auc --workers 4

Positive states, candidate survivors, and verification labels are frozen.
Only random negatives are generated, from the target-hidden source and the
archived positive states; no concrete execution or verifier is rerun.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_auc_score

from experiments.current_sampler_rescore_832 import score_task
from experiments.gold_augmented_coverage_auc import (
    DEFAULT_CANDIDATES, DEFAULT_GOLD, DEFAULT_OUTPUT, DEFAULT_SAMPLES,
    DEFAULT_TRIVIAL, read_jsonl,
)
from experiments.gpt5nano_full832.common import REPO_ROOT, discover_tasks
from experiments.gpt5nano_full832.samples import (
    _decode_payload, _encode_payload, _state_to_dict,
    load_sample, load_sample_manifest,
)
from rl_pipeline.common.program import parse_program
from rl_pipeline.common.state import dedup_normalized
from rl_pipeline.sampler.example_sampler import ExampleSampler


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def paired_task(task, manifest, archived, gold, output):
    invariants = dedup_normalized(gold["gold_invariants"])
    rows = [dict(row) for row in archived
            if row.get("current_negative_score") is not None]
    fingerprints = {
        (tuple(dedup_normalized(row.get("survivors") or [])), bool(row["verified"]))
        for row in rows
    }
    if (tuple(invariants), True) not in fingerprints:
        rows.extend(score_task(task, manifest, [{
            "method": "verified_gold", "verified": True,
            "invariants": invariants, "survivors": invariants,
            "archived_negative_score": None, "archived_binary_fallback": None,
        }]))
    if not any(not bool(row["verified"]) for row in rows):
        rows.extend(score_task(task, manifest, [{
            "method": "empty_decoy", "verified": False,
            "invariants": [], "survivors": [],
            "archived_negative_score": None, "archived_binary_fallback": None,
        }]))
    assert {bool(row["verified"]) for row in rows} == {False, True}

    # Verify that the current evaluator reproduces every archived score.
    reproduced = score_task(task, manifest, rows)
    for original, check in zip(rows, reproduced, strict=True):
        assert check["score_error"] is None
        assert abs(original["current_negative_score"] -
                   check["current_negative_score"]) < 1e-12, (task, original)

    examples = load_sample(task, manifest)
    payload, _ = _decode_payload(Path(manifest["sample_artifact"]))
    sampler = ExampleSampler(task.hidden_source, **payload["sampler"],
                             negative_sampler="random")
    execution_program = parse_program(sampler._determinize_source(task.hidden_source))
    # The random branch does not consume overrun/raw_reach/capped.  Reuse the
    # exact positive states rather than repeating possibly nondeterministic runs.
    negatives, groups, families, stats = sampler._negatives(
        execution_program, examples.pos(0), [], [],
        bool(payload.get("stats", {}).get("capped", False)),
        analysis_prog=examples.program,
    )
    assert all(family == "random" for family in families)
    assert len(groups) <= stats["negative_budget"] == 60
    frozen_positives = json.dumps(payload["positives"], sort_keys=True)
    payload.update({
        "negative_sampler": "random",
        "negative_sampling_protocol": "random_from_frozen_positive_states_v1",
        "structured_sample_file_sha256": manifest["sample_file_sha256"],
        "negatives": [_state_to_dict(state) for state in negatives],
        "negative_trace_groups": groups, "negative_trace_families": families,
        "stats": {"n_pos": len(examples.pos(0)), "n_neg": len(groups), **stats},
    })
    assert json.dumps(payload["positives"], sort_keys=True) == frozen_positives
    payload.pop("sampling_seconds", None)
    path = output / "samples" / task.suite / f"{task.case_id}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded, content_hash = _encode_payload(payload)
    path.write_bytes(encoded)
    random_manifest = {
        **manifest, "sample_artifact": str(path),
        "negative_sampler": "random", "negative_trace_count": len(groups),
        "negative_state_count": len(negatives),
        "negative_family_counts": {"random": len(groups)} if groups else {},
        "zero_blockers": stats.get("zero_blockers", []),
        "sample_content_sha256": content_hash, "sample_file_sha256": digest(path),
    }
    random_rows = score_task(task, random_manifest, rows)
    paired = []
    for index, (structured, random_row) in enumerate(zip(rows, random_rows, strict=True)):
        assert random_row["score_error"] is None
        score = random_row["current_negative_score"]
        # No target-label fallback: without negatives every candidate ties.
        # Retain the original cohort and record undefined coverage explicitly.
        rank_score = float(score) if score is not None else 0.0
        paired.append({
            "suite": task.suite, "case_id": task.case_id,
            "candidate_index": index, "method": structured["method"],
            "verified": structured["verified"],
            "invariants": structured["invariants"],
            "survivors": structured["survivors"],
            "structured_score": structured["current_negative_score"],
            "random_score": score, "random_ranking_score": rank_score,
            "structured_negative_groups": structured["negative_groups"],
            "random_negative_groups": len(groups),
            "structured_rejected_groups": structured["rejected_groups"],
            "random_rejected_groups": random_row["rejected_groups"],
            "random_rejected_trace_indices": random_row["rejected_trace_indices"],
        })
    labels = [row["verified"] for row in paired]
    task_result = {
        "suite": task.suite, "case_id": task.case_id, "candidates": len(paired),
        "structured_auroc": float(roc_auc_score(labels, [r["structured_score"] for r in paired])),
        "random_auroc": float(roc_auc_score(labels, [r["random_ranking_score"] for r in paired])),
        "structured_negative_groups": len(examples.groups(0)),
        "random_negative_groups": len(groups),
        "random_zero_negative_blockers": stats.get("zero_blockers", []),
    }
    return paired, task_result, random_manifest


def summarize(tasks, bootstrap, seed):
    values = np.asarray([[r["structured_auroc"], r["random_auroc"]] for r in tasks])
    rng = np.random.default_rng(seed)
    replicates = np.asarray([
        values[rng.choice(len(values), size=len(values), replace=True)].mean(axis=0)
        for _ in range(bootstrap)
    ])
    result = {"tasks": len(tasks), "candidates": sum(r["candidates"] for r in tasks)}
    for index, sampler in enumerate(("structured", "random")):
        result[sampler] = {
            "macro_within_program_auroc": float(values[:, index].mean()),
            "macro_within_program_auroc_ci95": np.quantile(
                replicates[:, index], [0.025, 0.975]).tolist(),
        }
    result["structured_minus_random"] = {
        "macro_auroc_difference": float((values[:, 0] - values[:, 1]).mean()),
        "paired_bootstrap_ci95": np.quantile(
            replicates[:, 0] - replicates[:, 1], [0.025, 0.975]).tolist(),
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path,
                        default=REPO_ROOT / "results/04_rq4_ablations/paired_sampler_coverage_auc")
    parser.add_argument("--bootstrap", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=31)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = load_sample_manifest(DEFAULT_SAMPLES)
    gold = {(r["suite"], str(r["case_id"])): r for r in read_jsonl(DEFAULT_GOLD)
            if r.get("status") == "verified"}
    trivial = {(r["suite"], str(r["case_id"])): r for r in read_jsonl(DEFAULT_TRIVIAL)}
    archived = defaultdict(list)
    for row in read_jsonl(DEFAULT_CANDIDATES):
        archived[(row["suite"], str(row["case_id"]))].append(row)
    selected = []
    excluded = defaultdict(list)
    for task in discover_tasks():
        key = task.suite, task.case_id
        reason = ("no_verified_gold" if key not in gold else
                  "target_trivial" if trivial.get(key, {}).get("target_verified") is True else
                  "no_negative_groups" if manifest[key]["negative_trace_count"] == 0 else None)
        if reason:
            excluded[reason].append("/".join(key))
        else:
            selected.append(task)
    completed = {}
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(paired_task, task, manifest[(task.suite, task.case_id)],
                            archived[(task.suite, task.case_id)],
                            gold[(task.suite, task.case_id)], args.output): task
            for task in selected
        }
        for future in as_completed(futures):
            task = futures[future]
            completed[(task.suite, task.case_id)] = future.result()
            if len(completed) % 50 == 0 or len(completed) == len(selected):
                print(f"Scored {len(completed)}/{len(selected)} programs", flush=True)
    ordered = [completed[key] for key in sorted(completed)]
    task_rows = [item[1] for item in ordered]
    summary = summarize(task_rows, args.bootstrap, args.seed)
    reference = json.loads(DEFAULT_OUTPUT.read_text())
    assert summary["tasks"] == reference["evaluated_tasks"] == 691
    assert summary["candidates"] == reference["evaluated_candidates"] == 5711
    assert dict(excluded) == reference["excluded"]
    for metric in ("macro_within_program_auroc", "macro_within_program_auroc_ci95"):
        np.testing.assert_allclose(summary["structured"][metric], reference[metric],
                                   rtol=0, atol=1e-12)
    summary.update({
        "protocol": "Fixed gold-augmented 691-program/5711-candidate cohort; identical "
                    "survivors, labels, and frozen positive states. Random negatives "
                    "use target-hidden source, seed 0 and a 60-trace budget. "
                    "Macro AUROC and bootstrap resampling use paired programs.",
        "zero_negative_policy": "Undefined random coverage is retained as null; "
                                "all candidates tie for ranking (AUROC 0.5). "
                                "No target-label fallback or task exclusion.",
        "bootstrap_replicates": args.bootstrap, "bootstrap_seed": args.seed,
        "sampler_seed": 0, "negative_trace_budget": 60,
        "structured_reproduction": "all candidate scores and published macro AUROC/CI match",
        "excluded": dict(excluded),
        "random_no_negative_tasks": [r for r in task_rows if r["random_negative_groups"] == 0],
        "by_suite": {
            suite: summarize([r for r in task_rows if r["suite"] == suite],
                             args.bootstrap, args.seed)
            for suite in ("linear", "NLA_lipus", "Loopy")
        },
        "input_sha256": {
            str(path.relative_to(REPO_ROOT)): digest(path)
            for path in (DEFAULT_GOLD, DEFAULT_CANDIDATES, DEFAULT_TRIVIAL,
                         DEFAULT_OUTPUT, DEFAULT_SAMPLES / "samples_manifest.jsonl",
                         Path(__file__),
                         REPO_ROOT / "rl_pipeline/sampler/example_sampler.py",
                         REPO_ROOT / "rl_pipeline/common/state.py",
                         REPO_ROOT / "rl_pipeline/common/program.py",
                         REPO_ROOT / "experiments/current_sampler_rescore_832.py")
        },
    })
    for filename, rows in (
        ("candidate_scores.jsonl", [r for item in ordered for r in item[0]]),
        ("task_aurocs.jsonl", task_rows),
        ("random_samples_manifest.jsonl", [item[2] for item in ordered]),
    ):
        path = args.output / filename
        path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
        summary.setdefault("output_sha256", {})[filename] = digest(path)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: summary[key] for key in
                      ("tasks", "candidates", "structured", "random", "structured_minus_random",
                       "random_no_negative_tasks", "by_suite")}, indent=2))


if __name__ == "__main__":
    main()
