"""Strict target-hidden reproduction of Loopy's k=8 + repair workflow.

The original Loopy schedule first samples 15 completions, evaluates ten random
8-completion combinations with Houdini, and, if none verifies, performs up to
seven sequential repair calls.  Every model-facing program and every repair
diagnostic here is derived from the target-hidden source.  The untouched target
is used only as the verifier's stop/final-judgment condition and is never placed
in a model prompt.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import random
import time

import openai

from rl_pipeline.common.program import parse_program
from rl_pipeline.common.state import dedup_normalized, extract_invariants
from rl_pipeline.reward import annotate, filters

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
    sha256_text,
    token_fields,
)
from .run import event_path, new_attempt_dir, save_hidden_source


METHOD = "loopy"
DEFAULT_LOOPY_ROOT = Path("/home/yangfp/Loopy")
INITIAL_COMPLETIONS = 15
INITIAL_BATCH_SIZE = 5
COMBINE_K = 8
SHUFFLE_TIMES = 10
MAX_REPAIRS = 7
TEMPERATURE = 0.7
TOP_P = 1.0
MAX_TOKENS = 8192
REASONING_EFFORT = os.environ.get("LOOPY_REASONING_EFFORT", "none").lower()
if REASONING_EFFORT not in {"none", "low", "medium", "high"}:
    raise ValueError(
        "LOOPY_REASONING_EFFORT must be none, low, medium, or high"
    )
LOOPY_PROTOCOL = (
    "loopy_n15_as_3x5_k8_shuffle10_repair7_target_hidden_"
    f"{REASONING_EFFORT}_cap8192_top_p1_v5"
)
# The shared endpoint can remain saturated for several minutes.  Low worker
# counts are the primary rate-limit control; retries preserve resumability.
MAX_API_ATTEMPTS = 100
API_TIMEOUT_SECONDS = 600.0


def _render(template: str, **values: str) -> str:
    for name, value in values.items():
        template = template.replace("{{ " + name + " }}", value)
        template = template.replace("{{" + name + "}}", value)
    return template


def _official_messages(loopy_root: Path, hidden_source: str) -> list[dict[str, str]]:
    system = (loopy_root / "templates" / "simplified_system_message.txt").read_text()
    user = _render(
        (loopy_root / "templates" / "simplified_prompt_with_nudges.txt").read_text(),
        code=hidden_source,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _repair_messages(
    loopy_root: Path,
    hidden_annotated_source: str,
    inductiveness_feedback: str,
) -> list[dict[str, str]]:
    system = (loopy_root / "templates" / "healing_system_message.txt").read_text()
    user = _render(
        (loopy_root / "templates" / "healing_prompt.txt").read_text(),
        code=hidden_annotated_source,
        error=inductiveness_feedback,
    )
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


def _official_invariants(response: str) -> list[str]:
    """Match Loopy's first-valid fenced-code-block extraction rule."""
    lines = response.splitlines()
    fences = [index for index, line in enumerate(lines) if "```" in line]
    if len(fences) < 2:
        return []
    if len(fences) % 2:
        fences = fences[:-1]
    for index in range(0, len(fences), 2):
        snippet = "\n".join(lines[fences[index] + 1:fences[index + 1]])
        invariants = extract_invariants(snippet, max_invariants=1000)
        if invariants:
            return invariants
    return []


def _completion_details(usage):
    return getattr(usage, "completion_tokens_details", None) if usage else None


def _request(
    client,
    *,
    model: str,
    messages: list[dict[str, str]],
    n: int,
    phase: str,
) -> tuple[list[str], dict]:
    request = {
        "model": model,
        "messages": messages,
        "n": n,
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "max_completion_tokens": MAX_TOKENS,
        "reasoning_effort": REASONING_EFFORT,
    }
    started = time.perf_counter()
    response = None
    last_error = None
    attempts = 0
    retry_errors = []
    for attempts in range(1, MAX_API_ATTEMPTS + 1):
        try:
            response = client.chat.completions.create(**request)
            break
        except (
            openai.RateLimitError,
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.InternalServerError,
        ) as exc:
            last_error = exc
            retry_errors.append(type(exc).__name__)
            # Some OpenAI-compatible gateways incorrectly wrap invalid
            # request errors as HTTP 429.  Retrying an unsupported parameter
            # forever is not rate-limit recovery, so fail this request
            # immediately and surface the real provider message.
            if "unsupported parameter" in str(exc).lower():
                break
            delay = (
                300
                if isinstance(exc, openai.RateLimitError)
                else min(5 * (2 ** (attempts - 1)), 60)
            )
            print(
                f"Loopy {phase}: attempt {attempts} failed with "
                f"{type(exc).__name__}; backing off {delay}s",
                flush=True,
            )
            if attempts == MAX_API_ATTEMPTS:
                break
            time.sleep(delay)
    if response is None:
        raise RuntimeError(
            f"Loopy {phase} request failed after {attempts} attempts: {last_error}"
        )

    choices = list(response.choices)
    if len(choices) != n:
        raise RuntimeError(
            f"Loopy {phase} requested {n} choices but received {len(choices)}"
        )
    texts = [_message_text(choice.message) for choice in choices]
    usage = getattr(response, "usage", None)
    details = _completion_details(usage)
    record = {
        "phase": phase,
        "model": model,
        "prompt_sha256": sha256_text(
            json.dumps(messages, ensure_ascii=False, sort_keys=True)
        ),
        "target_hidden": True,
        "requested_n": n,
        "choice_count": len(texts),
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "max_completion_tokens": MAX_TOKENS,
        "reasoning_effort": REASONING_EFFORT,
        "finish_reasons": [getattr(choice, "finish_reason", None) for choice in choices],
        "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
        "completion_tokens": getattr(usage, "completion_tokens", None) if usage else None,
        "reasoning_tokens": getattr(details, "reasoning_tokens", None) if details else None,
        "total_tokens": getattr(usage, "total_tokens", None) if usage else None,
        "api_call_count": 1,
        "token_accounting": "exact" if usage else "unavailable",
        "seconds": time.perf_counter() - started,
        "attempts": attempts,
        "retry_errors": retry_errors,
    }
    return texts, record


def _aggregate_usage(records: list[dict]) -> dict:
    def total(field: str):
        values = [record.get(field) for record in records]
        return sum(values) if values and all(value is not None for value in values) else None

    exact = bool(records) and all(
        record.get("token_accounting") == "exact" for record in records
    )
    return {
        **token_fields(
            prompt=total("prompt_tokens"),
            completion=total("completion_tokens"),
            total=total("total_tokens"),
            calls=len(records),
            accounting="exact" if exact else "unavailable",
        ),
        "reasoning_tokens": total("reasoning_tokens"),
    }


def _candidate_rollouts(
    rollouts: list[list[str]], seed: int
) -> list[tuple[list[int], list[list[str]]]]:
    rng = random.Random(seed)
    candidates = []
    indices = list(range(len(rollouts)))
    for _ in range(SHUFFLE_TIMES):
        shuffled = list(indices)
        rng.shuffle(shuffled)
        selected = shuffled[:COMBINE_K]
        candidates.append((selected, [rollouts[index] for index in selected]))
    return candidates


def _run_houdini_candidate(task: Task, masked_program, invariant_filter, selected):
    union = dedup_normalized(clause for rollout in selected for clause in rollout)
    filter_started = time.perf_counter()
    survivors = invariant_filter.filter(masked_program, 0, union, positives=None)
    filter_seconds = time.perf_counter() - filter_started
    judgment = judge_invariants(task, survivors)
    return union, survivors, filter_seconds, judgment


def _inductiveness_feedback(union: list[str], survivors: list[str]) -> str:
    survivor_keys = set(dedup_normalized(survivors))
    lines = []
    for invariant in union:
        if invariant in survivor_keys:
            lines.append(f"loop invariant {invariant} is inductive.")
        else:
            lines.append(
                f"loop invariant {invariant} is not inductive and was removed by Houdini."
            )
    if not lines:
        lines.append("No valid loop invariant was found in the candidate.")
    # No assertion, postcondition, target text, or target-specific WP message is
    # included.  This generic signal only asks the model to strengthen its set.
    lines.append(
        "The candidate is not yet sufficient. Generate a stronger inductive "
        "invariant set using only the target-hidden program above."
    )
    return "\n".join(lines)


def _generate(task: Task, root: Path, loopy_root: Path) -> dict:
    row = base_row(METHOD, task)
    row["reasoning_effort"] = REASONING_EFFORT
    directory = new_attempt_dir(root, METHOD, task)
    hidden_path = save_hidden_source(directory, task)
    model = os.environ.get("CRAFT_MODEL") or os.environ.get(
        "LOOPGYM_MODEL", "gpt-5-nano"
    )
    base_url = os.environ.get("OPENAI_BASE_URL", "https://yunwu.ai/v1")
    client = openai.OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=base_url,
        max_retries=0,
        timeout=API_TIMEOUT_SECONDS,
    )
    masked_program = parse_program(task.hidden_source)
    invariant_filter = filters.auto_filter()
    seed = int(task.hidden_source_sha256[:16], 16)

    api_records: list[dict] = []
    response_log: dict = {"initial": [], "repairs": []}
    calls_path = directory / "api_calls.json"
    responses_path = directory / "responses.json"

    def checkpoint() -> None:
        calls_path.write_text(json.dumps(api_records, indent=2, ensure_ascii=False))
        responses_path.write_text(
            json.dumps(response_log, indent=2, ensure_ascii=False)
        )

    candidate_summaries = []
    repair_summaries = []
    total_filter_seconds = 0.0
    total_judge_seconds = 0.0
    started = time.perf_counter()

    initial_responses = []
    initial_messages = _official_messages(loopy_root, task.hidden_source)
    for batch_index in range(INITIAL_COMPLETIONS // INITIAL_BATCH_SIZE):
        batch_responses, batch_record = _request(
            client,
            model=model,
            messages=initial_messages,
            n=INITIAL_BATCH_SIZE,
            phase=f"initial_batch_{batch_index + 1}",
        )
        api_records.append(batch_record)
        initial_responses.extend(batch_responses)
        response_log["initial"] = initial_responses
        checkpoint()
    rollouts = [
        _official_invariants(response)
        for response in initial_responses
    ]

    candidate_runs = []
    for candidate_index, (indices, selected) in enumerate(
        _candidate_rollouts(rollouts, seed)
    ):
        union, survivors, filter_seconds, judgment = _run_houdini_candidate(
            task, masked_program, invariant_filter, selected
        )
        total_filter_seconds += filter_seconds
        total_judge_seconds += float(judgment.get("judge_seconds") or 0.0)
        candidate_runs.append((union, survivors, judgment))
        candidate_summaries.append({
            "candidate_index": candidate_index,
            "rollout_indices": indices,
            "union_invariant_count": len(union),
            "survivor_count": len(survivors),
            "verified": judgment.get("verified"),
            "filter_seconds": filter_seconds,
            "judge_seconds": judgment.get("judge_seconds"),
        })

    verified_initial = next(
        (index for index, run in enumerate(candidate_runs) if run[2].get("verified") is True),
        None,
    )
    if verified_initial is not None:
        chosen_index = verified_initial
        success_phase = "initial_k8"
    else:
        chosen_index = random.Random(seed ^ 0x5A17).randrange(len(candidate_runs))
        success_phase = None

    current_union, final_survivors, final_judgment = candidate_runs[chosen_index]
    current_hidden_code = annotate.build_annotated(
        masked_program, current_union, 0
    )

    repair_count = 0
    while success_phase is None and repair_count < MAX_REPAIRS:
        feedback = _inductiveness_feedback(current_union, final_survivors)
        repair_count += 1
        repair_responses, repair_record = _request(
            client,
            model=model,
            messages=_repair_messages(loopy_root, current_hidden_code, feedback),
            n=1,
            phase=f"repair_{repair_count}",
        )
        api_records.append(repair_record)
        response_log["repairs"].append(repair_responses[0])
        checkpoint()
        repair_rollout = _official_invariants(repair_responses[0])
        current_union, final_survivors, filter_seconds, final_judgment = (
            _run_houdini_candidate(
                task, masked_program, invariant_filter, [repair_rollout]
            )
        )
        total_filter_seconds += filter_seconds
        total_judge_seconds += float(final_judgment.get("judge_seconds") or 0.0)
        repair_summaries.append({
            "repair_index": repair_count,
            "candidate_invariant_count": len(current_union),
            "survivor_count": len(final_survivors),
            "verified": final_judgment.get("verified"),
            "feedback_sha256": sha256_text(feedback),
            "filter_seconds": filter_seconds,
            "judge_seconds": final_judgment.get("judge_seconds"),
        })
        current_hidden_code = annotate.build_annotated(
            masked_program, current_union, 0
        )
        if final_judgment.get("verified") is True:
            success_phase = f"repair_{repair_count}"

    checkpoint()
    orchestration_path = directory / "orchestration.json"
    orchestration_path.write_text(json.dumps({
        "loopy_protocol": LOOPY_PROTOCOL,
        "shuffle_seed": seed,
        "candidate_summaries": candidate_summaries,
        "chosen_candidate_index": chosen_index,
        "repair_summaries": repair_summaries,
        "success_phase": success_phase,
    }, indent=2, ensure_ascii=False))

    row.update({
        "generation_status": "completed",
        "generation_error": None,
        "generation_eligible": True,
        "hidden_source": str(hidden_path),
        "native_tool": "Loopy",
        "native_tool_root": str(loopy_root),
        "loopy_protocol": LOOPY_PROTOCOL,
        "native_budget": {
            "initial_completions": INITIAL_COMPLETIONS,
            "initial_batch_size": INITIAL_BATCH_SIZE,
            "initial_api_calls": INITIAL_COMPLETIONS // INITIAL_BATCH_SIZE,
            "combine_k": COMBINE_K,
            "shuffle_times": SHUFFLE_TIMES,
            "max_repairs": MAX_REPAIRS,
            "houdini": True,
        },
        "decoding": {
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "max_completion_tokens": MAX_TOKENS,
            "reasoning_effort": REASONING_EFFORT,
        },
        "raw_response_artifact": str(responses_path),
        "api_call_artifact": str(calls_path),
        "orchestration_artifact": str(orchestration_path),
        "initial_rollout_count": len(rollouts),
        "rollout_count": len(rollouts) + repair_count,
        "candidate_count": len(candidate_runs),
        "repair_count": repair_count,
        "success_phase": success_phase,
        "invariants": final_survivors,
        "invariant_count": len(final_survivors),
        "verified": final_judgment.get("verified"),
        "judge_error": final_judgment.get("judge_error"),
        "generation_seconds": time.perf_counter() - started,
        "api_seconds": sum(float(record["seconds"]) for record in api_records),
        "api_attempts": sum(int(record["attempts"]) for record in api_records),
        "filter_seconds": total_filter_seconds,
        "judge_seconds": total_judge_seconds,
        **_aggregate_usage(api_records),
    })
    return row


def _compatible_generation(row: dict | None) -> bool:
    return bool(
        row
        and row.get("generation_status") == "completed"
        and row.get("loopy_protocol") == LOOPY_PROTOCOL
        and row.get("reasoning_effort") == REASONING_EFFORT
        and isinstance(row.get("decoding"), dict)
        and row["decoding"].get("reasoning_effort") == REASONING_EFFORT
        and int(row.get("initial_rollout_count") or 0) == INITIAL_COMPLETIONS
        and int(row.get("candidate_count") or 0) == SHUFFLE_TIMES
        and 3 <= int(row.get("api_call_count") or 0) <= 3 + MAX_REPAIRS
    )


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
        if _compatible_generation(old):
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
                    "loopy_protocol": LOOPY_PROTOCOL,
                    "invariants": [],
                    "generation_seconds": None,
                    **token_fields(accounting="unavailable"),
                })
            append_jsonl(path, row)
            print(
                f"[{index}/{len(pending)}] {task.suite}/{task.case_id} "
                f"status={row['generation_status']} verified={row.get('verified')} "
                f"repairs={row.get('repair_count')} attempts={row.get('api_attempts')}",
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
            "completed": sum(row.get("generation_status") == "completed" for row in group),
            "verified": sum(row.get("verified") is True for row in group),
        }
    completed = [
        row for row in selected
        if row.get("generation_status") == "completed"
        and row.get("loopy_protocol") == LOOPY_PROTOCOL
    ]

    def mean(field):
        values = [row.get(field) for row in completed if row.get(field) is not None]
        return sum(values) / len(values) if values else None

    summary = {
        "method": METHOD,
        "loopy_protocol": LOOPY_PROTOCOL,
        "model": os.environ.get("CRAFT_MODEL") or os.environ.get(
            "LOOPGYM_MODEL", "gpt-5-nano"
        ),
        "rows": len(selected),
        "compatible_completed": len(completed),
        "verified": sum(row.get("verified") is True for row in completed),
        "by_suite": by_suite,
        "mean_prompt_tokens": mean("prompt_tokens"),
        "mean_completion_tokens": mean("completion_tokens"),
        "mean_reasoning_tokens": mean("reasoning_tokens"),
        "mean_total_tokens": mean("total_tokens"),
        "mean_api_call_count": mean("api_call_count"),
        "mean_repair_count": mean("repair_count"),
        "mean_generation_seconds": mean("generation_seconds"),
        "mean_api_seconds": mean("api_seconds"),
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
