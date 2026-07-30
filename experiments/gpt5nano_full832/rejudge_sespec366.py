#!/usr/bin/env python3
"""Rejudge the saved 366 SESpec artifacts with their full loop annotations.

SESpec's ``output/{function}.c`` files sometimes copy ``loop invariant`` clauses
into a function contract.  Frama-C correctly rejects that form because loop
clauses are legal only immediately before a loop.  The generated file still
contains a separate, valid loop-annotation block before ``while``.

This reproducer:

1. reads the latest saved SESpec artifact for each Linear/NLA task;
2. preserves generated top-level ACSL predicate/logic definitions;
3. transplants only the ACSL block immediately preceding the first loop into
   the untouched benchmark source, thereby restoring the hidden assertion;
4. runs the same Frama-C/WP configuration used by the common judge; and
5. saves every reconstructed C file, Frama-C log, and machine-readable result.

No model/API calls are made.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import time
from typing import Any

from rl_pipeline.common.program import parse_program


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS = REPO_ROOT / "results" / "gpt5nano_full832"
DEFAULT_OUTPUT = DEFAULT_RESULTS / "sespec366_rejudge_full_artifact"
SUITES = ("linear", "NLA_lipus")
EXPECTED = {"linear": 316, "NLA_lipus": 50}
ACSL_BLOCK_RE = re.compile(r"/\*@.*?\*/", re.DOTALL)
HELPER_KEYWORD_RE = re.compile(r"\b(?:predicate|logic|axiom|lemma)\b")
CONTRACT_KEYWORD_RE = re.compile(
    r"\b(?:requires|ensures|assigns|loop\s+invariant|loop\s+assigns)\b"
)
PROVED_RE = re.compile(r"Proved goals:\s*(\d+)\s*/\s*(\d+)")
ASSERTION_GOAL_RE = re.compile(
    r"\bGoal\s+(?:Assertion|Post-condition|Complete behaviors|"
    r"Disjoint behaviors)\b"
)


def numeric_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)


def load_latest_sespec_rows(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for line in path.read_text(errors="ignore").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("method") == "sespec" and row.get("suite") in SUITES:
            rows[(str(row["suite"]), str(row["case_id"]))] = row
    expected_total = sum(EXPECTED.values())
    if len(rows) != expected_total:
        raise RuntimeError(
            f"expected {expected_total} SESpec rows for 366 subset, found {len(rows)}"
        )
    for suite, expected in EXPECTED.items():
        count = sum(key[0] == suite for key in rows)
        if count != expected:
            raise RuntimeError(f"{suite}: expected {expected} rows, found {count}")
    return rows


def find_loop_annotation(artifact: str) -> str:
    """Return the generated ACSL block closest to and before the first loop."""
    program = parse_program(artifact)
    if not program.loops:
        raise ValueError("artifact has no loop")
    loop_start = program.loops[0].kw_start
    candidates = [
        match
        for match in ACSL_BLOCK_RE.finditer(artifact, 0, loop_start)
        if re.search(r"\bloop\s+invariant\b", match.group(0))
    ]
    if not candidates:
        raise ValueError("no loop-invariant ACSL block before first loop")
    block = max(candidates, key=lambda match: match.end())
    between = artifact[block.end():loop_start]
    # Ordinary comments in this gap are harmless, but executable tokens imply
    # that the selected annotation does not actually annotate this loop.
    without_comments = re.sub(r"/\*.*?\*/|//[^\n]*", "", between, flags=re.DOTALL)
    if without_comments.strip():
        raise ValueError("non-comment tokens between annotation and first loop")
    return block.group(0)


def find_global_helpers(artifact: str) -> list[str]:
    """Keep generated global logic context, but never generated assumptions."""
    program = parse_program(artifact)
    signature = re.search(rf"\b{re.escape(program.func_name)}\s*\(", artifact)
    if signature is None:
        return []
    helpers: list[str] = []
    for match in ACSL_BLOCK_RE.finditer(artifact, 0, signature.start()):
        block = match.group(0)
        if (
            HELPER_KEYWORD_RE.search(block)
            and not CONTRACT_KEYWORD_RE.search(block)
        ):
            helpers.append(block)
    return helpers


def count_loop_invariants(block: str) -> int:
    return len(re.findall(r"\bloop\s+invariant\b", block))


def reconstruct(original: str, artifact: str) -> tuple[str, int, int]:
    loop_block = find_loop_annotation(artifact)
    helpers = find_global_helpers(artifact)
    original_program = parse_program(original)
    if not original_program.loops:
        raise ValueError("original source has no loop")
    loop_start = original_program.loops[0].kw_start
    helper_prefix = "\n".join(helpers)
    if helper_prefix:
        helper_prefix += "\n"
    annotated = (
        helper_prefix
        + original[:loop_start]
        + loop_block
        + "\n"
        + original[loop_start:]
    )
    return annotated, count_loop_invariants(loop_block), len(helpers)


def run_one(
    key: tuple[str, str],
    row: dict[str, Any],
    output_root: Path,
    frama_c: str,
    process_timeout: int,
) -> dict[str, Any]:
    suite, case_id = key
    case_dir = output_root / suite / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "suite": suite,
        "case_id": case_id,
        "source": row.get("source"),
        "artifact": row.get("artifact"),
        "old_verified": row.get("verified"),
        "status": "failed",
        "verified": False,
        "error": None,
    }
    started = time.perf_counter()
    try:
        source_path = Path(str(row["source"]))
        artifact_path = Path(str(row["artifact"]))
        original = source_path.read_text(errors="ignore")
        artifact = artifact_path.read_text(errors="ignore")
        annotated, invariant_count, helper_count = reconstruct(original, artifact)
        annotated_path = case_dir / "annotated.c"
        annotated_path.write_text(annotated)
        result.update(
            {
                "annotated": str(annotated_path.resolve()),
                "invariant_count": invariant_count,
                "helper_definition_count": helper_count,
                "assertion_restored": original != re.sub(
                    r"\bassert\b", "__removed_assert_marker__", original
                ),
            }
        )
        command = [
            frama_c,
            "-wp",
            "-wp-print",
            "-wp-timeout",
            "30",
            "-wp-par",
            "8",
            "-wp-prover",
            "alt-ergo",
            "-wp-model",
            "Typed",
            "-wp-prop=-@terminates",
            str(annotated_path.resolve()),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=process_timeout,
        )
        log = (completed.stdout or "") + (completed.stderr or "")
        (case_dir / "frama.log").write_text(log)
        summaries = PROVED_RE.findall(log)
        proved = int(summaries[-1][0]) if summaries else None
        goals = int(summaries[-1][1]) if summaries else None
        assertion_goal_seen = bool(ASSERTION_GOAL_RE.search(log))
        syntax_ok = (
            completed.returncode == 0
            and "Frama-C aborted" not in log
            and "invalid user input" not in log
            and "[kernel:annot-error]" not in log
        )
        verified = bool(
            syntax_ok
            and proved is not None
            and goals is not None
            and goals > 0
            and proved == goals
            and assertion_goal_seen
        )
        result.update(
            {
                "status": "completed",
                "returncode": completed.returncode,
                "syntax_ok": syntax_ok,
                "assertion_goal_seen": assertion_goal_seen,
                "proved_goals": proved,
                "total_goals": goals,
                "verified": verified,
            }
        )
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or b"") + (exc.stderr or b"")
        if isinstance(partial, bytes):
            partial = partial.decode(errors="replace")
        (case_dir / "frama.log").write_text(partial)
        result.update({"status": "timeout", "error": f"process_timeout_{process_timeout}s"})
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        (case_dir / "frama.log").write_text(result["error"] + "\n")
    result["seconds"] = time.perf_counter() - started
    result["event_utc"] = datetime.now(timezone.utc).isoformat()
    (case_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True)
    )
    return result


def write_outputs(output_root: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        rows, key=lambda row: (SUITES.index(row["suite"]), numeric_key(row["case_id"]))
    )
    fields = [
        "suite",
        "case_id",
        "status",
        "verified",
        "old_verified",
        "syntax_ok",
        "assertion_goal_seen",
        "invariant_count",
        "helper_definition_count",
        "proved_goals",
        "total_goals",
        "seconds",
        "error",
        "source",
        "artifact",
        "annotated",
    ]
    with (output_root / "results.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ordered)
    with (output_root / "results.jsonl").open("w") as handle:
        for row in ordered:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    summary: dict[str, Any] = {
        "protocol": "saved SESpec generations; full loop artifact + restored target",
        "model_calls": 0,
        "total": len(ordered),
        "completed": sum(row["status"] == "completed" for row in ordered),
        "timeouts": sum(row["status"] == "timeout" for row in ordered),
        "failed": sum(row["status"] == "failed" for row in ordered),
        "verified": sum(row["verified"] is True for row in ordered),
        "old_verified": sum(row["old_verified"] is True for row in ordered),
        "changed_false_to_true": sum(
            row["old_verified"] is not True and row["verified"] is True
            for row in ordered
        ),
        "changed_true_to_false": sum(
            row["old_verified"] is True and row["verified"] is not True
            for row in ordered
        ),
        "suites": {},
    }
    summary["verified_rate"] = summary["verified"] / summary["total"]
    summary["old_verified_rate"] = summary["old_verified"] / summary["total"]
    for suite in SUITES:
        subset = [row for row in ordered if row["suite"] == suite]
        passed = sum(row["verified"] is True for row in subset)
        old_passed = sum(row["old_verified"] is True for row in subset)
        summary["suites"][suite] = {
            "total": len(subset),
            "verified": passed,
            "verified_rate": passed / len(subset),
            "old_verified": old_passed,
            "old_verified_rate": old_passed / len(subset),
        }
    (output_root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True)
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--process-timeout", type=int, default=90)
    parser.add_argument(
        "--frama-c",
        default="/home/yangfp/.opam/frama-c.27.1/bin/frama-c",
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    latest = args.results_root / "latest.jsonl"
    rows = load_latest_sespec_rows(latest)
    args.output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                run_one,
                key,
                row,
                args.output_root,
                args.frama_c,
                args.process_timeout,
            ): key
            for key, row in rows.items()
        }
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            print(
                f"[{index:03d}/{len(futures)}] "
                f"{result['suite']}/{result['case_id']} "
                f"{result['status']} verified={result['verified']}",
                flush=True,
            )
    summary = write_outputs(args.output_root, results)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
