"""Resumable CRAFT R10-H extension for the frozen Full-832 evaluation."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from rl_pipeline.inference import BatchedLLMRolloutProvider, InferenceFramework

from .api import RecordingChat
from .common import (
    DEFAULT_RESULTS_ROOT,
    Task,
    append_jsonl,
    base_row,
    craft_env,
    discover_tasks,
    ensure_frama_c_available,
    latest_rows,
)
from .daikon_adapter import load_manifest_lightweight
from .run import _score_fixed_task, event_path, new_attempt_dir, save_hidden_source


METHOD = "loopgym_r10_houdini"
# Budget of archived rollouts per task; the compose grid is recomputed from
# prefixes of this pool, so it only needs to cover the largest reported k.
N_ROLLOUTS = int(craft_env("R10_N_ROLLOUTS") or "10")
# Some providers behind the shared OpenAI-compatible endpoint silently return
# fewer choices than requested for n>1 (observed for gpt-5 and
# claude-sonnet-4-6), rather than raising. Those models must use
# REQUESTS_PER_TASK=10 / ROLLOUTS_PER_REQUEST=1; models that honor n=5 keep
# the original two-batched-calls scheme. Override per model via env vars.
REQUESTS_PER_TASK = int(craft_env("R10_REQUESTS_PER_TASK") or "2")
ROLLOUTS_PER_REQUEST = int(craft_env("R10_ROLLOUTS_PER_REQUEST") or "5")
if REQUESTS_PER_TASK * ROLLOUTS_PER_REQUEST != N_ROLLOUTS:
    raise ValueError(
        "CRAFT_R10_REQUESTS_PER_TASK * CRAFT_R10_ROLLOUTS_PER_REQUEST must "
        f"equal {N_ROLLOUTS}, got {REQUESTS_PER_TASK} * {ROLLOUTS_PER_REQUEST}"
    )
EXTENSION_PROTOCOL = (
    f"loopgym_r10_houdini_n{ROLLOUTS_PER_REQUEST}x{REQUESTS_PER_TASK}_v4"
)


def _compatible_completed(row: dict | None) -> bool:
    return bool(
        row
        and row.get("generation_status") == "completed"
        and row.get("extension_protocol") == EXTENSION_PROTOCOL
        and int(row.get("api_call_count") or 0) == REQUESTS_PER_TASK
        and int(row.get("fresh_api_call_count") or 0) == REQUESTS_PER_TASK
        and int(row.get("n_rollouts") or 0) == N_ROLLOUTS
        and int(row.get("rollout_count") or 0) == N_ROLLOUTS
        and isinstance(row.get("raw_responses"), list)
        and len(row["raw_responses"]) == N_ROLLOUTS
    )


def _run_one(task: Task, root: Path) -> dict:
    row = base_row(METHOD, task)
    directory = new_attempt_dir(root, METHOD, task)
    hidden_path = save_hidden_source(directory, task)
    recorder = RecordingChat()

    def chat_two_batches(prompt: str, n: int) -> list[str]:
        if n != N_ROLLOUTS:
            raise ValueError(f"expected {N_ROLLOUTS} rollouts, got {n}")
        responses = []
        for _ in range(REQUESTS_PER_TASK):
            responses.extend(recorder.chat_n(prompt, ROLLOUTS_PER_REQUEST))
        return responses

    provider = BatchedLLMRolloutProvider(chat_two_batches)
    wall_started = time.perf_counter()
    result = None
    try:
        result = InferenceFramework(
            task.source_path.read_text(errors="ignore"),
            rollout_provider=provider,
            n_rollouts=N_ROLLOUTS,
        ).run()
        valid_batch = (
            len(recorder.records) == REQUESTS_PER_TASK
            and all(
                record.get("choice_count") == ROLLOUTS_PER_REQUEST
                for record in recorder.records
            )
        )
        status = "completed" if valid_batch else "failed"
        error = None if status == "completed" else (
            f"expected {REQUESTS_PER_TASK} n={ROLLOUTS_PER_REQUEST} calls, "
            f"recorded {len(recorder.records)}"
        )
        invariants = result.final_invariants
        rollouts = result.rollouts
        native_verified = result.verified
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        invariants, rollouts, native_verified = [], [], None
    observed_wall = time.perf_counter() - wall_started
    calls_path = directory / "api_calls.json"
    calls_path.write_text(json.dumps(recorder.records, indent=2, ensure_ascii=False))
    raw_responses = [
        response
        for record in recorder.records
        for response in (record.get("responses") or [])
    ]
    row.update({
        "generation_status": status,
        "generation_error": error,
        "hidden_source": str(hidden_path),
        "raw_responses": raw_responses,
        "rollouts": rollouts,
        "invariants": invariants,
        "native_verified": native_verified,
        "n_rollouts": N_ROLLOUTS,
        "rollout_count": len(raw_responses),
        "extension_protocol": EXTENSION_PROTOCOL,
        "api_calls_artifact": str(calls_path),
        "reuse_source_method": None,
        "reuse_source_artifact": None,
        "reused_api_call_count": 0,
        "fresh_api_call_count": len(recorder.records),
        "observed_extension_wall_seconds": observed_wall,
        "generation_seconds": observed_wall,
        "generation_time_accounting": "observed_wall",
        **recorder.usage(),
    })
    return row


def generate_all(root: Path, workers: int, retry_failed: bool) -> None:
    ensure_frama_c_available()
    tasks = discover_tasks()
    destination = event_path(root, METHOD)
    existing = latest_rows([destination])
    pending = []
    for task in tasks:
        old = existing.get(task.key(METHOD))
        if _compatible_completed(old):
            continue
        if old and not retry_failed and old.get("generation_status") in {
            "failed", "timeout"
        }:
            continue
        pending.append(task)
    print(f"R10-H generation reusable={len(tasks)-len(pending)} pending={len(pending)}")
    if pending:
        # RecordingChat performs the definitive credential check without logging it.
        RecordingChat()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_run_one, task, root): task
            for task in pending
        }
        for index, future in enumerate(as_completed(futures), 1):
            task = futures[future]
            row = future.result()
            append_jsonl(destination, row)
            print(
                f"[{index}/{len(pending)}] {task.suite}/{task.case_id} "
                f"{row['generation_status']} calls={row.get('api_call_count')} "
                f"reused={row.get('reused_api_call_count')} "
                f"verified={row.get('native_verified')} "
                f"seconds={row.get('generation_seconds', 0):.2f}",
                flush=True,
            )
    metadata = {
        "method": METHOD,
        "extension_protocol": EXTENSION_PROTOCOL,
        "rollouts_per_attempt": N_ROLLOUTS,
        "api_calls_per_attempt": REQUESTS_PER_TASK,
        "api_n": ROLLOUTS_PER_REQUEST,
        "request_schedule": [ROLLOUTS_PER_REQUEST] * REQUESTS_PER_TASK,
        "houdini": True,
        "target_hidden": True,
    }
    (root / "r10_protocol.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    )


def score_all(root: Path, workers: int) -> None:
    ensure_frama_c_available()
    samples = load_manifest_lightweight(root)
    tasks = {(task.suite, task.case_id): task for task in discover_tasks()}
    destination = event_path(root, METHOD)
    current = latest_rows([destination])
    work = []
    for task in tasks.values():
        row = current.get(task.key(METHOD))
        if not row or row.get("generation_status") not in {"completed", "failed"}:
            continue
        sample = samples[(task.suite, task.case_id)]
        score_current = (
            "verified" in row
            and row.get("negative_score_status") == "completed"
            and row.get("sample_content_sha256") == sample["sample_content_sha256"]
        )
        if not score_current:
            work.append((task, sample, [(destination, row)]))
    print(f"R10-H scoring pending={len(work)} workers={workers}", flush=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_score_fixed_task, task, sample, entries): task
            for task, sample, entries in work
        }
        for index, future in enumerate(as_completed(futures), 1):
            task = futures[future]
            for path, row in future.result():
                append_jsonl(path, row)
                print(
                    f"[{index}/{len(work)}] {task.suite}/{task.case_id} "
                    f"verified={row.get('verified')} "
                    f"negative={row.get('negative_rejection_score')}",
                    flush=True,
                )


def report(root: Path) -> dict:
    tasks = discover_tasks()
    current = latest_rows([event_path(root, METHOD)])
    rows = [current[task.key(METHOD)] for task in tasks if task.key(METHOD) in current]
    verified = sum(row.get("verified") is True for row in rows)
    negative = [
        row for row in rows
        if row.get("negative_trace_count") not in (None, 0)
        and row.get("negative_rejection_score") is not None
    ]
    rejected = sum(int(row.get("rejected_negative_count") or 0) for row in negative)
    traces = sum(int(row.get("negative_trace_count") or 0) for row in negative)
    timed = [float(row["generation_seconds"]) for row in rows if row.get("generation_seconds") is not None]
    tokens = [int(row["total_tokens"]) for row in rows if row.get("total_tokens") is not None]
    summary = {
        "method": METHOD,
        "task_rows": len(rows),
        "completed": sum(row.get("generation_status") == "completed" for row in rows),
        "failed": sum(row.get("generation_status") == "failed" for row in rows),
        "verified": verified,
        "accuracy": verified / 832,
        "mean_generation_seconds": sum(timed) / len(timed) if timed else None,
        "time_rows": len(timed),
        "mean_total_tokens": sum(tokens) / len(tokens) if tokens else None,
        "token_rows": len(tokens),
        "reused_api_calls": sum(int(row.get("reused_api_call_count") or 0) for row in rows),
        "fresh_api_calls": sum(int(row.get("fresh_api_call_count") or 0) for row in rows),
        "negative_micro_rejection": rejected / traces if traces else None,
        "negative_macro_rejection": (
            sum(float(row["negative_rejection_score"]) for row in negative) / len(negative)
            if negative else None
        ),
        "negative_rows": len(negative),
        "event_utc": datetime.now(timezone.utc).isoformat(),
    }
    (root / "r10_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    fields = sorted({key for row in rows for key in row})
    with (root / "r10_results.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value, ensure_ascii=False)
                if isinstance(value, (list, dict)) else value
                for key, value in row.items()
            })
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate")
    generate.add_argument("--workers", type=int, default=4)
    generate.add_argument("--retry-failed", action="store_true")
    score = sub.add_parser("score")
    score.add_argument("--workers", type=int, default=4)
    sub.add_parser("report")
    all_parser = sub.add_parser("all")
    all_parser.add_argument("--workers", type=int, default=4)
    all_parser.add_argument("--score-workers", type=int, default=4)
    all_parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    if args.command in {"generate", "all"}:
        generate_all(args.results_root, args.workers, args.retry_failed)
    if args.command in {"score", "all"}:
        score_all(
            args.results_root,
            args.score_workers if args.command == "all" else args.workers,
        )
    if args.command in {"report", "all"}:
        report(args.results_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
