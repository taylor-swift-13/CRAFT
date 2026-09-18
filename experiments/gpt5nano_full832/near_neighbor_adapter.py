#!/usr/bin/env python3
"""Run and summarize target-hidden LORIS/LaM4Inv GPT-5-nano adaptations."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .common import (
    DEFAULT_RESULTS_ROOT,
    append_jsonl,
    base_row,
    discover_tasks,
    ensure_frama_c_available,
    latest_rows,
    token_fields,
)
from .run import judge_invariants, new_attempt_dir


METHODS = ("loris", "lam4inv")
RUNNER = Path(__file__).with_name("near_neighbor_runner.py")
LAM4INV_SUITES = ("linear", "NLA_lipus")


def _tasks_for(method: str):
    """Return every task supported by the selected released interface."""
    tasks = discover_tasks()
    if method == "lam4inv":
        return [task for task in tasks if task.suite in LAM4INV_SUITES]
    return tasks


def _event_path(root: Path, method: str) -> Path:
    return root / "events" / f"{method}.jsonl"


def _normalise_single_function(source: str) -> str:
    """Give LORIS the `main` name assumed by its released AST frontend."""
    pattern = re.compile(
        r"\b(void|int)\s+([A-Za-z_]\w*)\s*(\([^;{}]*\)\s*\{)", re.MULTILINE
    )
    matches = list(pattern.finditer(source))
    preferred = [match for match in matches if match.group(2).startswith("loopy_")]
    candidates = [
        match for match in matches
        if match.group(2) not in {"errorFn", "unknown", "main"}
        and not match.group(2).startswith("__VERIFIER_")
    ]
    if len(preferred) == 1:
        match = preferred[0]
    elif len(candidates) == 1:
        match = candidates[0]
    elif len(matches) == 1:
        match = matches[0]
    else:
        names = ", ".join(match.group(2) for match in matches)
        raise ValueError(f"cannot identify target function among: {names}")
    return source[:match.start(2)] + "main" + source[match.end(2):]


def _parse_result(stdout: str) -> dict | None:
    for line in reversed(stdout.splitlines()):
        if line.startswith("RESULT_JSON:"):
            try:
                return json.loads(line[len("RESULT_JSON:"):])
            except json.JSONDecodeError:
                return None
    return None


def _redact(text: str) -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        text = text.replace(key, "<REDACTED_OPENAI_API_KEY>")
    return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "<REDACTED_API_KEY>", text)


def _run_one(method, task, root: Path, timeout: int) -> dict:
    row = base_row(method, task)
    row["generation_eligible"] = True
    attempt = new_attempt_dir(root, method, task)
    hidden = attempt / "input.hidden.c"
    source = task.hidden_source
    if method == "loris":
        source = _normalise_single_function(source)
    hidden.write_text(source)
    log = attempt / "native.log"
    runner_python = (
        Path(os.environ.get("LORIS_PYTHON", "/home/yangfp/LORIS/.conda/bin/python"))
        if method == "loris" else Path(sys.executable)
    )
    command = [
        str(runner_python),
        str(RUNNER),
        "--method", method,
        "--source", str(hidden),
        "--suite", task.suite,
        "--case-id", task.case_id,
        "--timeout", str(timeout),
        "--log", str(log),
    ]
    env = os.environ.copy()
    frama_bin = str(ensure_frama_c_available().parent)
    env["PATH"] = frama_bin + os.pathsep + env.get("PATH", "")
    started = time.perf_counter()
    try:
        process = subprocess.run(
            command,
            cwd=Path.cwd(),
            env=env,
            capture_output=True,
            text=True,
            # LORIS checks the native budget between refinement rounds.  Give
            # an in-flight final API/checker call enough shutdown grace so a
            # native timeout is recorded by the runner rather than losing its
            # result at the subprocess boundary.
            timeout=timeout + 180,
        )
        timed_out = False
        stdout, stderr = process.stdout or "", process.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        process = None
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    (attempt / "command.log").write_text(
        _redact(
            "$ " + " ".join(command) + "\n\n[stdout]\n" + stdout + "\n\n[stderr]\n" + stderr
        )
    )
    parsed = _parse_result(stdout)
    if parsed is not None:
        records = parsed.pop("api_records", [])
        (attempt / "api_calls.json").write_text(
            json.dumps(records, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        )
        row.update(parsed)
        row.update({
            "generation_status": "completed",
            "generation_error": None,
            "hidden_source": str(hidden),
            "api_calls_artifact": str(attempt / "api_calls.json"),
            "native_log": str(log),
        })
    else:
        row.update({
            "generation_status": "timeout" if timed_out else "failed",
            "generation_error": "timeout" if timed_out else (
                f"returncode_{process.returncode}: missing RESULT_JSON"
            ),
            "invariants": [],
            "generation_seconds": time.perf_counter() - started,
            "hidden_source": str(hidden),
            **token_fields(accounting="unavailable"),
        })
    return row


def generate(
    root: Path,
    method: str,
    workers: int,
    timeout: int,
    retry_failed: bool,
    suite: str | None = None,
    limit: int | None = None,
) -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required")
    path = _event_path(root, method)
    existing = latest_rows([path])
    tasks = _tasks_for(method)
    if suite:
        tasks = [task for task in tasks if task.suite == suite]
    pending = []
    for task in tasks:
        old = existing.get(task.key(method))
        if old and old.get("generation_status") in {"completed", "unsupported"}:
            continue
        # Retry infrastructure/API failures when requested, but do not turn a
        # genuine method timeout into a second stochastic attempt.
        if old and old.get("generation_status") == "timeout":
            continue
        if old and not retry_failed and old.get("generation_status") == "failed":
            continue
        pending.append(task)
    if limit is not None:
        pending = pending[:limit]
    print(f"{method}: reusable={len(tasks)-len(pending)} pending={len(pending)}")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_run_one, method, task, root, timeout): task for task in pending
        }
        for index, future in enumerate(as_completed(futures), 1):
            task = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                row = base_row(method, task)
                row.update({
                    "generation_status": "failed",
                    "generation_eligible": True,
                    "generation_error": f"{type(exc).__name__}: {exc}",
                    "invariants": [],
                    "generation_seconds": None,
                    **token_fields(accounting="unavailable"),
                })
            append_jsonl(path, row)
            print(
                f"[{index}/{len(pending)}] {method} {task.suite}/{task.case_id} "
                f"{row['generation_status']}", flush=True
            )


def score(root: Path, method: str, workers: int, suite: str | None = None) -> None:
    ensure_frama_c_available()
    path = _event_path(root, method)
    existing = latest_rows([path])
    tasks = _tasks_for(method)
    if suite:
        tasks = [task for task in tasks if task.suite == suite]
    pending = []
    for task in tasks:
        row = existing.get(task.key(method))
        if row and "verified" not in row:
            pending.append((task, row))

    def score_one(item):
        task, row = item
        updated = dict(row)
        if row.get("generation_status") == "unsupported":
            updated.update({"verified": False, "judge_seconds": 0.0, "judge_error": row.get("generation_error")})
        else:
            updated.update(judge_invariants(task, row.get("invariants") or []))
        updated["reproduction_total_seconds"] = float(updated.get("generation_seconds") or 0) + float(updated.get("judge_seconds") or 0)
        return updated

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(score_one, item): item[0] for item in pending}
        for index, future in enumerate(as_completed(futures), 1):
            row = future.result()
            append_jsonl(path, row)
            print(f"[{index}/{len(pending)}] {method} {row['suite']}/{row['case_id']} verified={row['verified']}", flush=True)


def summarize(root: Path) -> None:
    output = {}
    for method in METHODS:
        tasks = _tasks_for(method)
        rows = latest_rows([_event_path(root, method)])
        selected = [rows.get(task.key(method)) for task in tasks]
        selected = [row for row in selected if row]
        supported = [row for row in selected if row.get("generation_status") != "unsupported"]
        called = [row for row in supported if (row.get("api_call_count") or 0) > 0]
        timed = [row for row in supported if row.get("generation_seconds") is not None]
        result = {
            "method": method,
            "comparison_scope": (
                "316 Linear + 50 NLA" if method == "lam4inv"
                else "316 Linear + 50 NLA + 466 Loopy"
            ),
            "rows": len(selected),
            "supported": len(supported),
            "completed": sum(row.get("generation_status") == "completed" for row in selected),
            "verified": sum(bool(row.get("verified")) for row in selected),
            "accuracy": sum(bool(row.get("verified")) for row in selected) / len(tasks),
            "supported_accuracy": (
                sum(bool(row.get("verified")) for row in supported) / len(supported)
                if supported else 0.0
            ),
            "mean_total_tokens": (
                sum(int(row["total_tokens"]) for row in called) / len(called)
                if called and all(row.get("total_tokens") is not None for row in called) else None
            ),
            "token_rows": len(called),
            "mean_generation_seconds": (
                sum(float(row["generation_seconds"]) for row in timed) / len(timed)
                if timed else None
            ),
            "time_rows": len(timed),
            "by_suite": {},
        }
        if method == "lam4inv":
            result["unsupported_suites"] = {
                "Loopy": {
                    "tasks": 466,
                    "status": "not evaluated",
                    "reason": (
                        "released LaM4Inv requires CFG/SMT intermediate artifacts "
                        "that are unavailable for Loopy"
                    ),
                }
            }
        for suite in (LAM4INV_SUITES if method == "lam4inv" else ("linear", "NLA_lipus", "Loopy")):
            suite_rows = [row for row in selected if row["suite"] == suite]
            result["by_suite"][suite] = {
                "total": len(suite_rows),
                "verified": sum(bool(row.get("verified")) for row in suite_rows),
            }
        output[method] = result
    destination = root / "near_neighbor_summary.json"
    destination.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("generate", "score", "all", "summarize"))
    parser.add_argument("--method", choices=METHODS)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--score-workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--suite", choices=("linear", "NLA_lipus", "Loopy"))
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    methods = (args.method,) if args.method else METHODS
    if args.command in {"generate", "all"}:
        for method in methods:
            generate(
                args.results_root, method, args.workers, args.timeout,
                args.retry_failed, args.suite, args.limit,
            )
    if args.command in {"score", "all"}:
        for method in methods:
            score(args.results_root, method, args.score_workers, args.suite)
    if args.command in {"summarize", "all"}:
        summarize(args.results_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
