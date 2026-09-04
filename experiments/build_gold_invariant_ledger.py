"""Build a canonical ledger of target-proving invariant sets from saved runs.

Saved ``verified`` flags are treated only as candidate-discovery hints.  Every
selected invariant set is rejudged against the current canonical benchmark and
Frama-C/WP configuration before it enters the gold ledger.

Run:
    python3 -m experiments.build_gold_invariant_ledger --workers 8
"""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
import json
from pathlib import Path
from typing import Iterable

from experiments.gpt5nano_full832.common import (
    REPO_ROOT,
    Task,
    discover_tasks,
    ensure_frama_c_available,
    judge_invariants,
)
from rl_pipeline.common.state import dedup_normalized
from rl_pipeline.common.program import parse_program
from rl_pipeline.reward.filters import auto_filter


DEFAULT_OUTPUT = REPO_ROOT / "results" / "gold_invariants_832"
SUITES = {"linear", "NLA_lipus", "Loopy"}
SKIP_PATH_PARTS = (
    "/samples/",
    "samples_manifest",
    "candidate_scores",
    "/events/api",
    "trivial_invariant",
)


def _invariant_list(value) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    if not all(isinstance(item, str) for item in value):
        return None
    normalized = dedup_normalized(value)
    return normalized or None


def _reported_successes(row: dict) -> Iterable[tuple[str, list[str]]]:
    specifications = (
        ("pass_verified", ("raw_invariants", "invariants")),
        ("combine_verified", ("combine_invariants", "combined_invariants")),
        ("native_verified", ("invariants",)),
        ("target_verified", ("invariants", "survivors")),
        (
            "verified",
            ("invariants", "survivors", "raw_invariants", "combine_invariants"),
        ),
    )
    for flag, fields in specifications:
        if row.get(flag) is not True:
            continue
        for field in fields:
            invariants = _invariant_list(row.get(field))
            if invariants is not None:
                yield f"{flag}:{field}", invariants
                break


def discover_reported_candidates(results_root: Path) -> dict[tuple[str, str], list[dict]]:
    candidates: dict[tuple[str, str], list[dict]] = defaultdict(list)
    seen: dict[tuple[str, str], set[tuple[str, ...]]] = defaultdict(set)
    for path in sorted(results_root.rglob("*.jsonl")):
        rendered = str(path)
        if any(part in rendered for part in SKIP_PATH_PARTS):
            continue
        try:
            handle = path.open(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        with handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                suite = str(row.get("suite"))
                case_id = str(row.get("case_id"))
                if suite not in SUITES or not case_id:
                    continue
                key = (suite, case_id)
                for evidence, invariants in _reported_successes(row):
                    fingerprint = tuple(invariants)
                    if fingerprint in seen[key]:
                        continue
                    seen[key].add(fingerprint)
                    candidates[key].append({
                        "invariants": invariants,
                        "requires_filter": False,
                        "source_file": str(path.relative_to(REPO_ROOT)),
                        "source_line": line_number,
                        "reported_success_field": evidence,
                    })

    # Grid ledgers store only the compose verdict and survivor count.  Recover
    # the corresponding union from the archived ordered rollout pool; the
    # canonical target-free Houdini pass is replayed during rejudging.
    for grid_path in sorted(results_root.rglob("grid_recompute.jsonl")):
        run_root = grid_path.parent
        rollout_path = run_root / "r10_results.csv"
        if not rollout_path.is_file():
            continue
        successful: dict[tuple[str, str, int], dict] = {}
        with grid_path.open(encoding="utf-8", errors="ignore") as handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("status") != "ok" or row.get("verified") is not True:
                    continue
                suite = str(row.get("suite"))
                case_id = str(row.get("case_id"))
                k = int(row.get("k", 0))
                if suite in SUITES and case_id and k > 0:
                    successful[(suite, case_id, k)] = {
                        "line": line_number,
                        "n_survivors": row.get("n_survivors"),
                    }
        if not successful:
            continue
        with rollout_path.open(encoding="utf-8", errors="ignore", newline="") as handle:
            for line_number, row in enumerate(csv.DictReader(handle), 2):
                suite = str(row.get("suite"))
                case_id = str(row.get("case_id"))
                matching = sorted(
                    key for key in successful
                    if key[0] == suite and key[1] == case_id
                )
                if not matching:
                    continue
                try:
                    rollouts = ast.literal_eval(row["rollouts"])
                except (KeyError, SyntaxError, ValueError):
                    continue
                for key in matching:
                    k = key[2]
                    invariants = dedup_normalized(
                        clause
                        for rollout in rollouts[:k]
                        for clause in rollout
                    )
                    fingerprint = tuple(invariants)
                    task_key = (suite, case_id)
                    if not invariants or fingerprint in seen[task_key]:
                        continue
                    seen[task_key].add(fingerprint)
                    candidates[task_key].append({
                        "invariants": invariants,
                        "requires_filter": True,
                        "source_file": str(rollout_path.relative_to(REPO_ROOT)),
                        "source_line": line_number,
                        "reported_success_field": f"grid_recompute:k={k}",
                    })
    for values in candidates.values():
        values.sort(key=lambda item: (
            len(item["invariants"]),
            sum(len(value) for value in item["invariants"]),
            item["source_file"],
            item["source_line"],
        ))
    return candidates


def rejudge_task(task: Task, candidates: list[dict]) -> dict:
    attempts = []
    for candidate in candidates:
        invariants = candidate["invariants"]
        if candidate.get("requires_filter"):
            masked = parse_program(task.hidden_source)
            invariants = auto_filter().filter(masked, 0, invariants, None)
        verdict = judge_invariants(task, invariants)
        attempt = {
            **candidate,
            "verified": verdict["verified"],
            "judge_error": verdict["judge_error"],
            "judge_seconds": verdict["judge_seconds"],
        }
        attempts.append(attempt)
        if verdict["verified"] is True:
            return {
                "suite": task.suite,
                "case_id": task.case_id,
                "source_sha256": task.source_sha256,
                "status": "verified",
                "gold_invariants": verdict["invariants"],
                "provenance": {
                    key: candidate[key]
                    for key in (
                        "source_file",
                        "source_line",
                        "reported_success_field",
                    )
                },
                "attempt_count": len(attempts),
                "judge_seconds": sum(item["judge_seconds"] for item in attempts),
                "failed_attempts": attempts[:-1],
            }
    return {
        "suite": task.suite,
        "case_id": task.case_id,
        "source_sha256": task.source_sha256,
        "status": "missing",
        "gold_invariants": [],
        "provenance": None,
        "attempt_count": len(attempts),
        "judge_seconds": sum(item["judge_seconds"] for item in attempts),
        "failed_attempts": attempts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "results")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")

    ensure_frama_c_available()
    tasks = discover_tasks()
    candidates = discover_reported_candidates(args.results_root)
    args.output.mkdir(parents=True, exist_ok=True)

    previous = {}
    ledger = args.output / "gold_ledger.jsonl"
    if ledger.is_file():
        previous = {
            (row["suite"], str(row["case_id"])): row
            for row in (
                json.loads(line)
                for line in ledger.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
            if row.get("status") == "verified"
        }
    rows = list(previous.values())
    pending = [
        task for task in tasks
        if (task.suite, task.case_id) not in previous
    ]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                rejudge_task,
                task,
                candidates.get((task.suite, task.case_id), []),
            ): task
            for task in pending
        }
        for index, future in enumerate(as_completed(futures), 1):
            task = futures[future]
            rows.append(future.result())
            if index % 25 == 0 or index == len(pending):
                print(f"rejudge [{index}/{len(pending)}] {task.suite}/{task.case_id}", flush=True)

    suite_order = {"linear": 0, "NLA_lipus": 1, "Loopy": 2}
    rows.sort(key=lambda row: (suite_order[row["suite"]], int(row["case_id"])))
    with ledger.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    by_suite = {}
    for suite in suite_order:
        selected = [row for row in rows if row["suite"] == suite]
        by_suite[suite] = {
            "tasks": len(selected),
            "verified_gold": sum(row["status"] == "verified" for row in selected),
            "missing_gold": sum(row["status"] != "verified" for row in selected),
        }
    summary = {
        "tasks": len(rows),
        "verified_gold": sum(row["status"] == "verified" for row in rows),
        "missing_gold": sum(row["status"] != "verified" for row in rows),
        "reported_candidate_tasks": len(candidates),
        "reported_candidate_sets": sum(map(len, candidates.values())),
        "by_suite": by_suite,
        "missing": [
            f"{row['suite']}/{row['case_id']}"
            for row in rows
            if row["status"] != "verified"
        ],
        "ledger": str(ledger.relative_to(REPO_ROOT)),
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
