"""Resumable LoopGym R10-H extension for the frozen Full-832 evaluation."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from rl_pipeline.common import prompts
from rl_pipeline.common.program import strip_postcondition
from rl_pipeline.common.state import MAX_INVARIANTS_PER_RESPONSE, extract_invariants
from rl_pipeline.inference import InferenceFramework

from .api import RecordingChat
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


METHOD = "loopgym_r10_houdini"
SOURCE_METHOD = "loopgym_r4_houdini"
N_ROLLOUTS = 10
EXTENSION_PROTOCOL = "loopgym_r10_houdini_no_reroll_reuse_r4_v2"


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


def _reusable_batches(task: Task, source_row: dict | None) -> tuple[list[list[dict]], str | None]:
    if not source_row or source_row.get("generation_status") != "completed":
        return [], None
    artifact = source_row.get("api_calls_artifact")
    if not artifact or not Path(artifact).is_file():
        return [], None
    try:
        records = json.loads(Path(artifact).read_text())
    except Exception:
        return [], None
    expected_prompt = prompts.GENERATE_PROMPT.format(program=task.hidden_source)
    prompt_hash = sha256_text(expected_prompt)
    if (
        len(records) not in (4, 8)
        or any(record.get("prompt_sha256") != prompt_hash for record in records)
        or any(not isinstance(record.get("response"), str) for record in records)
    ):
        return [], None
    # R10-H has exactly one attempt, so only R4's first attempt is compatible.
    return [records[:4]], artifact


class _HybridProvider:
    """Reuse four compatible R4 responses per attempt and request the other six."""

    def __init__(self, recorder: RecordingChat, batches: list[list[dict]], source: str | None):
        self.recorder = recorder
        self.batches = batches
        self.source = source
        self.attempt = 0
        self.records: list[dict] = []

    def __call__(self, program, n: int) -> list[list[str]]:
        if n != N_ROLLOUTS:
            raise ValueError(f"R10 provider expected {N_ROLLOUTS} rollouts, got {n}")
        source = strip_postcondition(program.source)
        prompt = prompts.GENERATE_PROMPT.format(program=source)
        reusable = self.batches[self.attempt] if self.attempt < len(self.batches) else []
        outputs: list[list[str]] = []
        for record in reusable:
            copied = dict(record)
            copied.update({
                "reused": True,
                "reuse_source": self.source,
                "reuse_method": SOURCE_METHOD,
            })
            self.records.append(copied)
            outputs.append(extract_invariants(
                copied["response"], max_invariants=MAX_INVARIANTS_PER_RESPONSE
            ))
        for _ in range(n - len(reusable)):
            response = self.recorder.chat(prompt)
            copied = dict(self.recorder.records[-1])
            copied["reused"] = False
            self.records.append(copied)
            outputs.append(extract_invariants(
                response, max_invariants=MAX_INVARIANTS_PER_RESPONSE
            ))
        self.attempt += 1
        return outputs


def _run_one(task: Task, root: Path, source_row: dict | None) -> dict:
    row = base_row(METHOD, task)
    directory = new_attempt_dir(root, METHOD, task)
    hidden_path = save_hidden_source(directory, task)
    recorder = RecordingChat()
    batches, reuse_source = _reusable_batches(task, source_row)
    provider = _HybridProvider(recorder, batches, reuse_source)
    wall_started = time.perf_counter()
    result = None
    try:
        result = InferenceFramework(
            task.source_path.read_text(errors="ignore"),
            rollout_provider=provider,
            n_rollouts=N_ROLLOUTS,
        ).run()
        expected_calls = N_ROLLOUTS
        status = "completed" if len(provider.records) == expected_calls else "failed"
        error = None if status == "completed" else (
            f"expected {expected_calls} total calls, recorded {len(provider.records)}"
        )
        invariants = result.final_invariants
        rollouts = result.rollouts
        native_verified = result.verified
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        invariants, rollouts, native_verified = [], [], None
    observed_wall = time.perf_counter() - wall_started
    reused_seconds = sum(
        float(record.get("seconds") or 0.0)
        for record in provider.records if record.get("reused") is True
    )
    calls_path = directory / "api_calls.json"
    calls_path.write_text(json.dumps(provider.records, indent=2, ensure_ascii=False))
    reused_count = sum(record.get("reused") is True for record in provider.records)
    row.update({
        "generation_status": status,
        "generation_error": error,
        "hidden_source": str(hidden_path),
        "raw_responses": [record.get("response", "") for record in provider.records],
        "rollouts": rollouts,
        "invariants": invariants,
        "native_verified": native_verified,
        "n_rollouts": N_ROLLOUTS,
        "extension_protocol": EXTENSION_PROTOCOL,
        "api_calls_artifact": str(calls_path),
        "reuse_source_method": SOURCE_METHOD if reused_count else None,
        "reuse_source_artifact": reuse_source if reused_count else None,
        "reused_api_call_count": reused_count,
        "fresh_api_call_count": len(provider.records) - reused_count,
        "observed_extension_wall_seconds": observed_wall,
        # R10 algorithmic cost includes reused calls' original measured latency.
        "generation_seconds": observed_wall + reused_seconds,
        "generation_time_accounting": "observed_wall_plus_reused_call_wall",
        **_usage(provider.records),
    })
    return row


def generate_all(root: Path, workers: int, retry_failed: bool) -> None:
    ensure_frama_c_available()
    tasks = discover_tasks()
    destination = event_path(root, METHOD)
    existing = latest_rows([destination])
    source_rows = latest_rows([event_path(root, SOURCE_METHOD)])
    pending = []
    for task in tasks:
        old = existing.get(task.key(METHOD))
        if old and old.get("generation_status") == "completed":
            compatible = int(old.get("api_call_count") or 0) == N_ROLLOUTS
            if compatible:
                if old.get("extension_protocol") != EXTENSION_PROTOCOL:
                    updated = dict(old)
                    updated.update({
                        "extension_protocol": EXTENSION_PROTOCOL,
                    })
                    append_jsonl(destination, updated)
                    existing[task.key(METHOD)] = updated
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
            pool.submit(
                _run_one,
                task,
                root,
                source_rows.get(task.key(SOURCE_METHOD)),
            ): task
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
        "houdini": True,
        "reuse_source_method": SOURCE_METHOD,
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
