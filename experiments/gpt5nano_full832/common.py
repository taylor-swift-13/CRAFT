from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Iterable, Optional

from rl_pipeline.common.program import parse_program, strip_postcondition
from rl_pipeline.common.state import dedup_normalized
from rl_pipeline.inference import InferenceFramework, MockRolloutProvider
from rl_pipeline.reward import annotate
from rl_pipeline.reward.reward_calculator import RewardCalculator
from rl_pipeline.sampler import ExampleSampler


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
PROTOCOL_PATH = HERE / "protocol.json"
DEFAULT_RESULTS_ROOT = REPO_ROOT / "results" / "gpt5nano_full832"
METHODS = (
    "autospec",
    "clause2inv",
    "sespec",
    "naive",
    "loopgym_r1_no_houdini",
    "loopgym_r1_houdini",
    "loopgym_r4_houdini",
)


def ensure_frama_c_available() -> Path:
    """Resolve the pinned local Frama-C toolchain and fail closed if absent."""
    configured = os.environ.get("LOOPGYM_FRAMA_C_BIN")
    candidates = [
        Path(configured) if configured else None,
        Path("/home/yangfp/.opam/frama-c.27.1/bin"),
    ]
    for candidate in candidates:
        if candidate and (candidate / "frama-c").is_file():
            current = os.environ.get("PATH", "")
            parts = current.split(os.pathsep) if current else []
            if str(candidate) not in parts:
                os.environ["PATH"] = str(candidate) + os.pathsep + current
            return candidate / "frama-c"
    resolved = shutil.which("frama-c")
    if resolved:
        return Path(resolved)
    raise RuntimeError(
        "Frama-C is required for the common judge and negative scorer; "
        "set LOOPGYM_FRAMA_C_BIN to its bin directory"
    )


def canonical_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def load_protocol() -> dict:
    return json.loads(PROTOCOL_PATH.read_text())


def protocol_sha256() -> str:
    return sha256_text(canonical_json(load_protocol()))


@dataclass(frozen=True)
class Task:
    suite: str
    case_id: str
    source_path: Path
    source_sha256: str
    hidden_source: str
    hidden_source_sha256: str

    def key(self, method: str) -> tuple[str, str, str, str, str]:
        return (
            method,
            self.suite,
            self.case_id,
            os.environ.get("LOOPGYM_MODEL") or load_protocol()["model"],
            protocol_sha256(),
        )

    def to_dict(self) -> dict:
        return {
            "suite": self.suite,
            "case_id": self.case_id,
            "source": str(self.source_path),
            "source_sha256": self.source_sha256,
            "hidden_source_sha256": self.hidden_source_sha256,
        }


def _numeric_key(value: str):
    return (0, int(value)) if value.isdigit() else (1, value)


def assert_target_hidden(original: str, hidden: str) -> None:
    """Fail closed if a benchmark target remains in the model-facing source."""
    if re.search(r"\b(?:__VERIFIER_assert|assert)\s*\(", hidden):
        raise ValueError("executable assertion remains in hidden source")
    if re.search(r"\b(?:assert|ensures)\b", hidden):
        raise ValueError("ACSL assertion/ensures remains in hidden source")
    if re.search(r"\bERROR\s*:", hidden):
        raise ValueError("ERROR target label remains in hidden source")
    if hidden == original:
        raise ValueError("target hiding did not change the source")


def discover_tasks(source_root: Optional[Path] = None) -> list[Task]:
    protocol = load_protocol()
    root = source_root or REPO_ROOT / protocol["dataset"]["root"]
    tasks: list[Task] = []
    for suite, expected in protocol["dataset"]["suites"].items():
        paths = sorted((root / suite).glob("*.c"), key=lambda p: _numeric_key(p.stem))
        if len(paths) != expected:
            raise RuntimeError(f"{suite}: expected {expected} C files, found {len(paths)}")
        for path in paths:
            original = path.read_text(errors="ignore")
            hidden = strip_postcondition(original)
            assert_target_hidden(original, hidden)
            tasks.append(Task(
                suite=suite,
                case_id=path.stem,
                source_path=path.resolve(),
                source_sha256=sha256_text(original),
                hidden_source=hidden,
                hidden_source_sha256=sha256_text(hidden),
            ))
    if len(tasks) != protocol["dataset"]["total"]:
        raise RuntimeError(f"expected 832 tasks, found {len(tasks)}")
    return tasks


def base_row(method: str, task: Task) -> dict:
    protocol = load_protocol()
    configured_reasoning_effort = (
        os.environ.get("LOOPGYM_REASONING_EFFORT")
        or protocol["generation"].get("reasoning_effort")
    )
    return {
        "schema_version": 1,
        "protocol": protocol["name"],
        "protocol_sha256": protocol_sha256(),
        "method": method,
        "model": os.environ.get("LOOPGYM_MODEL") or protocol["model"],
        "reasoning_effort": (
            None
            if str(configured_reasoning_effort).lower() in {"default", "omit"}
            else configured_reasoning_effort
        ),
        **task.to_dict(),
        "target_hidden": True,
        "event_utc": datetime.now(timezone.utc).isoformat(),
    }


def row_key(row: dict) -> tuple[str, str, str, str, str]:
    return (
        str(row["method"]),
        str(row["suite"]),
        str(row["case_id"]),
        str(row["model"]),
        str(row["protocol_sha256"]),
    )


_append_locks: dict[str, threading.Lock] = {}
_append_locks_guard = threading.Lock()


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve())
    with _append_locks_guard:
        lock = _append_locks.setdefault(key, threading.Lock())
    with lock, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(errors="ignore").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def latest_rows(paths: Iterable[Path]) -> dict[tuple[str, str, str, str, str], dict]:
    latest = {}
    for path in paths:
        for row in read_jsonl(path):
            try:
                latest[row_key(row)] = row
            except KeyError:
                continue
    return latest


def generation_complete(row: Optional[dict]) -> bool:
    if not row:
        return False
    return row.get("generation_status") in {
        "completed", "failed", "timeout", "unsupported"
    }


def token_fields(
    *,
    prompt: Optional[int] = None,
    completion: Optional[int] = None,
    total: Optional[int] = None,
    calls: Optional[int] = None,
    accounting: str,
) -> dict:
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "api_call_count": calls,
        "token_accounting": accounting,
    }


def judge_invariants(task: Task, invariants: list[str]) -> dict:
    started = time.perf_counter()
    normalized = dedup_normalized(invariants)
    try:
        original = task.source_path.read_text(errors="ignore")
        program = parse_program(original)
        annotated = annotate.build_annotated(program, normalized, 0)
        framework = InferenceFramework(
            original,
            rollout_provider=MockRolloutProvider([[]]),
            n_rollouts=1,
        )
        verified = framework._verify(annotated)
        error = None if verified is not None else "frama_c_unavailable_or_failed"
    except Exception as exc:
        verified = None
        error = f"{type(exc).__name__}: {exc}"
    return {
        "invariants": normalized,
        "invariant_count": len(normalized),
        "verified": verified,
        "judge_error": error,
        "judge_seconds": time.perf_counter() - started,
    }


def score_negative_rejection_many(
    task: Task,
    invariant_batches: list[list[str]],
    *,
    examples=None,
) -> list[dict]:
    """Score several methods on one task while sampling traces only once."""
    sampling_started = time.perf_counter()
    try:
        if examples is None:
            examples = ExampleSampler(
                task.hidden_source, n_runs=12, seed=0
            ).sample()
        sampling_seconds = time.perf_counter() - sampling_started
        calculator = RewardCalculator(n_jobs=1)
    except Exception as exc:
        elapsed = time.perf_counter() - sampling_started
        return [
            {
                "negative_score_status": "failed",
                "reward_mode": None,
                "positive_state_count": None,
                "negative_trace_count": None,
                "rejected_negative_count": None,
                "negative_rejection_score": None,
                "binary_frama_c_validation": None,
                "score_surviving_invariants": [],
                "negative_score_error": f"{type(exc).__name__}: {exc}",
                "negative_score_seconds": elapsed / len(invariant_batches),
                "negative_score_time_accounting": "shared_equal_allocation",
                "negative_score_shared_batch_size": len(invariant_batches),
            }
            for _ in invariant_batches
        ]

    shared_sampling = sampling_seconds / len(invariant_batches)
    results = []
    for invariants in invariant_batches:
        scoring_started = time.perf_counter()
        try:
            reward = calculator.compute(
                task.hidden_source, [invariants], examples=examples
            )
            rollout = reward.rollouts[0]
            has_negatives = reward.n_negatives > 0
            results.append({
                "negative_score_status": "completed",
                "reward_mode": (
                    "negative_coverage" if has_negatives
                    else "binary_frama_c_validation"
                ),
                "positive_state_count": reward.n_positives,
                "negative_trace_count": reward.n_negatives,
                "rejected_negative_count": (
                    rollout.rejected if has_negatives else None
                ),
                "negative_rejection_score": (
                    rollout.base if has_negatives else None
                ),
                "binary_frama_c_validation": (
                    rollout.reward if not has_negatives else None
                ),
                "score_surviving_invariants": rollout.survivors,
                "negative_score_error": None,
                "negative_score_seconds": (
                    time.perf_counter() - scoring_started + shared_sampling
                ),
                "negative_score_time_accounting": (
                    "individual_filter_plus_equal_sampling_allocation"
                ),
                "negative_score_shared_batch_size": len(invariant_batches),
            })
        except Exception as exc:
            results.append({
                "negative_score_status": "failed",
                "reward_mode": None,
                "positive_state_count": None,
                "negative_trace_count": None,
                "rejected_negative_count": None,
                "negative_rejection_score": None,
                "binary_frama_c_validation": None,
                "score_surviving_invariants": [],
                "negative_score_error": f"{type(exc).__name__}: {exc}",
                "negative_score_seconds": (
                    time.perf_counter() - scoring_started + shared_sampling
                ),
                "negative_score_time_accounting": (
                    "individual_filter_plus_equal_sampling_allocation"
                ),
                "negative_score_shared_batch_size": len(invariant_batches),
            })
    return results


def score_negative_rejection(task: Task, invariants: list[str]) -> dict:
    return score_negative_rejection_many(task, [invariants])[0]


def write_manifest(results_root: Path = DEFAULT_RESULTS_ROOT) -> Path:
    tasks = discover_tasks()
    path = results_root / "task_manifest.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for task in tasks:
            handle.write(json.dumps({
                **task.to_dict(),
                "protocol_sha256": protocol_sha256(),
            }, sort_keys=True) + "\n")
    (results_root / "protocol.json").write_text(
        json.dumps(load_protocol(), indent=2, sort_keys=True) + "\n"
    )
    return path
