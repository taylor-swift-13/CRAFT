"""Rejudge high-scoring relation/escape candidates without an ACSL frame.

The frozen Full-832 ledger historically judged every non-invariant WP goal as
part of the target.  That accidentally includes ``Goal Loop assigns`` emitted
by the automatically inserted frame clause.  This audit instead evaluates the
exact invariant set used by negative scoring, emits no ``loop assigns`` clause,
and distinguishes invariant induction from the benchmark ``Goal Assertion``.

No model calls or candidate changes are made.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from experiments.gpt5nano_full832.common import (
    discover_tasks,
    ensure_frama_c_available,
)
from rl_pipeline.common.program import iter_acsl_clauses, parse_program
from rl_pipeline.common.state import dedup_normalized
from rl_pipeline.reward import annotate
from src.output_verify import OutputVerifier


REPO = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO / "results" / "negative_sampler_relation_escape_832"
_SEPARATOR = "-" * 60
_FRAME_LINE = re.compile(r"(?m)^\s*loop\s+assigns\s+[^;]+;\s*$")


@dataclass(frozen=True)
class Work:
    suite: str
    case_id: str
    source_path: Path
    invariants: tuple[str, ...]


def _valid(goal: str) -> bool:
    return OutputVerifier._is_content_valid(goal)


def _goal_blocks(stdout: str) -> list[str]:
    return [
        block.strip()
        for block in stdout.split(_SEPARATOR)
        if block.strip().startswith("Goal ")
    ]


def _run(
    work: Work,
    frama_c: str,
    process_timeout: int,
    provers: str,
) -> dict:
    started = time.perf_counter()
    original = work.source_path.read_text(errors="ignore")
    target_count = len(list(iter_acsl_clauses(original, "assert")))
    program = parse_program(original)
    annotated = annotate.build_annotated(program, list(work.invariants), 0)
    no_frame, removed = _FRAME_LINE.subn("", annotated)
    if removed > 1:
        raise RuntimeError(f"unexpected frame count: {removed}")

    with tempfile.TemporaryDirectory(prefix="craft_rejudge_no_frame_") as tmp:
        cpath = Path(tmp) / "program.c"
        cpath.write_text(no_frame, encoding="utf-8")
        command = [
            frama_c,
            "-wp",
            "-wp-print",
            "-wp-timeout",
            "5",
            "-wp-par",
            "8",
            "-wp-prover",
            provers,
            "-wp-model",
            "Typed",
            "-wp-prop=-@terminates,-missing_return",
            str(cpath),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=process_timeout,
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            status = "completed"
            error = None
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            output = stdout + stderr
            completed = None
            status = "timeout"
            error = f"process_timeout_{process_timeout}s"

    blocks = _goal_blocks(output)
    invariant_goals = OutputVerifier.filter_invariant(blocks)
    invariant_results = OutputVerifier().check_valid_pairs(invariant_goals)
    all_assertion_goals = [
        block for block in blocks
        if block.startswith("Goal Assertion")
    ]
    implicit_assertion_goals = [
        block for block in all_assertion_goals
        if block.startswith("Goal Assertion 'missing_return'")
    ]
    assertion_goals = [
        block for block in all_assertion_goals
        if block not in implicit_assertion_goals
    ]
    frame_goals = [
        block for block in blocks
        if block.startswith("Goal Loop assigns")
    ]
    expected_invariants = len(work.invariants)
    syntax_ok = bool(
        completed is not None
        and completed.returncode == 0
        and "Frama-C aborted" not in output
        and "invalid user input" not in output
        and "[kernel:annot-error]" not in output
    )
    invariants_valid = bool(
        syntax_ok
        and len(invariant_results) == expected_invariants
        and all(invariant_results)
    )
    target_verified = bool(
        syntax_ok
        and target_count > 0
        and len(assertion_goals) == target_count
        and all(map(_valid, assertion_goals))
    )
    return {
        "suite": work.suite,
        "case_id": work.case_id,
        "invariants": list(work.invariants),
        "invariant_count": expected_invariants,
        "target_count": target_count,
        "status": status,
        "syntax_ok": syntax_ok,
        "invariant_result_count": len(invariant_results),
        "invariants_valid": invariants_valid,
        "assertion_goal_count": len(assertion_goals),
        "implicit_assertion_goal_count": len(implicit_assertion_goals),
        "target_verified": target_verified,
        "verified": invariants_valid and target_verified,
        "frame_removed": removed == 1,
        "unexpected_frame_goal_count": len(frame_goals),
        "failed_goal_heads": [
            block.splitlines()[0] for block in blocks if not _valid(block)
        ],
        "seconds": time.perf_counter() - started,
        "error": error,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument(
        "--candidate-file",
        type=Path,
        help="candidate JSONL (default: <results-root>/candidate_scores.jsonl)",
    )
    parser.add_argument("--score-field", default="current_negative_score")
    parser.add_argument("--min-score", type=float, default=0.9)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--process-timeout", type=int, default=90)
    parser.add_argument("--provers", default="alt-ergo,z3")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")

    frama_c = str(ensure_frama_c_available())
    tasks = {
        (task.suite, task.case_id): task for task in discover_tasks()
    }
    candidate_path = args.candidate_file or (
        args.results_root / "candidate_scores.jsonl"
    )
    candidates = [
        json.loads(line)
        for line in candidate_path
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    selected = [
        row for row in candidates
        if row.get("verified") is False
        and row.get(args.score_field) is not None
        and float(row[args.score_field]) >= args.min_score
    ]
    output_stem = f"{candidate_path.stem}_no_frame_high_score_rejudge"
    output = args.results_root / f"{output_stem}.jsonl"

    unique: dict[tuple, Work] = {}
    for row in selected:
        suite, case_id = str(row["suite"]), str(row["case_id"])
        invariants = tuple(dedup_normalized(row.get("survivors") or []))
        key = (suite, case_id, invariants)
        task = tasks[(suite, case_id)]
        unique[key] = Work(suite, case_id, task.source_path, invariants)

    judged: dict[tuple, dict] = {}
    if output.is_file():
        for line in output.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            cached = json.loads(line)
            key = (
                str(cached["suite"]),
                str(cached["case_id"]),
                tuple(dedup_normalized(cached.get("invariants") or [])),
            )
            if key not in unique or cached.get("status") != "completed":
                continue
            for field in (
                "method", "negative_score", "archived_verified"
            ):
                cached.pop(field, None)
            judged[key] = cached
    pending = {
        key: work for key, work in unique.items() if key not in judged
    }
    print(
        f"rejudge unique={len(unique)} reused={len(judged)} "
        f"pending={len(pending)}",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                _run, work, frama_c, args.process_timeout, args.provers
            ): key
            for key, work in pending.items()
        }
        for index, future in enumerate(as_completed(futures), 1):
            key = futures[future]
            try:
                judged[key] = future.result()
            except Exception as exc:
                work = unique[key]
                judged[key] = {
                    "suite": work.suite,
                    "case_id": work.case_id,
                    "invariants": list(work.invariants),
                    "status": "failed",
                    "verified": False,
                    "target_verified": False,
                    "invariants_valid": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            if index % 25 == 0 or index == len(futures):
                print(f"rejudge [{index}/{len(futures)}]", flush=True)

    rows = []
    for candidate in selected:
        suite, case_id = str(candidate["suite"]), str(candidate["case_id"])
        invariants = tuple(dedup_normalized(candidate.get("survivors") or []))
        rows.append({
            "suite": suite,
            "case_id": case_id,
            "method": candidate["method"],
            "negative_score": float(candidate[args.score_field]),
            "archived_verified": False,
            **judged[(suite, case_id, invariants)],
        })
    rows.sort(key=lambda row: (
        row["suite"], int(row["case_id"]), row["method"]
    ))
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    full = [row for row in rows if math.isclose(row["negative_score"], 1.0)]
    summary = {
        "protocol": "exact_scoring_survivors_no_frame_assertion_only_v1",
        "candidate_rows": len(rows),
        "unique_invariant_sets": len(unique),
        "new_wp_jobs": len(pending),
        "reused_wp_jobs": len(unique) - len(pending),
        "minimum_negative_score": args.min_score,
        "completed": sum(row["status"] == "completed" for row in rows),
        "timeouts": sum(row["status"] == "timeout" for row in rows),
        "reclassified_verified": sum(row["verified"] for row in rows),
        "target_verified": sum(row["target_verified"] for row in rows),
        "invalid_invariant_sets": sum(
            row["status"] == "completed" and not row["invariants_valid"]
            for row in rows
        ),
        "full_score_rows": len(full),
        "full_score_reclassified_verified": sum(row["verified"] for row in full),
        "full_score_remaining_failed": sum(not row["verified"] for row in full),
        "wall_seconds_sum_all_judgments": sum(
            float(row.get("seconds") or 0.0) for row in judged.values()
        ),
        "output": str(output.relative_to(REPO)),
    }
    summary_path = args.results_root / f"{output_stem}_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True))
    return int(bool(summary["timeouts"]))


if __name__ == "__main__":
    raise SystemExit(main())
