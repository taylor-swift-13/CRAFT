"""Target-hidden Loopy evaluation with GPT-5-nano.

This adapter preserves Loopy's published invariant prompt, 15-completion
budget, completion union, and Houdini pruning.  The program shown to the model
has its target removed; the untouched target is restored only for the common
final judge.  API usage and wall time are recorded per task, and the JSONL
event stream is append-only and resumable.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import time

import openai

from rl_pipeline.common.program import parse_program
from rl_pipeline.common.state import dedup_normalized, extract_invariants
from rl_pipeline.reward import filters

from .common import (
    DEFAULT_RESULTS_ROOT,
    Task,
    append_jsonl,
    base_row,
    discover_tasks,
    ensure_frama_c_available,
    generation_complete,
    judge_invariants,
    latest_rows,
    read_jsonl,
    token_fields,
)
from .run import event_path, new_attempt_dir, save_hidden_source


METHOD = "loopy"
DEFAULT_LOOPY_ROOT = Path("/home/yangfp/Loopy")
N_COMPLETIONS = 15
TEMPERATURE = 0.7
TOP_P = 0.95
MAX_TOKENS = 1000
MAX_API_ATTEMPTS = 6


def _official_messages(loopy_root: Path, hidden_source: str) -> list[dict[str, str]]:
    system = (loopy_root / "templates" / "simplified_system_message.txt").read_text()
    user_template = (
        loopy_root / "templates" / "simplified_prompt_with_nudges.txt"
    ).read_text()
    # The official template is plain Jinja with one {{ code }} hole.  Remove
    # only the explicit chain-of-thought request; all inference runs in this
    # project use visible-answer-only/default API settings.
    user = user_template.replace("{{ code }}", hidden_source)
    user = user.replace(" Let's think step by step.", "")
    user = user.replace("Let's think step by step.", "")
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _message_text(message) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if not content:
        return ""
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
        elif getattr(item, "type", None) == "text":
            parts.append(str(getattr(item, "text", "") or ""))
    return "\n".join(parts)


def _generate(task: Task, root: Path, loopy_root: Path) -> dict:
    row = base_row(METHOD, task)
    row["reasoning_effort"] = None
    directory = new_attempt_dir(root, METHOD, task)
    hidden_path = save_hidden_source(directory, task)
    messages = _official_messages(loopy_root, task.hidden_source)
    model = os.environ.get("LOOPGYM_MODEL", "gpt-5-nano")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://yunwu.ai/v1")
    client = openai.OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=base_url,
    )

    request = {
        "model": model,
        "messages": messages,
        "n": N_COMPLETIONS,
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "max_completion_tokens": MAX_TOKENS,
    }
    generation_started = time.perf_counter()
    response = None
    last_error = None
    api_attempts = 0
    for api_attempts in range(1, MAX_API_ATTEMPTS + 1):
        try:
            response = client.chat.completions.create(**request)
            break
        except (openai.RateLimitError, openai.APITimeoutError,
                openai.APIConnectionError, openai.InternalServerError) as exc:
            last_error = exc
            if api_attempts == MAX_API_ATTEMPTS:
                break
            time.sleep(min(5 * (2 ** (api_attempts - 1)), 30))
    if response is None:
        raise RuntimeError(
            f"Loopy API request failed after {api_attempts} attempts: {last_error}"
        )
    generation_seconds = time.perf_counter() - generation_started
    raw_responses = [_message_text(choice.message) for choice in response.choices]
    rollouts = [extract_invariants(text, max_invariants=1000) for text in raw_responses]
    union = dedup_normalized(clause for rollout in rollouts for clause in rollout)

    filter_started = time.perf_counter()
    masked_program = parse_program(task.hidden_source)
    survivors = filters.auto_filter().filter(masked_program, 0, union, positives=None)
    filter_seconds = time.perf_counter() - filter_started
    judgment = judge_invariants(task, survivors)

    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
    completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
    total_tokens = getattr(usage, "total_tokens", None) if usage else None

    calls_path = directory / "api_call.json"
    calls_path.write_text(json.dumps({
        "model": model,
        "base_url": base_url,
        "parameters": {
            "n": N_COMPLETIONS,
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "max_completion_tokens": MAX_TOKENS,
            "reasoning_effort": "provider_default",
        },
        "finish_reasons": [choice.finish_reason for choice in response.choices],
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "seconds": generation_seconds,
        "attempts": api_attempts,
    }, indent=2, ensure_ascii=False))
    responses_path = directory / "responses.json"
    responses_path.write_text(json.dumps(raw_responses, indent=2, ensure_ascii=False))

    row.update({
        "generation_status": "completed",
        "generation_error": None,
        "generation_eligible": True,
        "hidden_source": str(hidden_path),
        "native_tool": "Loopy",
        "native_tool_root": str(loopy_root),
        "native_budget": {
            "completions": N_COMPLETIONS,
            "union": True,
            "houdini": True,
            "repair": False,
        },
        "decoding": {
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "max_completion_tokens": MAX_TOKENS,
            "reasoning_effort": "provider_default",
        },
        "raw_response_artifact": str(responses_path),
        "api_call_artifact": str(calls_path),
        "rollouts": rollouts,
        "rollout_count": len(rollouts),
        "union_invariants": union,
        "union_invariant_count": len(union),
        "invariants": judgment.pop("invariants"),
        "invariant_count": judgment.pop("invariant_count"),
        "generation_seconds": generation_seconds,
        "api_attempts": api_attempts,
        "filter_seconds": filter_seconds,
        **judgment,
        **token_fields(
            prompt=prompt_tokens,
            completion=completion_tokens,
            total=total_tokens,
            calls=1,
            accounting="exact" if usage else "unavailable",
        ),
    })
    return row


def generate(
    root: Path,
    loopy_root: Path,
    workers: int,
    retry_failed: bool,
    max_tasks: int | None,
) -> None:
    if not (loopy_root / "src" / "loopy.py").is_file():
        raise RuntimeError(f"Loopy is not installed at {loopy_root}")
    ensure_frama_c_available()
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required")
    tasks = discover_tasks()
    path = event_path(root, METHOD)
    existing = latest_rows([path])
    pending = []
    for task in tasks:
        old = existing.get(task.key(METHOD))
        if old and old.get("generation_status") == "completed":
            continue
        if old and not retry_failed and generation_complete(old):
            continue
        pending.append(task)
    if max_tasks is not None:
        pending = pending[:max_tasks]
    print(f"{METHOD}: pending={len(pending)} workers={workers}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_generate, task, root, loopy_root): task for task in pending
        }
        for index, future in enumerate(as_completed(futures), 1):
            task = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                row = base_row(METHOD, task)
                row.update({
                    "generation_status": "failed",
                    "generation_error": f"{type(exc).__name__}: {exc}",
                    "invariants": [],
                    "generation_seconds": None,
                    **token_fields(accounting="unavailable"),
                })
            append_jsonl(path, row)
            print(
                f"[{index}/{len(pending)}] {task.suite}/{task.case_id} "
                f"status={row['generation_status']} verified={row.get('verified')}",
                flush=True,
            )


def report(root: Path) -> None:
    rows = read_jsonl(event_path(root, METHOD))
    latest = {}
    for row in rows:
        latest[(row.get("suite"), row.get("case_id"))] = row
    selected = list(latest.values())
    by_suite = {}
    for suite in ("linear", "NLA_lipus", "Loopy"):
        group = [row for row in selected if row.get("suite") == suite]
        by_suite[suite] = {
            "completed": len(group),
            "verified": sum(row.get("verified") is True for row in group),
        }
    completed = [row for row in selected if row.get("generation_status") == "completed"]

    def mean(field):
        values = [row.get(field) for row in completed if row.get(field) is not None]
        return sum(values) / len(values) if values else None

    summary = {
        "method": METHOD,
        "model": os.environ.get("LOOPGYM_MODEL", "gpt-5-nano"),
        "rows": len(selected),
        "verified": sum(row.get("verified") is True for row in selected),
        "by_suite": by_suite,
        "mean_total_tokens": mean("total_tokens"),
        "mean_generation_seconds": mean("generation_seconds"),
        "mean_filter_seconds": mean("filter_seconds"),
        "mean_judge_seconds": mean("judge_seconds"),
    }
    output = root / "loopy_summary.json"
    output.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("generate", "report", "all"))
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--loopy-root", type=Path, default=DEFAULT_LOOPY_ROOT)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--max-tasks", type=int)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    if args.command in {"generate", "all"}:
        generate(
            args.results_root,
            args.loopy_root,
            args.workers,
            args.retry_failed,
            args.max_tasks,
        )
    if args.command in {"report", "all"}:
        report(args.results_root)


if __name__ == "__main__":
    main()
