"""LoopGym R5-H/no-reroll replay from the exact R10 rollout prefix."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from rl_pipeline.common import prompts
from rl_pipeline.common.state import MAX_INVARIANTS_PER_RESPONSE, extract_invariants
from rl_pipeline.inference import InferenceFramework, MockRolloutProvider

from .common import (
    DEFAULT_RESULTS_ROOT,
    Task,
    append_jsonl,
    base_row,
    discover_tasks,
    ensure_frama_c_available,
    latest_rows,
    sha256_text,
    token_fields,
)
from .daikon_adapter import load_manifest_lightweight
from .run import _score_fixed_task, event_path, new_attempt_dir, save_hidden_source


METHOD = "loopgym_r5_houdini"
SOURCE_METHOD = "loopgym_r10_houdini"
N_ROLLOUTS = 5
EXTENSION_PROTOCOL = "loopgym_r5_houdini_no_reroll_r10_prefix_v1"


def _usage(records: list[dict]) -> dict:
    exact = bool(records) and all(
        record.get("token_accounting") == "exact" for record in records
    )

    def total(field: str):
        values = [record.get(field) for record in records]
        return sum(values) if values and all(value is not None for value in values) else None

    return token_fields(
        prompt=total("prompt_tokens"),
        completion=total("completion_tokens"),
        total=total("total_tokens"),
        calls=len(records),
        accounting="exact" if exact else "unavailable",
    )


def _load_prefix(task: Task, source_row: dict) -> tuple[list[dict], str]:
    if source_row.get("generation_status") != "completed":
        raise RuntimeError("R10 source row is not completed")
    artifact = Path(source_row["api_calls_artifact"])
    records = json.loads(artifact.read_text())
    prompt_hash = sha256_text(
        prompts.GENERATE_PROMPT.format(program=task.hidden_source)
    )
    if (
        len(records) != 10
        or any(record.get("prompt_sha256") != prompt_hash for record in records)
        or any(not isinstance(record.get("response"), str) for record in records)
    ):
        raise RuntimeError(f"incompatible R10 call artifact: {artifact}")
    return records[:N_ROLLOUTS], str(artifact.resolve())


def _run_one(task: Task, root: Path, source_row: dict) -> dict:
    row = base_row(METHOD, task)
    directory = new_attempt_dir(root, METHOD, task)
    hidden_path = save_hidden_source(directory, task)
    started = time.perf_counter()
    records: list[dict] = []
    source_artifact = None
    result = None
    try:
        source_records, source_artifact = _load_prefix(task, source_row)
        records = []
        rollouts = []
        for record in source_records:
            copied = dict(record)
            copied.update({
                "reused": True,
                "reuse_source": source_artifact,
                "reuse_method": SOURCE_METHOD,
                "reuse_prefix_position": len(records),
            })
            records.append(copied)
            rollouts.append(extract_invariants(
                copied["response"], max_invariants=MAX_INVARIANTS_PER_RESPONSE
            ))
        result = InferenceFramework(
            task.source_path.read_text(errors="ignore"),
            rollout_provider=MockRolloutProvider(rollouts),
            n_rollouts=N_ROLLOUTS,
        ).run()
        status, error = "completed", None
        invariants = result.final_invariants
        native_verified = result.verified
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        rollouts, invariants, native_verified = [], [], None
    replay_wall = time.perf_counter() - started
    call_seconds = sum(float(record.get("seconds") or 0.0) for record in records)
    calls_path = directory / "api_calls.reused.json"
    calls_path.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    row.update({
        "generation_status": status,
        "generation_error": error,
        "hidden_source": str(hidden_path),
        "raw_responses": [record.get("response", "") for record in records],
        "rollouts": rollouts,
        "invariants": invariants,
        "native_verified": native_verified,
        "n_rollouts": N_ROLLOUTS,
        "extension_protocol": EXTENSION_PROTOCOL,
        "api_calls_artifact": str(calls_path),
        "reuse_source_method": SOURCE_METHOD,
        "reuse_source_artifact": source_artifact,
        "reused_api_call_count": len(records),
        "fresh_api_call_count": 0,
        "observed_replay_wall_seconds": replay_wall,
        "generation_seconds": replay_wall + call_seconds,
        "generation_time_accounting": "replay_wall_plus_original_call_wall",
        **_usage(records),
    })
    return row


def generate_all(root: Path, workers: int, retry_failed: bool) -> None:
    ensure_frama_c_available()
    tasks = discover_tasks()
    destination = event_path(root, METHOD)
    existing = latest_rows([destination])
    source = latest_rows([event_path(root, SOURCE_METHOD)])
    pending = []
    for task in tasks:
        old = existing.get(task.key(METHOD))
        if old and old.get("generation_status") == "completed":
            continue
        if old and not retry_failed and old.get("generation_status") == "failed":
            continue
        pending.append(task)
    print(f"R5-H replay reusable={len(tasks)-len(pending)} pending={len(pending)}")
    work = []
    for task in pending:
        source_row = source.get(task.key(SOURCE_METHOD))
        if source_row is None:
            raise RuntimeError(f"missing R10 source row: {task.suite}/{task.case_id}")
        work.append((task, source_row))
    # WP/Houdini is CPU-heavy, so process isolation is preferable to threads.
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_run_one, task, root, source_row): task
            for task, source_row in work
        }
        for index, future in enumerate(as_completed(futures), 1):
            task = futures[future]
            row = future.result()
            append_jsonl(destination, row)
            print(
                f"[{index}/{len(work)}] {task.suite}/{task.case_id} "
                f"{row['generation_status']} verified={row.get('native_verified')} "
                f"seconds={row.get('generation_seconds', 0):.2f}",
                flush=True,
            )
    metadata = {
        "method": METHOD,
        "extension_protocol": EXTENSION_PROTOCOL,
        "rollouts": N_ROLLOUTS,
        "houdini": True,
        "source_method": SOURCE_METHOD,
        "source_prefix_length": N_ROLLOUTS,
        "target_hidden": True,
    }
    (root / "r5_protocol.json").write_text(
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
        current_score = (
            "verified" in row
            and row.get("negative_score_status") == "completed"
            and row.get("sample_content_sha256") == sample["sample_content_sha256"]
        )
        if not current_score:
            work.append((task, sample, [(destination, row)]))
    print(f"R5-H scoring pending={len(work)} workers={workers}", flush=True)
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
    replay_timed = [
        float(row["observed_replay_wall_seconds"])
        for row in rows if row.get("observed_replay_wall_seconds") is not None
    ]
    tokens = [int(row["total_tokens"]) for row in rows if row.get("total_tokens") is not None]
    prompt_tokens = [int(row["prompt_tokens"]) for row in rows if row.get("prompt_tokens") is not None]
    completion_tokens = [
        int(row["completion_tokens"])
        for row in rows if row.get("completion_tokens") is not None
    ]
    summary = {
        "method": METHOD,
        "task_rows": len(rows),
        "completed": sum(row.get("generation_status") == "completed" for row in rows),
        "failed": sum(row.get("generation_status") == "failed" for row in rows),
        "verified": verified,
        "accuracy": verified / 832,
        "mean_generation_seconds": sum(timed) / len(timed) if timed else None,
        "mean_observed_replay_wall_seconds": (
            sum(replay_timed) / len(replay_timed) if replay_timed else None
        ),
        "mean_original_api_call_seconds": (
            (sum(timed) - sum(replay_timed)) / len(timed)
            if timed and len(timed) == len(replay_timed) else None
        ),
        "time_rows": len(timed),
        "replay_time_rows": len(replay_timed),
        "mean_prompt_tokens": (
            sum(prompt_tokens) / len(prompt_tokens) if prompt_tokens else None
        ),
        "mean_completion_tokens": (
            sum(completion_tokens) / len(completion_tokens)
            if completion_tokens else None
        ),
        "mean_total_tokens": sum(tokens) / len(tokens) if tokens else None,
        "token_rows": len(tokens),
        "reused_api_calls": sum(int(row.get("reused_api_call_count") or 0) for row in rows),
        "fresh_api_calls": 0,
        "negative_micro_rejection": rejected / traces if traces else None,
        "negative_macro_rejection": (
            sum(float(row["negative_rejection_score"]) for row in negative) / len(negative)
            if negative else None
        ),
        "negative_rows": len(negative),
        "event_utc": datetime.now(timezone.utc).isoformat(),
    }
    (root / "r5_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    fields = sorted({key for row in rows for key in row})
    with (root / "r5_results.csv").open("w", newline="") as handle:
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
    generate.add_argument("--workers", type=int, default=16)
    generate.add_argument("--retry-failed", action="store_true")
    score = sub.add_parser("score")
    score.add_argument("--workers", type=int, default=4)
    sub.add_parser("report")
    all_parser = sub.add_parser("all")
    all_parser.add_argument("--workers", type=int, default=16)
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
