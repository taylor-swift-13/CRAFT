"""Daikon baseline over the frozen Full-832 loop-head traces.

Daikon is given reachable loop-head states only.  It never receives the hidden
assertion, the postcondition, or the synthetic negative traces.  Its scalar
invariants are translated to ACSL and passed directly to the common judge and
negative-rejection scorer; this native Daikon baseline does not use Houdini.

The frozen sample payloads can be very large after decompression.  This module
therefore reads the ``positives`` JSON array incrementally and selects a fixed,
stratified subset without materialising the rest of the payload.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Iterable

from rl_pipeline.common.program import parse_program
from rl_pipeline.common.state import (
    State,
    dedup_normalized,
    eval_predicate,
    normalize_invariant,
)
from rl_pipeline.reward.filters import out_of_scope_ids

from .common import (
    DEFAULT_RESULTS_ROOT,
    Task,
    append_jsonl,
    base_row,
    discover_tasks,
    ensure_frama_c_available,
    judge_invariants,
    latest_rows,
    protocol_sha256,
    sha256_text,
    token_fields,
)
from .samples import load_sample


METHOD = "daikon"
DEFAULT_DAIKON_JAR = (
    Path(__file__).resolve().parents[2]
    / ".tools"
    / "daikon-5.8.24"
    / "daikon.jar"
)
DAIKON_VERSION = "5.8.24"
MAX_TRACE_STATES = 2048
_POSITIVE_MARKER = '"positives":['


@dataclass(frozen=True)
class DaikonRun:
    stdout: str
    stderr: str
    seconds: float


def _safe_ppt_name(suite: str, case_id: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_]", "_", f"loopgym_{suite}_{case_id}")
    return f"{stem}:::POINT"


def _target_indices(count: int, limit: int) -> set[int]:
    if count <= 0 or limit <= 0:
        return set()
    if count <= limit:
        return set(range(count))
    return {
        (index * (count - 1)) // (limit - 1)
        for index in range(limit)
    }


def _state_from_dict(data: dict) -> State:
    return State(
        vars={str(key): int(value) for key, value in data["vars"].items()},
        pre={str(key): int(value) for key, value in data.get("pre", {}).items()},
        loop_entry={
            str(key): int(value)
            for key, value in data.get("loop_entry", {}).items()
        },
        run=int(data.get("run", -1)),
        it=int(data.get("it", -1)),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def stream_selected_positives(
    path: Path,
    *,
    positive_count: int,
    limit: int = MAX_TRACE_STATES,
) -> list[State]:
    """Read deterministic indices from the canonical ``positives`` JSON array."""
    wanted = _target_indices(positive_count, limit)
    if not wanted:
        return []
    decoder = json.JSONDecoder()
    selected: list[State] = []
    buffer = ""
    found = False
    state_index = 0
    finished = False

    with gzip.open(path, "rt", encoding="utf-8") as handle:
        while not finished:
            chunk = handle.read(1024 * 1024)
            if not chunk and not buffer:
                break
            buffer += chunk
            if not found:
                marker_index = buffer.find(_POSITIVE_MARKER)
                if marker_index < 0:
                    if not chunk:
                        break
                    buffer = buffer[-len(_POSITIVE_MARKER):]
                    continue
                buffer = buffer[marker_index + len(_POSITIVE_MARKER):]
                found = True

            while found:
                buffer = buffer.lstrip()
                if buffer.startswith(","):
                    buffer = buffer[1:].lstrip()
                if buffer.startswith("]"):
                    finished = True
                    break
                if not buffer:
                    break
                try:
                    item, end = decoder.raw_decode(buffer)
                except json.JSONDecodeError:
                    if not chunk:
                        raise RuntimeError(f"truncated positive-state array: {path}")
                    break
                if state_index in wanted:
                    selected.append(_state_from_dict(item))
                state_index += 1
                buffer = buffer[end:]
            if not chunk and not finished:
                break

    if not found or not finished:
        raise RuntimeError(f"could not stream positive-state array: {path}")
    if state_index != positive_count:
        raise RuntimeError(
            f"positive-state count mismatch for {path}: "
            f"manifest={positive_count}, payload={state_index}"
        )
    if len(selected) != len(wanted):
        raise RuntimeError(f"failed to select every requested state from {path}")
    return selected


def load_manifest_lightweight(results_root: Path) -> dict[tuple[str, str], dict]:
    """Validate the immutable index without decompressing all 832 payloads."""
    manifest_path = results_root / "samples_manifest.jsonl"
    metadata_path = results_root / "samples_metadata.json"
    if not manifest_path.is_file() or not metadata_path.is_file():
        raise RuntimeError("frozen Full-832 sample manifest is missing")
    manifest_text = manifest_path.read_text()
    metadata = json.loads(metadata_path.read_text())
    if metadata.get("manifest_sha256") != sha256_text(manifest_text):
        raise RuntimeError("fixed sample manifest hash mismatch")
    if metadata.get("protocol_sha256") != protocol_sha256():
        raise RuntimeError("fixed samples belong to a different protocol")
    rows = [json.loads(line) for line in manifest_text.splitlines() if line.strip()]
    index = {(str(row["suite"]), str(row["case_id"])): row for row in rows}
    tasks = {(task.suite, task.case_id): task for task in discover_tasks()}
    if len(rows) != 832 or set(index) != set(tasks):
        raise RuntimeError(f"expected 832 unique fixed samples, found {len(index)}")
    return index


def load_trace_states(task: Task, sample: dict, limit: int) -> list[State]:
    path = Path(sample["sample_artifact"])
    if _sha256_file(path) != sample.get("sample_file_sha256"):
        raise RuntimeError(f"fixed sample artifact hash mismatch: {path}")
    if sample.get("sample_status") != "completed":
        raise RuntimeError(
            f"fixed sampler failure for {task.suite}/{task.case_id}: "
            f"{sample.get('sample_error')}"
        )
    return stream_selected_positives(
        path,
        positive_count=int(sample["positive_state_count"]),
        limit=limit,
    )


def _trace_variables(source: str, states: list[State]) -> list[tuple[str, str]]:
    """Return (Daikon name, source expression) scalar variables."""
    program = parse_program(source)
    available = set.intersection(*(set(state.vars) for state in states))
    variables = [(name, name) for name in program.pre_vars if name in available]
    for name in program.params:
        if all(name in state.pre for state in states):
            variables.append((f"pre__{name}", name))
    return variables


def write_daikon_trace(
    directory: Path,
    *,
    suite: str,
    case_id: str,
    source: str,
    states: list[State],
) -> tuple[Path, Path, str, int]:
    if not states:
        raise ValueError("Daikon requires at least one reachable state")
    variables = _trace_variables(source, states)
    if not variables:
        raise ValueError("Daikon trace has no source-level scalar variables")
    ppt = _safe_ppt_name(suite, case_id)
    directory.mkdir(parents=True, exist_ok=True)
    decls = directory / f"{suite}_{case_id}.decls"
    dtrace = directory / f"{suite}_{case_id}.dtrace"

    decl_lines = [
        "decl-version 2.0",
        "var-comparability none",
        "",
        f"ppt {ppt}",
        "ppt-type point",
    ]
    for daikon_name, _source_name in variables:
        decl_lines.extend([
            f"variable {daikon_name}",
            "  var-kind variable",
            "  dec-type int",
            "  rep-type int",
            "  comparability 1",
        ])
    decls.write_text("\n".join(decl_lines) + "\n")

    with dtrace.open("w", encoding="utf-8") as handle:
        for state in states:
            handle.write(ppt + "\n")
            for daikon_name, source_name in variables:
                value = (
                    state.pre[source_name]
                    if daikon_name.startswith("pre__")
                    else state.vars[source_name]
                )
                handle.write(f"{daikon_name}\n{int(value)}\n1\n")
            handle.write("\n")
    return decls, dtrace, ppt, len(states)


def run_daikon(
    jar: Path,
    decls: Path,
    dtrace: Path,
    *,
    timeout: float = 120.0,
) -> DaikonRun:
    if not jar.is_file():
        raise FileNotFoundError(f"Daikon jar not found: {jar}")
    command = [
        "java", "-Xmx768m", "-cp", str(jar), "daikon.Daikon",
        "--nohierarchy", "-o", os.devnull, str(decls), str(dtrace),
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, check=False,
    )
    seconds = time.perf_counter() - started
    if completed.returncode != 0:
        raise RuntimeError(
            f"Daikon failed with return code {completed.returncode}: "
            f"{completed.stderr[-1000:]}"
        )
    return DaikonRun(completed.stdout, completed.stderr, seconds)


def _daikon_lines(stdout: str, ppt: str) -> list[str]:
    lines = stdout.splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == ppt)
    except StopIteration:
        return []
    result = []
    for line in lines[start + 1:]:
        text = line.strip()
        if not text:
            continue
        if text.startswith("Exiting Daikon") or set(text) == {"="}:
            break
        result.append(text)
    return result


def translate_daikon_invariant(line: str, source: str) -> str | None:
    """Translate Daikon's scalar integer syntax to the accepted ACSL subset."""
    program = parse_program(source)
    current = set(program.pre_vars)
    params = set(program.params)
    identifiers = re.findall(r"[A-Za-z_]\w*", line)
    for identifier in identifiers:
        if identifier.startswith("pre__"):
            if identifier[5:] not in params:
                return None
        elif identifier not in current:
            return None
    # This deliberately rejects Daikon prose/set/modulo forms, arrays, method
    # calls, floating literals, and derived-variable syntax.
    if not re.fullmatch(r"[A-Za-z0-9_+\-*/%<>=!() \t]+", line):
        return None
    if not re.search(r"(?:==|!=|<=|>=|<|>)", line):
        return None
    if line.count("(") != line.count(")"):
        return None
    translated = re.sub(
        r"\bpre__([A-Za-z_]\w*)\b", r"\\at(\1, Pre)", line
    )
    translated = normalize_invariant(translated)
    if out_of_scope_ids(translated, program.pre_vars):
        return None
    return translated


def parse_daikon_invariants(stdout: str, ppt: str, source: str) -> list[str]:
    return dedup_normalized(
        translated
        for line in _daikon_lines(stdout, ppt)
        if (translated := translate_daikon_invariant(line, source)) is not None
    )


def _artifact_dir(root: Path, task: Task) -> Path:
    return root / "artifacts" / METHOD / task.suite / task.case_id


def _event_path(root: Path) -> Path:
    return root / "events" / f"{METHOD}.jsonl"


def _run_one(
    task: Task,
    sample: dict,
    *,
    root: Path,
    jar: Path,
    max_states: int,
    timeout: float,
) -> dict:
    row = base_row(METHOD, task)
    directory = _artifact_dir(root, task)
    directory.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        trace_started = time.perf_counter()
        states = load_trace_states(task, sample, max_states)
        trace_load_seconds = time.perf_counter() - trace_started
        (directory / "input.hidden.c").write_text(task.hidden_source)
        decls, dtrace, ppt, state_count = write_daikon_trace(
            directory,
            suite=task.suite,
            case_id=task.case_id,
            source=task.hidden_source,
            states=states,
        )
        daikon = run_daikon(jar, decls, dtrace, timeout=timeout)
        (directory / "daikon.stdout.txt").write_text(daikon.stdout)
        (directory / "daikon.stderr.txt").write_text(daikon.stderr)
        candidates = parse_daikon_invariants(daikon.stdout, ppt, task.hidden_source)
        (directory / "candidates.json").write_text(
            json.dumps(candidates, indent=2, ensure_ascii=False) + "\n"
        )
        status, error = "completed", None
    except subprocess.TimeoutExpired as exc:
        states, candidates = [], []
        state_count = 0
        trace_load_seconds = locals().get("trace_load_seconds", 0.0)
        daikon = DaikonRun("", str(exc), timeout)
        status, error = "timeout", f"Daikon timeout after {timeout:g}s"
    except Exception as exc:
        states, candidates = [], []
        state_count = 0
        trace_load_seconds = locals().get("trace_load_seconds", 0.0)
        daikon = DaikonRun("", "", 0.0)
        status, error = "failed", f"{type(exc).__name__}: {exc}"
    row.update({
        "generation_status": status,
        "generation_error": error,
        "generator": f"Daikon {DAIKON_VERSION}",
        "model_called": False,
        "target_hidden": True,
        "artifact": str(directory.resolve()),
        "hidden_source": str((directory / "input.hidden.c").resolve()),
        "trace_frontend": "frozen_loop_head_dtrace",
        "trace_state_limit": max_states,
        "trace_state_count": state_count,
        "trace_load_seconds": trace_load_seconds,
        "daikon_seconds": daikon.seconds,
        "raw_daikon_invariants": candidates,
        "invariants": candidates,
        "generation_seconds": time.perf_counter() - started,
        "generation_time_accounting": "trace_load_export_plus_daikon_wall",
        "sample_artifact": sample["sample_artifact"],
        "sample_content_sha256": sample["sample_content_sha256"],
        "sample_file_sha256": sample["sample_file_sha256"],
        **token_fields(prompt=0, completion=0, total=0, calls=0, accounting="not_called"),
    })
    return row


def generate_all(
    root: Path,
    *,
    jar: Path,
    workers: int,
    max_states: int,
    timeout: float,
) -> None:
    ensure_frama_c_available()
    samples = load_manifest_lightweight(root)
    tasks = discover_tasks()
    event = _event_path(root)
    current = latest_rows([event])
    pending = [
        task for task in tasks
        if not (current.get(task.key(METHOD)) or {}).get("generation_status")
        in {"completed", "failed", "timeout"}
    ]
    print(f"Daikon generation pending={len(pending)} workers={workers}", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _run_one,
                task,
                samples[(task.suite, task.case_id)],
                root=root,
                jar=jar,
                max_states=max_states,
                timeout=timeout,
            ): task
            for task in pending
        }
        for index, future in enumerate(as_completed(futures), 1):
            task = futures[future]
            row = future.result()
            append_jsonl(event, row)
            print(
                f"[{index}/{len(pending)}] {task.suite}/{task.case_id} "
                f"{row['generation_status']} candidates="
                f"{len(row.get('raw_daikon_invariants') or [])} "
                f"seconds={row['generation_seconds']:.2f}",
                flush=True,
            )


def score_all(root: Path, *, workers: int) -> None:
    ensure_frama_c_available()
    samples = load_manifest_lightweight(root)
    tasks = {(task.suite, task.case_id): task for task in discover_tasks()}
    event = _event_path(root)
    latest = latest_rows([event])
    work = []
    for task in tasks.values():
        row = latest.get(task.key(METHOD))
        if not row or row.get("generation_status") not in {
            "completed", "failed", "timeout"
        }:
            continue
        sample = samples[(task.suite, task.case_id)]
        current_score = (
            "verified" in row
            and row.get("negative_score_status") == "completed"
            and row.get("sample_content_sha256") == sample["sample_content_sha256"]
            and row.get("daikon_score_protocol") == "raw_no_houdini_v1"
        )
        if not current_score:
            work.append((task, sample, row))
    print(f"Daikon scoring pending={len(work)} workers={workers}", flush=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_score_one_raw, task, sample, row): task
            for task, sample, row in work
        }
        for index, future in enumerate(as_completed(futures), 1):
            task = futures[future]
            row = future.result()
            append_jsonl(event, row)
            print(
                f"[{index}/{len(work)}] {task.suite}/{task.case_id} "
                f"verified={row.get('verified')} "
                f"negative={row.get('negative_rejection_score')}",
                flush=True,
            )


def _score_one_raw(task: Task, sample: dict, row: dict) -> dict:
    """Common judge plus raw Daikon negative coverage, with no Houdini pass."""
    updated = dict(row)
    updated.update(judge_invariants(task, updated.get("invariants") or []))
    score_started = time.perf_counter()
    try:
        examples = load_sample(task, sample)
        updated.update(raw_negative_fields(
            examples,
            updated.get("invariants") or [],
            verified=updated.get("verified") is True,
        ))
        updated.update({
            "negative_score_status": "completed",
            "positive_state_count": int(sample["positive_state_count"]),
            "negative_score_error": None,
            "negative_score_seconds": time.perf_counter() - score_started,
            "negative_score_time_accounting": "sample_load_plus_raw_state_evaluation",
            "negative_score_shared_batch_size": 1,
            "daikon_score_protocol": "raw_no_houdini_v1",
            "sample_artifact": sample["sample_artifact"],
            "sample_content_sha256": sample["sample_content_sha256"],
            "sample_file_sha256": sample["sample_file_sha256"],
        })
    except Exception as exc:
        updated.update({
            "negative_score_status": "failed",
            "reward_mode": None,
            "positive_state_count": None,
            "negative_trace_count": None,
            "rejected_negative_count": None,
            "negative_rejection_score": None,
            "binary_frama_c_validation": None,
            "score_surviving_invariants": [],
            "negative_score_error": f"{type(exc).__name__}: {exc}",
            "negative_score_seconds": time.perf_counter() - score_started,
            "negative_score_time_accounting": "failed_raw_no_houdini",
            "negative_score_shared_batch_size": 1,
            "daikon_score_protocol": "raw_no_houdini_v1",
        })
    updated["reproduction_total_seconds"] = sum(
        float(updated.get(field) or 0.0)
        for field in ("generation_seconds", "judge_seconds", "negative_score_seconds")
    )
    return updated


def raw_negative_fields(examples, invariants: list[str], *, verified: bool) -> dict:
    """Evaluate a tool's final clauses on negatives without filtering them."""
    negatives = examples.neg(0)
    groups = examples.groups(0)
    if groups:
        rejected = sum(
            any(
                eval_predicate(invariant, negatives[state_index]) is False
                for state_index in group
                for invariant in invariants
            )
            for group in groups
        )
        negative_score = rejected / len(groups)
        binary = None
        reward_mode = "negative_coverage_raw_no_houdini"
    else:
        rejected = None
        negative_score = None
        binary = 1.0 if verified else 0.0
        reward_mode = "binary_frama_c_validation"
    return {
        "reward_mode": reward_mode,
        "negative_trace_count": len(groups),
        "rejected_negative_count": rejected,
        "negative_rejection_score": negative_score,
        "binary_frama_c_validation": binary,
        # The name is retained for schema compatibility. No filtering occurs.
        "score_surviving_invariants": list(invariants),
    }


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


def report(root: Path) -> dict:
    tasks = discover_tasks()
    latest = latest_rows([_event_path(root)])
    rows = [latest[task.key(METHOD)] for task in tasks if task.key(METHOD) in latest]
    verified = sum(row.get("verified") is True for row in rows)
    negative = [
        row for row in rows
        if row.get("negative_trace_count") not in (None, 0)
        and row.get("negative_rejection_score") is not None
    ]
    total_negative = sum(int(row["negative_trace_count"]) for row in negative)
    total_rejected = sum(int(row["rejected_negative_count"]) for row in negative)
    no_negative = [row for row in rows if row.get("negative_trace_count") == 0]
    summary = {
        "method": METHOD,
        "generator": f"Daikon {DAIKON_VERSION}",
        "task_rows": len(rows),
        "completed": sum(row.get("generation_status") == "completed" for row in rows),
        "failed": sum(row.get("generation_status") == "failed" for row in rows),
        "timeout": sum(row.get("generation_status") == "timeout" for row in rows),
        "verified": verified,
        "accuracy": verified / 832,
        "mean_generation_seconds": _mean(
            float(row["generation_seconds"])
            for row in rows if row.get("generation_seconds") is not None
        ),
        "time_rows": sum(row.get("generation_seconds") is not None for row in rows),
        "mean_total_tokens": 0.0,
        "token_rows_exact": len(rows),
        "negative_rows": len(negative),
        "negative_trace_count": total_negative,
        "rejected_negative_count": total_rejected,
        "negative_micro_rejection": (
            total_rejected / total_negative if total_negative else None
        ),
        "negative_macro_rejection": _mean(
            float(row["negative_rejection_score"]) for row in negative
        ),
        "no_negative_rows": len(no_negative),
        "no_negative_binary_pass": sum(
            row.get("binary_frama_c_validation") == 1.0 for row in no_negative
        ),
        "trace_frontend": "frozen_loop_head_dtrace",
        "trace_state_limit": MAX_TRACE_STATES,
        "target_hidden": True,
        "token_accounting": "not_called",
        "event_utc": datetime.now(timezone.utc).isoformat(),
    }
    (root / "daikon_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    fields = sorted({key for row in rows for key in row})
    with (root / "daikon_results.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value, ensure_ascii=False)
                if isinstance(value, (list, dict)) else value
                for key, value in row.items()
            })
    percent = lambda value: "—" if value is None else f"{100 * value:.2f}%"
    seconds = summary["mean_generation_seconds"]
    markdown = "\n".join([
        "# Daikon Full-832 extension",
        "",
        "| Method | Correct / 832 | Mean time / task | Mean tokens | Negative micro | Negative macro |",
        "|---|---:|---:|---:|---:|---:|",
        f"| Daikon | {verified} ({percent(summary['accuracy'])}) | "
        f"{seconds:.2f} s ({summary['time_rows']} rows) | 0 (exact, {len(rows)} rows) | "
        f"{percent(summary['negative_micro_rejection'])} | "
        f"{percent(summary['negative_macro_rejection'])} |",
        "",
        "Daikon receives only fixed reachable loop-head states; it does not receive "
        "the assertion, postcondition, or negative traces. Candidate invariants are "
        "translated to scalar ACSL and sent directly to the common judge without "
        "Houdini. Time includes fixed-trace hash/read/export and Daikon, but excludes the one-time "
        "creation of the shared frozen evaluation traces.",
        "",
        f"Zero-negative tasks: {summary['no_negative_rows']}; binary Frama-C passes: "
        f"{summary['no_negative_binary_pass']}.",
        "",
    ])
    (root / "daikon_report.md").write_text(markdown)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def probe(suite: str, case_id: str, *, results_root: Path, jar: Path) -> int:
    tasks = {(task.suite, task.case_id): task for task in discover_tasks()}
    task = tasks[(suite, case_id)]
    sample = load_manifest_lightweight(results_root)[(suite, case_id)]
    with tempfile.TemporaryDirectory(prefix="loopgym_daikon_probe_") as raw:
        directory = Path(raw)
        states = load_trace_states(task, sample, MAX_TRACE_STATES)
        decls, dtrace, ppt, count = write_daikon_trace(
            directory, suite=suite, case_id=case_id,
            source=task.hidden_source, states=states,
        )
        result = run_daikon(jar, decls, dtrace)
        invariants = parse_daikon_invariants(result.stdout, ppt, task.hidden_source)
        print(f"ppt={ppt} states={count} seconds={result.seconds:.6f}")
        print(result.stdout)
        print("ACSL candidates:")
        print(json.dumps(invariants, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--jar", type=Path, default=DEFAULT_DAIKON_JAR)
    subparsers = parser.add_subparsers(dest="command", required=True)
    probe_parser = subparsers.add_parser("probe")
    probe_parser.add_argument("suite")
    probe_parser.add_argument("case_id")
    generate = subparsers.add_parser("generate")
    generate.add_argument("--workers", type=int, default=4)
    generate.add_argument("--max-states", type=int, default=MAX_TRACE_STATES)
    generate.add_argument("--timeout", type=float, default=120.0)
    score = subparsers.add_parser("score")
    score.add_argument("--workers", type=int, default=4)
    subparsers.add_parser("report")
    all_parser = subparsers.add_parser("all")
    all_parser.add_argument("--workers", type=int, default=4)
    all_parser.add_argument("--score-workers", type=int, default=4)
    all_parser.add_argument("--max-states", type=int, default=MAX_TRACE_STATES)
    all_parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    if args.command == "probe":
        return probe(args.suite, args.case_id, results_root=args.results_root, jar=args.jar)
    if args.command in {"generate", "all"}:
        generate_all(
            args.results_root, jar=args.jar, workers=args.workers,
            max_states=args.max_states, timeout=args.timeout,
        )
    if args.command in {"score", "all"}:
        score_all(args.results_root, workers=args.score_workers if args.command == "all" else args.workers)
    if args.command in {"report", "all"}:
        report(args.results_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
