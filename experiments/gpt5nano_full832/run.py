from __future__ import annotations

import argparse
import ast
import csv
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import sys
import time

from rl_pipeline.common import prompts
from rl_pipeline.common.program import parse_program
from rl_pipeline.common.state import extract_invariants
from rl_pipeline.inference import InferenceFramework, LLMRolloutProvider

from .api import RecordingChat
from .common import (
    DEFAULT_RESULTS_ROOT,
    METHODS,
    Task,
    append_jsonl,
    base_row,
    discover_tasks,
    ensure_frama_c_available,
    generation_complete,
    judge_invariants,
    latest_rows,
    load_protocol,
    protocol_sha256,
    read_jsonl,
    row_key,
    score_negative_rejection_many,
    token_fields,
    write_manifest,
)
from .native import run_autospec, run_sespec
from .samples import load_sample, load_sample_manifest, materialize_samples


RESTORED_TARGET_IDS = {
    ("linear", str(case_id))
    for case_id in (147, 148, 149, 212, 213, 214, 215, 216, 217, 222)
}


def event_path(root: Path, method: str) -> Path:
    return root / "events" / f"{method}.jsonl"


def artifact_dir(root: Path, method: str, task: Task) -> Path:
    return root / "artifacts" / method / task.suite / task.case_id


def new_attempt_dir(root: Path, method: str, task: Task) -> Path:
    directory = (
        artifact_dir(root, method, task)
        / f"attempt_{time.time_ns()}"
    )
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def save_hidden_source(directory: Path, task: Task) -> Path:
    path = directory / "input.hidden.c"
    path.write_text(task.hidden_source)
    return path


def _run_naive(task: Task, root: Path) -> dict:
    row = base_row("naive", task)
    directory = new_attempt_dir(root, "naive", task)
    hidden_path = save_hidden_source(directory, task)
    recorder = RecordingChat()
    started = time.perf_counter()
    try:
        prompt = prompts.GENERATE_PROMPT.format(program=task.hidden_source)
        response = recorder.chat(prompt)
        invariants = extract_invariants(response, max_invariants=20)
        status, error = "completed", None
    except Exception as exc:
        response, invariants = "", []
        status, error = "failed", f"{type(exc).__name__}: {exc}"
    calls_path = directory / "api_calls.json"
    calls_path.write_text(json.dumps(recorder.records, indent=2, ensure_ascii=False))
    row.update({
        "generation_status": status,
        "generation_error": error,
        "hidden_source": str(hidden_path),
        "raw_responses": [response] if response else [],
        "rollouts": [invariants],
        "invariants": invariants,
        "generation_seconds": time.perf_counter() - started,
        "api_calls_artifact": str(calls_path),
        **recorder.usage(),
    })
    return row


def _run_loopgym(task: Task, root: Path) -> dict:
    row = base_row("loopgym", task)
    directory = new_attempt_dir(root, "loopgym", task)
    hidden_path = save_hidden_source(directory, task)
    recorder = RecordingChat()
    started = time.perf_counter()
    try:
        provider = LLMRolloutProvider(chat_fn=recorder.chat)
        source = task.source_path.read_text(errors="ignore")
        result = InferenceFramework(
            source,
            rollout_provider=provider,
            n_rollouts=4,
            max_rerolls=1,
            m_refine=0,
        ).run()
        expected_calls = 4 * (result.reroll_count + 1)
        status = "completed" if len(recorder.records) == expected_calls else "failed"
        error = None if status == "completed" else (
            f"expected {expected_calls} API calls, recorded {len(recorder.records)}"
        )
        rollouts = result.rollouts
        invariants = result.final_invariants
        native_verified = result.verified
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        rollouts, invariants, native_verified = [], [], None
    calls_path = directory / "api_calls.json"
    calls_path.write_text(json.dumps(recorder.records, indent=2, ensure_ascii=False))
    row.update({
        "generation_status": status,
        "generation_error": error,
        "hidden_source": str(hidden_path),
        "raw_responses": [record["response"] for record in recorder.records],
        "rollouts": rollouts,
        "invariants": invariants,
        "native_verified": native_verified,
        "reroll_count": result.reroll_count if status == "completed" else None,
        "generation_seconds": time.perf_counter() - started,
        "api_calls_artifact": str(calls_path),
        **recorder.usage(),
    })
    return row


def generate_neural(method: str, root: Path, workers: int, retry_failed: bool) -> None:
    tasks = discover_tasks()
    path = event_path(root, method)
    existing = latest_rows([path])
    pending = []
    for task in tasks:
        old = existing.get(task.key(method))
        if old and old.get("generation_status") == "completed":
            continue
        if old and not retry_failed and generation_complete(old):
            continue
        pending.append(task)
    print(f"{method}: reusable={len(tasks)-len(pending)} pending={len(pending)}")
    if pending and not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            f"OPENAI_API_KEY is required for {method}'s {len(pending)} missing tasks"
        )
    runner = _run_naive if method == "naive" else _run_loopgym
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(runner, task, root): task for task in pending}
        for index, future in enumerate(as_completed(futures), 1):
            task = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                row = base_row(method, task)
                row.update({
                    "generation_status": "failed",
                    "generation_error": f"{type(exc).__name__}: {exc}",
                    "invariants": [],
                    "generation_seconds": None,
                    **token_fields(accounting="unavailable"),
                })
            append_jsonl(path, row)
            print(
                f"[{index}/{len(pending)}] {method} {task.suite}/{task.case_id} "
                f"{row['generation_status']}",
                flush=True,
            )


def generate_native(
    method: str,
    root: Path,
    workers: int,
    retry_failed: bool,
    *,
    autospec_root: Path,
    sespec_root: Path,
    timeout: int,
) -> None:
    tasks = discover_tasks()
    path = event_path(root, method)
    existing = latest_rows([path])
    pending = []
    for task in tasks:
        old = existing.get(task.key(method))
        if old and old.get("generation_status") == "completed":
            continue
        if old and not retry_failed and generation_complete(old):
            continue
        pending.append(task)
    print(f"{method}: reusable={len(tasks)-len(pending)} pending={len(pending)}")

    if method == "clause2inv":
        for index, task in enumerate(pending, 1):
            row = base_row(method, task)
            if task.suite != "Loopy":
                row.update({
                    "generation_status": "failed",
                    "generation_eligible": True,
                    "generation_error": "missing reusable native Clause2Inv result",
                    "invariants": [],
                    "generation_seconds": 0.0,
                    **token_fields(accounting="not_called"),
                })
            else:
                row.update({
                    "generation_status": "unsupported",
                    "generation_eligible": True,
                    "generation_error": (
                        "native Clause2Inv requires precomputed Code2Inv SMT "
                        "transition VCs; the Loopy corpus has no compatible VCs"
                    ),
                    "invariants": [],
                    "generation_seconds": 0.0,
                    **token_fields(
                        prompt=0, completion=0, total=0, calls=0,
                        accounting="not_called",
                    ),
                })
            append_jsonl(path, row)
            print(f"[{index}/{len(pending)}] clause2inv {task.suite}/{task.case_id} "
                  f"{row['generation_status']}")
        return

    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for native tool generation")

    def run_one(task: Task):
        directory = new_attempt_dir(root, method, task)
        if method == "autospec":
            generated = run_autospec(
                task, directory, autospec_root=autospec_root, timeout=timeout
            )
        else:
            generated = run_sespec(
                task, directory, sespec_root=sespec_root, timeout=timeout
            )
        row = base_row(method, task)
        row.update({"generation_eligible": True, **generated})
        return row

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_one, task): task for task in pending}
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
                f"{row['generation_status']}",
                flush=True,
            )


def _python_string_constant(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                value = ast.literal_eval(node.value)
                if isinstance(value, str):
                    return value
    raise KeyError(f"{name} not found in {path}")


def _clause2inv_token_estimate(log_path: Path, program_path: Path) -> dict:
    """Conservative log-based estimate; old runs did not persist API usage."""
    if not log_path.exists():
        return token_fields(accounting="unavailable")
    text = log_path.read_text(errors="ignore")
    # Each printed Python set immediately after the model name is one accepted
    # clause-list response. Failed JSON repair responses were not persisted, so
    # this is a lower-bound estimate and is explicitly not labelled exact.
    response_lines = [
        line for line in text.splitlines()
        if line.startswith("{") and line.endswith("}") and "'" in line
    ]
    calls = len(response_lines) or None
    try:
        import tiktoken
        encoding = tiktoken.get_encoding("cl100k_base")
        generator_root = Path("/home/yangfp/Clause2Inv/generator")
        system = _python_string_constant(
            generator_root / "re_clause_generater.py", "SYSTEM_PROMPT"
        )
        schema = _python_string_constant(
            generator_root / "re_clause_generater.py", "JSON_SCHEMA"
        )
        response_instruction = _python_string_constant(
            generator_root / "base_agent.py", "SYSTEM_PROMPT_JSON_INSTRUCTION"
        )
        program = program_path.read_text(errors="ignore")
        # Match Clause2Inv's old target-hidden replacement closely enough for a
        # reproducible lower-bound estimate; usage was not persisted by that run.
        program = re.sub(
            r"\bassert\s*\([^;]*\)\s*;",
            "/* verification target hidden */",
            program,
        )
        prompt = system.format(
            program=program,
            attention="pre-conditions and loop body",
        ) + response_instruction.format(json_schema=schema)
        prompt_per_call = len(encoding.encode(prompt))
        completion = 0
        for line in response_lines:
            try:
                clauses = sorted(ast.literal_eval(line))
                response = json.dumps({"clause_list": clauses})
            except Exception:
                response = line
            completion += len(encoding.encode(response))
        prompt_tokens = prompt_per_call * len(response_lines)
        total = prompt_tokens + completion
    except Exception:
        prompt_tokens = None
        completion = None
        total = None
    return token_fields(
        prompt=prompt_tokens,
        completion=completion,
        total=total,
        calls=calls,
        accounting="estimated" if calls is not None else "unavailable",
    )


def _legacy_inference_token_estimate(
    task: Task, invariants: list[str], calls: int
) -> dict:
    """Reproducible lower bound for legacy runs whose aggregate usage was lost."""
    try:
        import tiktoken
        encoding = tiktoken.get_encoding("cl100k_base")
        user_prompt = prompts.GENERATE_PROMPT.format(program=task.hidden_source)
        prompt_per_call = len(encoding.encode(prompts.system_prompt())) + len(
            encoding.encode(user_prompt)
        )
        visible_survivors = "\n".join(
            f"loop invariant {invariant};" for invariant in invariants
        )
        completion_lower_bound = len(encoding.encode(visible_survivors))
        prompt_tokens = prompt_per_call * calls
        total = prompt_tokens + completion_lower_bound
    except Exception:
        return token_fields(accounting="unavailable", calls=calls)
    return token_fields(
        prompt=prompt_tokens,
        completion=completion_lower_bound,
        total=total,
        calls=calls,
        accounting="estimated",
    )


def import_legacy_inference(root: Path, source_root: Path) -> tuple[int, int]:
    """Reuse the two original closed-book gpt-5-nano inference batches."""
    audit_path = Path(__file__).with_name("legacy_batches.json")
    audit = json.loads(audit_path.read_text())
    tasks = {(task.suite, task.case_id): task for task in discover_tasks(source_root)}
    counts = {"naive": 0, "loopgym": 0}
    for method, metadata in audit.items():
        source = REPO_ROOT / metadata["source"]
        batch_id = f"legacy-{method}-366-20260724"
        batch_seconds = float(metadata["batch_seconds"])
        if not source.exists():
            continue
        destination = event_path(root, method)
        existing = latest_rows([destination])
        for old in read_jsonl(source):
            input_path = Path(str(old["input"]))
            suite = input_path.parent.name
            case_id = input_path.stem
            task = tasks[(suite, case_id)]
            if task.key(method) in existing:
                continue
            invariants = list(old.get("invariants") or [])
            rerolls = int(old.get("reroll_count") or 0)
            calls = 1 if method == "naive" else 4 * (rerolls + 1)
            row = base_row(method, task)
            row.update({
                "generation_status": (
                    "completed" if old.get("error") is None else "failed"
                ),
                "generation_eligible": True,
                "generation_error": old.get("error"),
                "invariants": invariants,
                "raw_responses": None,
                "rollouts": None,
                "native_verified": old.get("verified"),
                "reroll_count": rerolls if method == "loopgym" else 0,
                "generation_seconds": None,
                "generation_time_accounting": "unavailable_per_task",
                "generation_batch_id": batch_id,
                "generation_batch_seconds": batch_seconds,
                "generation_batch_audit": str(audit_path),
                "reuse_source": str(source),
                "reuse_evidence": (
                    "gpt-5-nano batch used LLMRolloutProvider; its framework "
                    "stripped targets before every model call"
                ),
                "token_estimate_kind": (
                    "cl100k prompt plus surviving-invariant completion lower bound"
                ),
                **_legacy_inference_token_estimate(task, invariants, calls),
            })
            append_jsonl(destination, row)
            existing[task.key(method)] = row
            counts[method] += 1
    return counts["naive"], counts["loopgym"]


def reuse_legacy_inference_judges(root: Path) -> int:
    """Reuse old Frama-C/WP verdicts except where the restored target changed."""
    sources = {
        "naive": REPO_ROOT / "results" / "loopgym_pass1_gpt5nano_nohoudini.jsonl",
        "loopgym": REPO_ROOT / "results" / "loopgym_rollout4_houdini_gpt5nano.jsonl",
    }
    count = 0
    for method, source in sources.items():
        if not source.exists():
            continue
        destination = event_path(root, method)
        current = latest_rows([destination])
        for old in read_jsonl(source):
            input_path = Path(str(old["input"]))
            suite, case_id = input_path.parent.name, input_path.stem
            if (suite, case_id) in RESTORED_TARGET_IDS:
                continue
            matches = [
                row for row in current.values()
                if row.get("method") == method
                and row.get("suite") == suite
                and row.get("case_id") == case_id
                and row.get("protocol_sha256") == protocol_sha256()
            ]
            if not matches:
                continue
            row = dict(matches[-1])
            if "verified" in row:
                continue
            row.update({
                "verified": old.get("verified"),
                "judge_error": old.get("error"),
                "judge_seconds": None,
                "judge_time_accounting": "unavailable_from_reused_native_judge",
                "judge_reuse_source": str(source),
                "judge_reuse_evidence": (
                    "the original batch used the same InferenceFramework final "
                    "Frama-C/WP verification on the unchanged target-bearing source"
                ),
            })
            append_jsonl(destination, row)
            current[row_key(row)] = row
            count += 1
    return count


def attach_legacy_batch_audit(root: Path) -> int:
    audit_path = Path(__file__).with_name("legacy_batches.json")
    changed = 0
    for method in ("naive", "loopgym"):
        destination = event_path(root, method)
        for row in latest_rows([destination]).values():
            if row.get("protocol_sha256") != protocol_sha256():
                continue
            if not row.get("generation_batch_id"):
                continue
            if row.get("generation_batch_audit"):
                continue
            updated = dict(row)
            updated["generation_batch_audit"] = str(audit_path)
            append_jsonl(destination, updated)
            changed += 1
    return changed


def normalize_shared_score_time(root: Path) -> int:
    """Migrate early shared-score rows from first-row to equal allocation."""
    current = latest_rows([event_path(root, method) for method in METHODS])
    groups: dict[tuple[str, str], list[tuple[Path, dict]]] = {}
    for row in current.values():
        if row.get("protocol_sha256") != protocol_sha256():
            continue
        if int(row.get("negative_score_shared_batch_size") or 1) <= 1:
            continue
        groups.setdefault(
            (str(row.get("suite")), str(row.get("case_id"))), []
        ).append((event_path(root, str(row["method"])), row))
    changed = 0
    for entries in groups.values():
        if all(
            row.get("negative_score_time_accounting")
            == "shared_equal_allocation"
            for _path, row in entries
        ):
            continue
        total = sum(
            float(row.get("negative_score_seconds") or 0.0)
            for _path, row in entries
        )
        allocation = total / len(entries)
        for path, row in entries:
            updated = dict(row)
            updated.update({
                "negative_score_seconds": allocation,
                "negative_score_time_accounting": "shared_equal_allocation",
                "negative_score_shared_batch_size": len(entries),
            })
            append_jsonl(path, updated)
            changed += 1
    return changed


def attach_fixed_sample_metadata(
    root: Path, sample_index: dict[tuple[str, str], dict]
) -> int:
    """Bind already-computed deterministic scores to their materialized sample."""
    changed = 0
    for method in METHODS:
        destination = event_path(root, method)
        for row in latest_rows([destination]).values():
            if row.get("protocol_sha256") != protocol_sha256():
                continue
            if row.get("negative_score_status") not in {
                "completed", "not_applicable"
            }:
                continue
            sample = sample_index.get(
                (str(row.get("suite")), str(row.get("case_id")))
            )
            if sample is None:
                continue
            if (
                row.get("negative_score_status") == "completed"
                and (
                    row.get("positive_state_count")
                    != sample["positive_state_count"]
                    or row.get("negative_trace_count")
                    != sample["negative_trace_count"]
                )
            ):
                continue
            if row.get("sample_content_sha256") == sample["sample_content_sha256"]:
                continue
            updated = dict(row)
            updated.update({
                "sample_artifact": sample["sample_artifact"],
                "sample_content_sha256": sample["sample_content_sha256"],
                "sample_file_sha256": sample["sample_file_sha256"],
                "sample_binding": (
                    "same hidden-source hash, n_runs=12, seed=0, and matching counts"
                ),
            })
            append_jsonl(destination, updated)
            changed += 1
    return changed


def import_clause2inv(root: Path, source_root: Path) -> int:
    source = REPO_ROOT / "results" / "clause2inv" / "results.jsonl"
    if not source.exists():
        return 0
    tasks = {(task.suite, task.case_id): task for task in discover_tasks(source_root)}
    destination = event_path(root, "clause2inv")
    existing = latest_rows([destination])
    imported = 0
    for data in read_jsonl(source):
        suite, case_id = str(data["task_id"]).split("/", 1)
        normalized_suite = "NLA_lipus" if suite == "nonlinear" else suite
        task = tasks[(normalized_suite, case_id)]
        if task.key("clause2inv") in existing:
            continue
        invariant = data.get("invariant")
        log_name = f"{suite}_{case_id}.log"
        log_path = source.parent / log_name
        native_program = (
            Path("/home/yangfp/Clause2Inv/combinator/Benchmarks/Linear/c")
            / f"{case_id}.c"
            if suite == "linear"
            else Path("/home/yangfp/Clause2Inv/combinator/Benchmarks/NL/c")
            / f"NL{case_id}.c"
        )
        row = base_row("clause2inv", task)
        row.update({
            "generation_status": (
                "timeout" if data.get("timeout")
                else "completed" if data.get("returncode") == 0
                else "failed"
            ),
            "generation_error": None if data.get("returncode") == 0 else (
                f"returncode_{data.get('returncode')}"
            ),
            "invariants": [invariant] if invariant else [],
            "native_candidate_found": bool(data.get("candidate_found")),
            "native_verified": data.get("target_verified"),
            "generation_seconds": data.get("elapsed_seconds"),
            "reuse_source": str(source),
            "reuse_log": str(log_path) if log_path.exists() else None,
            "reuse_evidence": "native target_hidden=true result row",
            "token_estimate_kind": "accepted-response lower bound",
            **_clause2inv_token_estimate(log_path, native_program),
        })
        append_jsonl(destination, row)
        imported += 1
    return imported


def import_autospec_audit(root: Path, source_root: Path) -> int:
    source = REPO_ROOT / "results" / "autospec_loopgym366_strict"
    if not source.exists():
        return 0
    tasks = {(task.suite, task.case_id): task for task in discover_tasks(source_root)}
    destination = event_path(root, "autospec")
    existing = latest_rows([destination])
    imported = 0
    for summary_path in source.glob("*/*/summary.json"):
        data = json.loads(summary_path.read_text())
        suite, case_id = str(data["suite"]), str(data["case_id"])
        task = tasks[(suite, case_id)]
        if task.key("autospec") in existing:
            continue
        merged = next(iter(summary_path.parent.glob("*_merged.c")), None)
        row = base_row("autospec", task)
        row.update({
            "generation_status": "ineligible_target_leak",
            "generation_eligible": False,
            "generation_error": (
                "legacy AutoSpec command log shows the assertion in MSLines "
                "and in the model-facing infill source"
            ),
            "invariants": extract_invariants(
                merged.read_text(errors="ignore") if merged else ""
            ),
            "native_verified": data.get("valid_pass"),
            "generation_seconds": data.get("total_seconds"),
            "artifact": str(merged) if merged else None,
            "reuse_source": str(summary_path),
            "reuse_evidence": str(summary_path.parent / "command.log"),
            "token_estimate_kind": "AutoSpec native tiktoken estimate",
            **token_fields(
                total=data.get("total_tokens"),
                accounting=(
                    "estimated"
                    if data.get("total_tokens") is not None else "unavailable"
                ),
            ),
        })
        append_jsonl(destination, row)
        existing[task.key("autospec")] = row
        imported += 1
    return imported


def _sespec_latest_summaries(roots: list[Path]) -> list[Path]:
    latest = {}
    for matrix_root in roots:
        for path in matrix_root.rglob("summary.json"):
            data = json.loads(path.read_text())
            key = (str(data.get("bench") or data.get("root_dir")), str(data["case_id"]))
            if key not in latest or path.stat().st_mtime > latest[key].stat().st_mtime:
                latest[key] = path
    return list(latest.values())


def import_sespec(root: Path, source_root: Path, roots: list[Path]) -> int:
    tasks = {(task.suite, task.case_id): task for task in discover_tasks(source_root)}
    destination = event_path(root, "sespec")
    existing = latest_rows([destination])
    imported = 0
    for summary_path in _sespec_latest_summaries(roots):
        data = json.loads(summary_path.read_text())
        suite = str(data.get("bench") or data.get("root_dir"))
        case_id = str(data["case_id"])
        task = tasks[(suite, case_id)]
        if task.key("sespec") in existing:
            continue
        artifact = None
        for field in ("output_path", "loop_acsl_path", "loop_qcp_path"):
            candidate = data.get(field)
            if candidate and Path(candidate).exists():
                artifact = Path(candidate)
                break
        invariants = extract_invariants(
            artifact.read_text(errors="ignore") if artifact else ""
        )
        token_exact = data.get("total_tokens") is not None
        row = base_row("sespec", task)
        row.update({
            "generation_status": "ineligible_target_leak",
            "generation_eligible": False,
            "generation_error": (
                "legacy matrix runner hid SESPEC_INPUT_ROOT, but SESpec main.py "
                "read SESpec/src/input and could see the assertion"
            ),
            "invariants": invariants,
            "native_verified": data.get("valid_pass"),
            "generation_seconds": data.get("total_seconds"),
            "artifact": str(artifact) if artifact else None,
            "reuse_source": str(summary_path),
            "reuse_evidence": (
                "legacy wrapper edited SESPEC_INPUT_ROOT, but main.py read "
                "SESpec/src/input instead"
            ),
            **token_fields(
                prompt=data.get("prompt_tokens"),
                completion=data.get("completion_tokens"),
                total=data.get("total_tokens"),
                calls=data.get("call_count"),
                accounting="exact" if token_exact else "unavailable",
            ),
        })
        append_jsonl(destination, row)
        imported += 1
    return imported


def import_existing(root: Path, source_root: Path) -> None:
    naive, loopgym = import_legacy_inference(root, source_root)
    clause = import_clause2inv(root, source_root)
    autospec = import_autospec_audit(root, source_root)
    sespec_roots = [
        Path("/home/yangfp/SESpec/results/matrix_runs/20260724_sespec_loopgym366_strict2_linear"),
        Path("/home/yangfp/SESpec/results/matrix_runs/20260724_sespec_loopgym366_strict2_nla"),
    ]
    sespec = import_sespec(root, source_root, [path for path in sespec_roots if path.exists()])
    invalidated = invalidate_legacy_sespec(root)
    rejudged = import_rejudged(root)
    inference_judges = reuse_legacy_inference_judges(root)
    audit_attached = attach_legacy_batch_audit(root)
    normalized_times = normalize_shared_score_time(root)
    print(
        f"imported naive={naive}, LoopGym={loopgym}, Clause2Inv={clause}, "
        f"AutoSpec audit={autospec}, SESpec audit={sespec}, "
        f"SESpec invalidated={invalidated}, rejudged={rejudged}, "
        f"inference judges reused={inference_judges}, "
        f"legacy audits attached={audit_attached}, "
        f"shared score times normalized={normalized_times}"
    )


def invalidate_legacy_sespec(root: Path) -> int:
    destination = event_path(root, "sespec")
    current = latest_rows([destination])
    count = 0
    for row in current.values():
        if row.get("generation_eligible") is False:
            continue
        if "20260724_sespec_loopgym366_strict2_" not in str(row.get("reuse_source", "")):
            continue
        updated = dict(row)
        updated.update({
            "generation_status": "ineligible_target_leak",
            "generation_eligible": False,
            "generation_error": (
                "legacy matrix runner hid SESPEC_INPUT_ROOT, but SESpec main.py "
                "read SESpec/src/input and could see the assertion"
            ),
            "verified": None,
            "judge_error": "ineligible generation; old rejudge excluded",
        })
        append_jsonl(destination, updated)
        count += 1
    return count


def import_rejudged(root: Path) -> int:
    """Reuse completed common-judge booleans from the interrupted audit run."""
    sources = {
        "clause2inv": REPO_ROOT / "results" / "clause2inv" / "certified_target_hidden.jsonl",
        "sespec": REPO_ROOT / "results" / "sespec_loopgym832_strict" / "certified_target_hidden.jsonl",
    }
    imported = 0
    for method, source in sources.items():
        if not source.exists():
            continue
        destination = event_path(root, method)
        current = latest_rows([destination])
        for old in read_jsonl(source):
            suite = str(old.get("suite"))
            if suite == "nonlinear":
                suite = "NLA_lipus"
            case_id = str(old.get("case_id"))
            if (suite, case_id) in RESTORED_TARGET_IDS:
                continue
            matches = [
                row for key, row in current.items()
                if key[0] == method and key[1] == suite and key[2] == case_id
            ]
            if not matches:
                continue
            row = dict(matches[-1])
            if row.get("generation_eligible") is False:
                continue
            if sorted(row.get("invariants") or []) != sorted(old.get("invariants") or []):
                continue
            if "verified" in row:
                continue
            row.update({
                "verified": old.get("verified"),
                "judge_error": old.get("verification_error"),
                "judge_seconds": None,
                "judge_time_accounting": "unavailable_from_reused_audit",
                "judge_reuse_source": str(source),
            })
            append_jsonl(destination, row)
            current[row_key(row)] = row
            imported += 1
    return imported


def _score_fixed_task(
    task: Task,
    sample: dict,
    entries: list[tuple[Path, dict]],
) -> list[tuple[Path, dict]]:
    """Process-pool worker for one task shared by every available method."""
    examples = (
        load_sample(task, sample)
        if sample.get("sample_status") == "completed"
        else None
    )
    updated_entries = [(path, dict(row)) for path, row in entries]
    negative_indices = []
    for index, (_path, updated) in enumerate(updated_entries):
        if updated.get("generation_status") == "unsupported":
            updated.update({
                "verified": False,
                "judge_error": updated.get("generation_error"),
                "judge_seconds": 0.0,
                "negative_score_status": "not_applicable",
                "reward_mode": None,
                "positive_state_count": None,
                "negative_trace_count": None,
                "rejected_negative_count": None,
                "negative_rejection_score": None,
                "binary_frama_c_validation": None,
                "score_surviving_invariants": [],
                "negative_score_error": "unsupported native input",
                "negative_score_seconds": 0.0,
                "sample_artifact": sample["sample_artifact"],
                "sample_content_sha256": sample["sample_content_sha256"],
                "sample_file_sha256": sample["sample_file_sha256"],
            })
            continue
        if "verified" not in updated:
            updated.update(judge_invariants(task, updated.get("invariants") or []))
        if updated.get("negative_score_status") != "completed":
            negative_indices.append(index)

    if negative_indices:
        if sample.get("sample_status") == "failed":
            scores = [
                {
                    "negative_score_status": "failed",
                    "reward_mode": None,
                    "positive_state_count": None,
                    "negative_trace_count": None,
                    "rejected_negative_count": None,
                    "negative_rejection_score": None,
                    "binary_frama_c_validation": None,
                    "score_surviving_invariants": [],
                    "negative_score_error": (
                        f"fixed sampler failure: {sample.get('sample_error')}"
                    ),
                    "negative_score_seconds": 0.0,
                    "negative_score_time_accounting": "fixed_failure",
                    "negative_score_shared_batch_size": len(negative_indices),
                }
                for _ in negative_indices
            ]
        else:
            scores = score_negative_rejection_many(
                task,
                [
                    updated_entries[index][1].get("invariants") or []
                    for index in negative_indices
                ],
                examples=examples,
            )
        for index, score in zip(negative_indices, scores):
            updated_entries[index][1].update(score)
            updated_entries[index][1].update({
                "sample_artifact": sample["sample_artifact"],
                "sample_content_sha256": sample["sample_content_sha256"],
                "sample_file_sha256": sample["sample_file_sha256"],
            })

    for _path, updated in updated_entries:
        updated["reproduction_total_seconds"] = sum(
            float(updated.get(field) or 0.0)
            for field in (
                "generation_seconds", "judge_seconds", "negative_score_seconds",
            )
        )
    return updated_entries


def score_all(root: Path, workers: int) -> None:
    frama_c = ensure_frama_c_available()
    print(f"common judge Frama-C: {frama_c}")
    sample_index = load_sample_manifest(root)
    rebound = attach_fixed_sample_metadata(root, sample_index)
    print(f"fixed samples bound to existing scores={rebound}")
    tasks = {
        (task.suite, task.case_id): task for task in discover_tasks()
    }
    work: dict[tuple[str, str], list[tuple[Path, dict]]] = {}
    for method in METHODS:
        path = event_path(root, method)
        latest = latest_rows([path])
        for row in latest.values():
            task = tasks.get((row.get("suite"), row.get("case_id")))
            if task is None or row_key(row) != task.key(method):
                continue
            if row.get("generation_status") not in {"completed", "failed", "timeout", "unsupported"}:
                continue
            sample = sample_index[(task.suite, task.case_id)]
            score_is_current = (
                row.get("negative_score_status") == "completed"
                and row.get("sample_content_sha256")
                == sample["sample_content_sha256"]
                and row.get("positive_state_count")
                == sample["positive_state_count"]
                and row.get("negative_trace_count")
                == sample["negative_trace_count"]
            )
            need_judge = "verified" not in row
            need_negative = row.get("negative_score_status") not in {
                "completed", "not_applicable"
            } or (
                row.get("negative_score_status") == "completed"
                and not score_is_current
            )
            if need_judge or need_negative:
                if need_negative and row.get("negative_score_status") == "completed":
                    row = dict(row)
                    row["negative_score_status"] = "stale_fixed_sample"
                work.setdefault((task.suite, task.case_id), []).append((path, row))

    pending_rows = sum(len(entries) for entries in work.values())
    print(f"scoring pending tasks={len(work)} rows={pending_rows}")
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _score_fixed_task, tasks[key], sample_index[key], entries
            ): key
            for key, entries in work.items()
        }
        completed_rows = 0
        for future in as_completed(futures):
            for path, row in future.result():
                append_jsonl(path, row)
                completed_rows += 1
                print(
                    f"[{completed_rows}/{pending_rows}] {row['method']} "
                    f"{row['suite']}/{row['case_id']} "
                    f"verified={row.get('verified')}",
                    flush=True,
                )


def summarize(root: Path) -> None:
    tasks = discover_tasks()
    latest = latest_rows([event_path(root, method) for method in METHODS])
    latest_path = root / "latest.jsonl"
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    with latest_path.open("w") as handle:
        for method in METHODS:
            for task in tasks:
                row = latest.get(task.key(method))
                if row:
                    handle.write(json.dumps(row, sort_keys=True) + "\n")

    fields = [
        "method", "suite", "expected", "generated", "completed", "failed",
        "timeout", "unsupported", "scored", "verified",
        "verified_rate", "generation_seconds", "judge_seconds",
        "negative_score_seconds", "reproduction_total_seconds",
        "generation_time_accounting", "generation_task_seconds",
        "generation_task_rows", "generation_task_mean_seconds",
        "generation_time_rows_unavailable", "legacy_batch_seconds",
        "total_tokens_known", "token_rows_exact",
        "token_rows_estimated", "token_rows_unavailable",
        "token_rows_not_called",
        "negative_rows", "negative_micro_rejection", "negative_macro_rejection",
        "no_negative_rows", "binary_validation_pass",
    ]
    summary_rows = []
    for method in METHODS:
        for suite in ("linear", "NLA_lipus", "Loopy", "all"):
            selected_tasks = [task for task in tasks if suite == "all" or task.suite == suite]
            rows = [
                latest.get(task.key(method)) for task in selected_tasks
                if latest.get(task.key(method))
                and latest.get(task.key(method)).get("generation_eligible", True)
            ]
            scored = [row for row in rows if "verified" in row]
            negative = [
                row for row in rows
                if row.get("negative_trace_count") not in (None, 0)
                and row.get("negative_rejection_score") is not None
            ]
            no_negative = [
                row for row in rows if row.get("negative_trace_count") == 0
            ]
            rejected = sum(int(row.get("rejected_negative_count") or 0) for row in negative)
            total_negative = sum(int(row.get("negative_trace_count") or 0) for row in negative)
            macro = (
                sum(float(row["negative_rejection_score"]) for row in negative) / len(negative)
                if negative else None
            )
            accounting = [row.get("token_accounting", "unavailable") for row in rows]
            legacy_batches = {
                row["generation_batch_id"]: float(row["generation_batch_seconds"])
                for row in rows
                if row.get("generation_batch_id")
                and row.get("generation_batch_seconds") is not None
            }
            known_generation_seconds = sum(
                float(row.get("generation_seconds") or 0)
                for row in rows if not row.get("generation_batch_id")
            )
            generation_task_rows = sum(
                row.get("generation_seconds") is not None
                and not row.get("generation_batch_id")
                for row in rows
            )
            legacy_batch_seconds = (
                sum(legacy_batches.values()) if suite == "all" else None
            )
            if legacy_batches and suite == "all":
                generation_time_accounting = (
                    "task_wall_sum_plus_legacy_batch_wall"
                )
            elif legacy_batches:
                generation_time_accounting = (
                    "partial_task_wall_sum_legacy_batch_unallocated"
                )
            else:
                generation_time_accounting = "task_wall_sum"
            judge_seconds = sum(
                float(row.get("judge_seconds") or 0) for row in rows
            )
            negative_seconds = sum(
                float(row.get("negative_score_seconds") or 0) for row in rows
            )
            total_tokens = sum(
                int(row["total_tokens"]) for row in rows
                if row.get("total_tokens") is not None
            )
            summary_rows.append({
                "method": method,
                "suite": suite,
                "expected": len(selected_tasks),
                "generated": len(rows),
                "completed": sum(
                    row.get("generation_status") == "completed" for row in rows
                ),
                "failed": sum(
                    row.get("generation_status") == "failed" for row in rows
                ),
                "timeout": sum(
                    row.get("generation_status") == "timeout" for row in rows
                ),
                "unsupported": sum(
                    row.get("generation_status") == "unsupported" for row in rows
                ),
                "scored": len(scored),
                "verified": sum(row.get("verified") is True for row in scored),
                "verified_rate": (
                    sum(row.get("verified") is True for row in scored) / len(selected_tasks)
                ),
                "generation_seconds": (
                    known_generation_seconds
                    + (legacy_batch_seconds or 0.0)
                ),
                "judge_seconds": judge_seconds,
                "negative_score_seconds": negative_seconds,
                "reproduction_total_seconds": (
                    known_generation_seconds
                    + (legacy_batch_seconds or 0.0)
                    + judge_seconds
                    + negative_seconds
                ),
                "generation_time_accounting": generation_time_accounting,
                "generation_task_seconds": known_generation_seconds,
                "generation_task_rows": generation_task_rows,
                "generation_task_mean_seconds": (
                    known_generation_seconds / generation_task_rows
                    if generation_task_rows else None
                ),
                "generation_time_rows_unavailable": sum(
                    row.get("generation_seconds") is None for row in rows
                ),
                "legacy_batch_seconds": legacy_batch_seconds,
                "total_tokens_known": total_tokens,
                "token_rows_exact": accounting.count("exact"),
                "token_rows_estimated": accounting.count("estimated"),
                "token_rows_unavailable": accounting.count("unavailable"),
                "token_rows_not_called": accounting.count("not_called"),
                "negative_rows": len(negative),
                "negative_micro_rejection": rejected / total_negative if total_negative else None,
                "negative_macro_rejection": macro,
                "no_negative_rows": len(no_negative),
                "binary_validation_pass": sum(
                    row.get("binary_frama_c_validation") == 1.0 for row in no_negative
                ),
            })
    summary_path = root / "summary.csv"
    with summary_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary_rows)
    print(summary_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("manifest")
    sub.add_parser("import-existing")
    samples = sub.add_parser("samples")
    samples.add_argument("--workers", type=int, default=8)
    generate = sub.add_parser("generate")
    generate.add_argument("--method", choices=METHODS, required=True)
    generate.add_argument("--workers", type=int, default=8)
    generate.add_argument("--retry-failed", action="store_true")
    generate.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="per-task timeout (default: AutoSpec 600s, other native tools 7200s)",
    )
    generate.add_argument(
        "--autospec-root", type=Path,
        default=Path("/home/yangfp/TRASH/SESpecTrash/represent/external/autospec"),
    )
    generate.add_argument(
        "--sespec-root", type=Path, default=Path("/home/yangfp/SESpec"),
    )
    score = sub.add_parser("score")
    score.add_argument("--workers", type=int, default=4)
    run_all = sub.add_parser("all")
    run_all.add_argument("--workers", type=int, default=8)
    run_all.add_argument("--score-workers", type=int, default=4)
    run_all.add_argument("--retry-failed", action="store_true")
    run_all.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="override every native-tool timeout",
    )
    run_all.add_argument(
        "--autospec-root", type=Path,
        default=Path("/home/yangfp/TRASH/SESpecTrash/represent/external/autospec"),
    )
    run_all.add_argument(
        "--sespec-root", type=Path, default=Path("/home/yangfp/SESpec"),
    )
    sub.add_parser("summarize")
    args = parser.parse_args()

    if args.command == "manifest":
        print(write_manifest(args.results_root))
    elif args.command == "import-existing":
        write_manifest(args.results_root)
        import_existing(args.results_root, REPO_ROOT / "src" / "input")
    elif args.command == "samples":
        if args.workers < 1:
            parser.error("--workers must be at least 1")
        write_manifest(args.results_root)
        materialize_samples(args.results_root, workers=args.workers)
    elif args.command == "generate":
        if args.workers < 1:
            parser.error("--workers must be at least 1")
        if args.method in {"naive", "loopgym"}:
            generate_neural(args.method, args.results_root, args.workers, args.retry_failed)
        else:
            timeout = (
                args.timeout
                if args.timeout is not None
                else 600 if args.method == "autospec" else 7200
            )
            generate_native(
                args.method,
                args.results_root,
                args.workers,
                args.retry_failed,
                autospec_root=args.autospec_root,
                sespec_root=args.sespec_root,
                timeout=timeout,
            )
    elif args.command == "score":
        score_all(args.results_root, args.workers)
    elif args.command == "all":
        if args.workers < 1 or args.score_workers < 1:
            parser.error("worker counts must be at least 1")
        write_manifest(args.results_root)
        import_existing(args.results_root, REPO_ROOT / "src" / "input")
        generate_native(
            "clause2inv", args.results_root, args.workers, args.retry_failed,
            autospec_root=args.autospec_root,
            sespec_root=args.sespec_root,
            timeout=args.timeout if args.timeout is not None else 7200,
        )
        for method in ("naive", "loopgym"):
            generate_neural(
                method, args.results_root, args.workers, args.retry_failed
            )
        for method in ("autospec", "sespec"):
            timeout = (
                args.timeout
                if args.timeout is not None
                else 600 if method == "autospec" else 7200
            )
            generate_native(
                method, args.results_root, args.workers, args.retry_failed,
                autospec_root=args.autospec_root,
                sespec_root=args.sespec_root,
                timeout=timeout,
            )
        materialize_samples(
            args.results_root, workers=min(args.score_workers, 8)
        )
        score_all(args.results_root, args.score_workers)
        summarize(args.results_root)
    else:
        summarize(args.results_root)
    return 0


REPO_ROOT = Path(__file__).resolve().parents[2]


if __name__ == "__main__":
    raise SystemExit(main())
