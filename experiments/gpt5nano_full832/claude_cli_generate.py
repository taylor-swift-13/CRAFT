"""Generate the paired RQ2 rollout with the standard Claude Code client.

Some compatible providers accept Claude traffic only from the official CLI.
This adapter keeps that transport detail separate while preserving the shared
target-hidden prompt, Houdini/WP pipeline, result schema, and final judge.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import subprocess
import time

from rl_pipeline.common import prompts
from rl_pipeline.inference import InferenceFramework, LLMRolloutProvider

from .common import (
    Task,
    append_jsonl,
    base_row,
    discover_tasks,
    ensure_frama_c_available,
    generation_complete,
    latest_rows,
    token_fields,
)
from .run import event_path, new_attempt_dir, save_hidden_source


METHOD = "loopgym_r1_houdini"
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8192
CLI_PROTOCOL = "claude_code_target_hidden_no_tools_no_thinking_cap8192_v2"


class ClaudeCLI:
    def __init__(self, model: str = MODEL, retries: int = 5):
        self.model = model
        self.retries = retries
        self.records: list[dict] = []

    def chat(self, user_prompt: str) -> str:
        command = [
            "claude", "-p",
            "--model", self.model,
            "--output-format", "json",
            "--permission-mode", "bypassPermissions",
            "--system-prompt", prompts.system_prompt(),
            "--tools", "",
            "--exclude-dynamic-system-prompt-sections",
            "--setting-sources", "",
            "--no-session-persistence",
        ]
        env = os.environ.copy()
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(MAX_TOKENS)
        # Sonnet 4.6 otherwise uses adaptive reasoning in Claude Code.  The
        # official non-thinking configuration first disables adaptive mode,
        # then sets the fixed thinking budget to zero.  On third-party
        # providers this omits the thinking parameter entirely.
        env["CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING"] = "1"
        env["MAX_THINKING_TOKENS"] = "0"
        started = time.perf_counter()
        errors = []
        payload = None
        for attempt in range(1, self.retries + 1):
            try:
                completed = subprocess.run(
                    command,
                    input=user_prompt,
                    text=True,
                    capture_output=True,
                    timeout=600,
                    env=env,
                    check=False,
                )
                if completed.returncode != 0:
                    raise RuntimeError(
                        completed.stderr.strip() or completed.stdout.strip()
                    )
                payload = json.loads(completed.stdout)
                if payload.get("is_error"):
                    raise RuntimeError(str(payload.get("result") or payload))
                break
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}"[:1000])
                if attempt == self.retries:
                    raise
                time.sleep(min(2 ** (attempt - 1), 16))
        assert payload is not None

        model_usage = (payload.get("modelUsage") or {}).get(self.model) or {}
        input_tokens = int(model_usage.get("inputTokens") or 0)
        cache_creation = int(model_usage.get("cacheCreationInputTokens") or 0)
        cache_read = int(model_usage.get("cacheReadInputTokens") or 0)
        output_tokens = int(model_usage.get("outputTokens") or 0)
        prompt_tokens = input_tokens + cache_creation + cache_read
        total_tokens = prompt_tokens + output_tokens
        result = str(payload.get("result") or "")
        self.records.append({
            "transport": "claude_code_cli",
            "cli_protocol": CLI_PROTOCOL,
            "model": self.model,
            "max_completion_tokens": MAX_TOKENS,
            "reasoning_effort": None,
            "thinking": "disabled",
            "disable_adaptive_thinking": True,
            "max_thinking_tokens": 0,
            "tools_enabled": False,
            "session_persistence": False,
            "attempts": len(errors) + 1,
            "retry_errors": errors,
            "response": result,
            "stop_reason": payload.get("stop_reason"),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            "seconds": time.perf_counter() - started,
            "provider_duration_api_ms": payload.get("duration_api_ms"),
            "provider_cost_usd": payload.get("total_cost_usd"),
            "token_accounting": "exact_provider_cli_usage",
        })
        return result

    def usage(self) -> dict:
        return token_fields(
            prompt=sum(r["prompt_tokens"] for r in self.records),
            completion=sum(r["completion_tokens"] for r in self.records),
            total=sum(r["total_tokens"] for r in self.records),
            calls=len(self.records),
            accounting="exact_provider_cli_usage",
        )


def _run_one(task: Task, root: Path) -> dict:
    row = base_row(METHOD, task)
    directory = new_attempt_dir(root, METHOD, task)
    hidden_path = save_hidden_source(directory, task)
    client = ClaudeCLI()
    started = time.perf_counter()
    try:
        result = InferenceFramework(
            task.source_path.read_text(errors="ignore"),
            rollout_provider=LLMRolloutProvider(client.chat),
            n_rollouts=1,
        ).run()
        status, error = "completed", None
        invariants = result.final_invariants
        rollouts = result.rollouts
        native_verified = result.verified
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        invariants, rollouts, native_verified = [], [], None

    calls_path = directory / "api_calls.json"
    calls_path.write_text(json.dumps(client.records, indent=2, ensure_ascii=False))
    row.update({
        "generation_status": status,
        "generation_error": error,
        "hidden_source": str(hidden_path),
        "raw_responses": [r["response"] for r in client.records],
        "rollouts": rollouts,
        "invariants": invariants,
        "native_verified": native_verified,
        "generation_seconds": time.perf_counter() - started,
        "api_calls_artifact": str(calls_path),
        "claude_cli_protocol": CLI_PROTOCOL,
        "thinking": "disabled",
        **client.usage(),
    })
    return row


def _compatible(row: dict | None) -> bool:
    return bool(
        row
        and row.get("generation_status") == "completed"
        and row.get("claude_cli_protocol") == CLI_PROTOCOL
        and row.get("model") == MODEL
        and int(row.get("api_call_count") or 0) == 1
    )


def generate(root: Path, workers: int, retry_failed: bool, max_tasks: int | None) -> None:
    ensure_frama_c_available()
    if not (os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")):
        raise RuntimeError("ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY is required")
    os.environ["CRAFT_MODEL"] = MODEL
    tasks = discover_tasks()
    destination = event_path(root, METHOD)
    existing = latest_rows([destination])
    pending = []
    for task in tasks:
        old = existing.get(task.key(METHOD))
        if _compatible(old):
            continue
        if old and not retry_failed and generation_complete(old):
            continue
        pending.append(task)
    if max_tasks is not None:
        pending = pending[:max_tasks]
    print(f"Claude CLI reusable={len(tasks)-len(pending)} pending={len(pending)}", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_one, task, root): task for task in pending}
        for index, future in enumerate(as_completed(futures), 1):
            task = futures[future]
            row = future.result()
            append_jsonl(destination, row)
            print(
                f"[{index}/{len(pending)}] {task.suite}/{task.case_id} "
                f"{row['generation_status']} verified={row.get('native_verified')} "
                f"seconds={row.get('generation_seconds', 0):.2f}",
                flush=True,
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-tasks", type=int)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    generate(args.results_root, args.workers, args.retry_failed, args.max_tasks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
