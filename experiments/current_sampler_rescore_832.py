"""Rescore saved Full-832 candidates with the structured sampler.

The expensive target-free Houdini survivors are reused from the immutable
Full-832 event ledger.  Program text and candidate invariants are unchanged;
only the negative example set is regenerated with the current sampler.  Final
target labels remain the common target-bearing Frama-C/WP judgments.

Run:
    python3 -m experiments.current_sampler_rescore_832 --workers 2
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
import gzip
import json
import math
from pathlib import Path

from experiments.gpt5nano_full832.common import discover_tasks
from experiments.gpt5nano_full832.samples import (
    _state_from_dict,
    load_sample_manifest,
    load_sample,
    materialize_samples,
)
from rl_pipeline.common.program import (
    bind_integer_constants,
    parse_program,
    state_external_integer_constants,
)
from rl_pipeline.common.state import (
    dedup_normalized,
    eval_predicate,
)


REPO = Path(__file__).resolve().parents[1]
ARCHIVE = REPO / "results" / "gpt5nano_full832"
DEFAULT_ROOT = REPO / "results" / "negative_sampler_relation_escape_832"
EXPECTED_TASKS = 832
EXPECTED_CANDIDATES = 6190


def load_candidates() -> dict[tuple[str, str], list[dict]]:
    with (ARCHIVE / "final_results_9methods.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        final_rows = list(csv.DictReader(handle))
    wanted = {
        (row["method"], row["suite"], row["case_id"]): row
        for row in final_rows
        if row["verified"] in {"True", "False"}
        and row["method"] != "autospec"
        and row["generation_status"] != "unsupported"
    }

    latest: dict[tuple[str, str, str], dict] = {}
    for path in sorted((ARCHIVE / "events").glob("*.jsonl")):
        for line in path.read_text(errors="ignore").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = (
                str(row.get("method")),
                str(row.get("suite")),
                str(row.get("case_id")),
            )
            if key in wanted:
                latest[key] = row

    missing = sorted(set(wanted) - set(latest))
    if missing:
        raise RuntimeError(f"missing {len(missing)} candidate event rows")
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for key, final in wanted.items():
        event = latest[key]
        if (
            event.get("negative_score_status") != "completed"
            or "score_surviving_invariants" not in event
        ):
            raise RuntimeError(f"candidate lacks reusable survivors: {key}")
        if event.get("verified") is not (final["verified"] == "True"):
            raise RuntimeError(f"candidate target label mismatch: {key}")
        if final["negative_rejection_score"] and abs(
            float(event["negative_rejection_score"])
            - float(final["negative_rejection_score"])
        ) > 1e-12:
            raise RuntimeError(f"candidate archived score mismatch: {key}")
        grouped[(key[1], key[2])].append({
            "method": key[0],
            "verified": final["verified"] == "True",
            "invariants": event.get("invariants") or [],
            "survivors": event["score_surviving_invariants"],
            "archived_negative_score": (
                float(final["negative_rejection_score"])
                if final["negative_rejection_score"] else None
            ),
            "archived_binary_fallback": (
                float(final["binary_frama_c_validation"])
                if final["binary_frama_c_validation"] else None
            ),
        })
    if sum(map(len, grouped.values())) != EXPECTED_CANDIDATES:
        raise RuntimeError("candidate ledger is not the expected 6190 rows")
    return grouped


def score_task(task, manifest_row: dict, candidates: list[dict]) -> list[dict]:
    if manifest_row["sample_status"] != "completed":
        return [{
            **candidate,
            "suite": task.suite,
            "case_id": task.case_id,
            "score_error": manifest_row.get("sample_error"),
        } for candidate in candidates]
    examples = load_sample(task, manifest_row)
    negatives = examples.neg(0)
    groups = examples.groups(0)
    families = examples.group_families(0)
    constants = state_external_integer_constants(
        parse_program(task.hidden_source)
    )
    if len(families) != len(groups):
        raise RuntimeError(
            f"negative family/group mismatch for {task.suite}/{task.case_id}"
        )
    family_counts = {
        family: families.count(family)
        for family in sorted(set(families))
    }
    rows = []
    for candidate in candidates:
        raw = dedup_normalized(candidate["invariants"][:20])
        survivors = dedup_normalized(candidate["survivors"])
        if groups:
            rejected_states = set()
            for invariant in survivors:
                condition = bind_integer_constants(invariant, constants)
                for index, state in enumerate(negatives):
                    if index not in rejected_states and (
                        eval_predicate(condition, state) is False
                    ):
                        rejected_states.add(index)
            rejected_group_indices = {
                group_index
                for group_index, group in enumerate(groups)
                if any(index in rejected_states for index in group)
            }
            rejected_groups = len(rejected_group_indices)
            score = rejected_groups / len(groups)
            family_rejected = {
                family: sum(
                    group_index in rejected_group_indices
                    for group_index, value in enumerate(families)
                    if value == family
                )
                for family in family_counts
            }
            family_scores = {
                family: family_rejected[family] / count
                for family, count in family_counts.items()
            }
            fallback = None
        else:
            rejected_group_indices = set()
            rejected_groups = None
            score = None
            family_rejected = {}
            family_scores = {}
            fallback = None
        rows.append({
            **candidate,
            "suite": task.suite,
            "case_id": task.case_id,
            "positive_states": len(examples.pos(0)),
            "negative_states": len(negatives),
            "negative_groups": len(groups),
            "rejected_groups": rejected_groups,
            "rejected_trace_indices": sorted(rejected_group_indices),
            "negative_family_counts": family_counts,
            "rejected_by_family": family_rejected,
            "negative_family_scores": family_scores,
            "current_negative_score": score,
            "current_binary_fallback": fallback,
            "raw_invariant_count": len(raw),
            "survivor_count": len(survivors),
            "score_error": None,
        })
    return rows


def validate_archived_scores(rows: list[dict], count: int = 100) -> dict:
    """Reproduce a deterministic sample of old scores with the same evaluator."""
    archived_manifest = {
        (row["suite"], str(row["case_id"])): row
        for row in (
            json.loads(line)
            for line in (ARCHIVE / "samples_manifest.jsonl").read_text().splitlines()
            if line.strip()
        )
    }
    matched = [
        row for row in rows
        if row["archived_negative_score"] is not None
    ]
    step = max(1, len(matched) // count)
    selected = matched[::step][:count]
    cache = {}
    failures = []
    for row in selected:
        key = (row["suite"], str(row["case_id"]))
        if key not in cache:
            path = Path(archived_manifest[key]["sample_artifact"])
            if not path.is_file():
                path = ARCHIVE / "samples" / key[0] / f"{key[1]}.json.gz"
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            cache[key] = (
                [_state_from_dict(item) for item in payload["negatives"]],
                payload["negative_trace_groups"],
            )
        negatives, groups = cache[key]
        rejected = set()
        for invariant in row["survivors"]:
            for index, state in enumerate(negatives):
                if index not in rejected and (
                    eval_predicate(invariant, state) is False
                ):
                    rejected.add(index)
        score = sum(
            any(index in rejected for index in group)
            for group in groups
        ) / len(groups)
        if not math.isclose(
            score,
            row["archived_negative_score"],
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            failures.append({
                "suite": row["suite"],
                "case_id": row["case_id"],
                "method": row["method"],
                "recomputed": score,
                "archived": row["archived_negative_score"],
            })
    return {
        "selection": f"{count} deterministic evenly spaced matched candidate rows",
        "checked": len(selected),
        "exact_within_1e-15": len(selected) - len(failures),
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--reuse-samples",
        action="store_true",
        help="validate and load the completed manifest without materializing samples",
    )
    parser.add_argument(
        "--samples-only",
        action="store_true",
        help="materialize the sample manifest and stop before candidate rescoring",
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")

    if args.reuse_samples and args.samples_only:
        parser.error("--reuse-samples and --samples-only are mutually exclusive")

    manifest = (
        load_sample_manifest(args.results_root)
        if args.reuse_samples
        else materialize_samples(args.results_root, workers=args.workers)
    )
    if args.samples_only:
        print(args.results_root / "samples_manifest.jsonl")
        return int(any(
            row["sample_status"] != "completed" for row in manifest.values()
        ))
    tasks = discover_tasks()
    candidates = load_candidates()
    if len(tasks) != EXPECTED_TASKS or len(manifest) != EXPECTED_TASKS:
        raise RuntimeError("expected a complete 832-task sample manifest")

    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                score_task,
                task,
                manifest[(task.suite, task.case_id)],
                candidates[(task.suite, task.case_id)],
            ): task
            for task in tasks
        }
        for index, future in enumerate(as_completed(futures), 1):
            task = futures[future]
            rows.extend(future.result())
            if index % 25 == 0 or index == len(tasks):
                print(
                    f"scores [{index}/{len(tasks)}] "
                    f"{task.suite}/{task.case_id}",
                    flush=True,
                )
    rows.sort(key=lambda row: (
        row["suite"], int(row["case_id"]), row["method"]
    ))
    output = args.results_root / "candidate_scores.jsonl"
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    completed = [row for row in rows if row.get("score_error") is None]
    reproduction = validate_archived_scores(completed)
    summary = {
        "protocol": "structured_random_fill_full832_rescore_v2",
        "predicate_evaluator": (
            "c_identifier_keyword_and_integer_constant_safe_v3"
        ),
        "tasks": len(tasks),
        "candidate_rows": len(rows),
        "completed_candidate_rows": len(completed),
        "errors": len(rows) - len(completed),
        "tasks_with_negatives": sum(
            row["negative_trace_count"] > 0 for row in manifest.values()
            if row["sample_status"] == "completed"
        ),
        "tasks_without_negatives": sum(
            row["negative_trace_count"] == 0 for row in manifest.values()
            if row["sample_status"] == "completed"
        ),
        "sample_errors": sum(
            row["sample_status"] != "completed" for row in manifest.values()
        ),
        "archived_score_compatibility": reproduction,
        "survivor_provenance": (
            "Reused exact target-free Houdini survivors from the archived "
            "Full-832 event ledger; only negative states were regenerated."
        ),
        "output": str(output.resolve().relative_to(REPO)),
    }
    summary_path = args.results_root / "rescore_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )
    print(output)
    print(summary_path)
    print(json.dumps(summary, sort_keys=True))
    return int(bool(
        summary["errors"]
        or summary["sample_errors"]
    ))


if __name__ == "__main__":
    raise SystemExit(main())
