from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
import gzip
import json
import os
from pathlib import Path
import time

from rl_pipeline.common.program import parse_program
from rl_pipeline.common.state import State
from rl_pipeline.sampler import (
    ExampleSampler,
    ExampleSet,
    NEGATIVE_SCHEMA_VERSION,
)

from .common import (
    Task,
    canonical_json,
    discover_tasks,
    protocol_sha256,
    sha256_bytes,
    sha256_text,
)


SAMPLE_SCHEMA_VERSION = NEGATIVE_SCHEMA_VERSION
LOADABLE_SAMPLE_SCHEMA_VERSIONS = set(range(1, SAMPLE_SCHEMA_VERSION + 1))
SAMPLER_CONFIG = {"n_runs": 12, "seed": 0}
SAMPLER_RUNTIME_POLICY = "skip_and_record_abnormal_concrete_runs_v1"


def sample_path(results_root: Path, task: Task) -> Path:
    return results_root / "samples" / task.suite / f"{task.case_id}.json.gz"


def _state_to_dict(state: State) -> dict:
    return {
        "vars": state.vars,
        "pre": state.pre,
        "loop_entry": state.loop_entry,
        "run": state.run,
        "it": state.it,
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


def _payload(task: Task, examples: ExampleSet) -> dict:
    positives = examples.pos(0)
    negatives = examples.neg(0)
    groups = examples.groups(0)
    return {
        "schema_version": SAMPLE_SCHEMA_VERSION,
        "sample_status": "completed",
        "sample_error": None,
        "suite": task.suite,
        "case_id": task.case_id,
        "source_sha256": task.source_sha256,
        "hidden_source_sha256": task.hidden_source_sha256,
        "sampler": SAMPLER_CONFIG,
        "sampler_runtime_policy": SAMPLER_RUNTIME_POLICY,
        "positives": [_state_to_dict(state) for state in positives],
        "negatives": [_state_to_dict(state) for state in negatives],
        "negative_trace_groups": groups,
        "negative_trace_families": examples.group_families(0),
        "stats": examples.stats.get(0, {}),
    }


def _failure_payload(task: Task, exc: Exception) -> dict:
    return {
        "schema_version": SAMPLE_SCHEMA_VERSION,
        "sample_status": "failed",
        "sample_error": f"{type(exc).__name__}: {exc}",
        "suite": task.suite,
        "case_id": task.case_id,
        "source_sha256": task.source_sha256,
        "hidden_source_sha256": task.hidden_source_sha256,
        "sampler": SAMPLER_CONFIG,
        "sampler_runtime_policy": SAMPLER_RUNTIME_POLICY,
        "positives": [],
        "negatives": [],
        "negative_trace_groups": [],
        "stats": {},
    }


def _encode_payload(payload: dict) -> tuple[bytes, str]:
    raw = (canonical_json(payload) + "\n").encode("utf-8")
    return gzip.compress(raw, compresslevel=9, mtime=0), sha256_bytes(raw)


def _decode_payload(path: Path) -> tuple[dict, str]:
    raw = gzip.decompress(path.read_bytes())
    return json.loads(raw), sha256_bytes(raw)


def _valid_existing(path: Path, task: Task) -> tuple[dict, str] | None:
    if not path.exists():
        return None
    try:
        payload, content_hash = _decode_payload(path)
    except Exception:
        return None
    sample_status = payload.get("sample_status", "completed")
    sample_error = payload.get("sample_error")
    if (
        payload.get("schema_version") != SAMPLE_SCHEMA_VERSION
        or payload.get("suite") != task.suite
        or str(payload.get("case_id")) != task.case_id
        or payload.get("source_sha256") != task.source_sha256
        or payload.get("hidden_source_sha256") != task.hidden_source_sha256
        or payload.get("sampler") != SAMPLER_CONFIG
        or sample_status not in {"completed", "failed"}
    ):
        return None
    if (
        sample_status == "failed"
        and "instrumented program exited abnormally" in str(sample_error)
    ):
        return None
    payload.setdefault("sample_status", sample_status)
    payload.setdefault("sample_error", None)
    payload.setdefault("sampler_runtime_policy", SAMPLER_RUNTIME_POLICY)
    return payload, content_hash


def _manifest_row(
    task: Task,
    path: Path,
    payload: dict,
    content_hash: str,
) -> dict:
    families = [str(value) for value in payload.get(
        "negative_trace_families", []
    )]
    stats = payload.get("stats", {})
    return {
        "schema_version": SAMPLE_SCHEMA_VERSION,
        "protocol_sha256": protocol_sha256(),
        "suite": task.suite,
        "case_id": task.case_id,
        "source_sha256": task.source_sha256,
        "hidden_source_sha256": task.hidden_source_sha256,
        "sampler": SAMPLER_CONFIG,
        "sampler_runtime_policy": SAMPLER_RUNTIME_POLICY,
        "sample_status": payload["sample_status"],
        "sample_error": payload.get("sample_error"),
        "sample_artifact": str(path.resolve()),
        "sample_content_sha256": content_hash,
        "sample_file_sha256": sha256_bytes(path.read_bytes()),
        "positive_state_count": len(payload["positives"]),
        "negative_state_count": len(payload["negatives"]),
        "negative_trace_count": len(payload["negative_trace_groups"]),
        "negative_family_counts": dict(sorted(Counter(families).items())),
        "zero_blockers": list(stats.get("zero_blockers", [])),
        "nondet_guard": bool(stats.get("nondet_guard", False)),
        "nondet_body": bool(stats.get("nondet_body", False)),
        "tainted_relation_axis_count": len(
            stats.get("tainted_relation_axes", [])
        ),
        "sampling_seconds": payload.get("sampling_seconds"),
    }


def materialize_samples(results_root: Path, workers: int = 4) -> dict[tuple[str, str], dict]:
    """Create or validate the immutable 832-task evaluation sample set."""
    tasks = discover_tasks()

    def one(task: Task) -> dict:
        path = sample_path(results_root, task)
        existing = _valid_existing(path, task)
        if existing is not None:
            payload, content_hash = existing
            return _manifest_row(task, path, payload, content_hash)

        started = time.perf_counter()
        try:
            examples = ExampleSampler(task.hidden_source, **SAMPLER_CONFIG).sample()
            payload = _payload(task, examples)
        except Exception as exc:
            payload = _failure_payload(task, exc)
        payload["sampling_seconds"] = time.perf_counter() - started
        compressed, content_hash = _encode_payload(payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(compressed)
        os.replace(temporary, path)
        return _manifest_row(task, path, payload, content_hash)

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, task): task for task in tasks}
        for index, future in enumerate(as_completed(futures), 1):
            row = future.result()
            rows.append(row)
            if index % 25 == 0 or index == len(tasks):
                print(f"samples [{index}/{len(tasks)}]", flush=True)

    rows.sort(key=lambda row: (
        ("linear", "NLA_lipus", "Loopy").index(row["suite"]),
        int(row["case_id"]) if row["case_id"].isdigit() else row["case_id"],
    ))
    manifest = results_root / "samples_manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    manifest_hash = sha256_text(manifest.read_text())
    metadata = {
        "schema_version": SAMPLE_SCHEMA_VERSION,
        "protocol_sha256": protocol_sha256(),
        "sampler": SAMPLER_CONFIG,
        "sampler_runtime_policy": SAMPLER_RUNTIME_POLICY,
        "task_count": len(rows),
        "manifest": str(manifest.resolve()),
        "manifest_sha256": manifest_hash,
    }
    (results_root / "samples_metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    )
    return {
        (str(row["suite"]), str(row["case_id"])): row for row in rows
    }


def load_sample_manifest(results_root: Path) -> dict[tuple[str, str], dict]:
    """Load the frozen sample index without creating or changing any samples."""
    manifest = results_root / "samples_manifest.jsonl"
    metadata_path = results_root / "samples_metadata.json"
    if not manifest.exists() or not metadata_path.exists():
        raise RuntimeError(
            "fixed evaluation samples are missing; run "
            "`python -m experiments.gpt5nano_full832.run samples --workers 8` first"
        )

    manifest_text = manifest.read_text()
    metadata = json.loads(metadata_path.read_text())
    if metadata.get("manifest_sha256") != sha256_text(manifest_text):
        raise RuntimeError("fixed sample manifest hash mismatch")
    archived_schema_version = metadata.get("schema_version")
    if (
        archived_schema_version not in LOADABLE_SAMPLE_SCHEMA_VERSIONS
        or metadata.get("protocol_sha256") != protocol_sha256()
        or metadata.get("sampler") != SAMPLER_CONFIG
        or metadata.get("sampler_runtime_policy") != SAMPLER_RUNTIME_POLICY
    ):
        raise RuntimeError("fixed sample metadata does not match the current protocol")

    rows = [
        json.loads(line)
        for line in manifest_text.splitlines()
        if line.strip()
    ]
    tasks = {(task.suite, task.case_id): task for task in discover_tasks()}
    index = {
        (str(row["suite"]), str(row["case_id"])): row
        for row in rows
    }
    if len(rows) != len(index) or set(index) != set(tasks):
        raise RuntimeError(
            f"fixed sample manifest is incomplete: expected {len(tasks)} unique "
            f"tasks, found {len(index)}"
        )

    for key, task in tasks.items():
        row = index[key]
        path = Path(row["sample_artifact"])
        if (
            row.get("schema_version") != archived_schema_version
            or row.get("protocol_sha256") != protocol_sha256()
            or row.get("source_sha256") != task.source_sha256
            or row.get("hidden_source_sha256") != task.hidden_source_sha256
            or row.get("sampler") != SAMPLER_CONFIG
            or row.get("sampler_runtime_policy") != SAMPLER_RUNTIME_POLICY
            or not path.is_file()
        ):
            raise RuntimeError(
                f"fixed sample manifest entry does not match {task.suite}/{task.case_id}"
            )
        if sha256_bytes(path.read_bytes()) != row.get("sample_file_sha256"):
            raise RuntimeError(
                f"fixed sample artifact hash mismatch: {task.suite}/{task.case_id}"
            )
        payload, content_hash = _decode_payload(path)
        if (
            payload.get("schema_version") != archived_schema_version
            or content_hash != row.get("sample_content_sha256")
        ):
            raise RuntimeError(
                f"fixed sample payload mismatch: {task.suite}/{task.case_id}"
            )
    return index


def load_sample(task: Task, manifest_row: dict) -> ExampleSet:
    path = Path(manifest_row["sample_artifact"])
    payload, content_hash = _decode_payload(path)
    if content_hash != manifest_row["sample_content_sha256"]:
        raise RuntimeError(f"sample content hash mismatch: {path}")
    archived_schema_version = manifest_row.get(
        "schema_version", payload.get("schema_version")
    )
    if (
        payload.get("schema_version") != archived_schema_version
        or payload.get("suite") != task.suite
        or str(payload.get("case_id")) != task.case_id
        or payload.get("source_sha256") != task.source_sha256
        or payload.get("hidden_source_sha256") != task.hidden_source_sha256
        or payload.get("sampler") != SAMPLER_CONFIG
    ):
        raise RuntimeError(f"sample metadata does not match task: {path}")
    if payload.get("sample_status", "completed") != "completed":
        raise RuntimeError(
            f"fixed sampler failure for {task.suite}/{task.case_id}: "
            f"{payload.get('sample_error')}"
        )
    # Schema < 3 payloads carry no per-trace family labels.
    families = payload.get("negative_trace_families")
    return ExampleSet(
        program=parse_program(task.hidden_source),
        positives={0: [_state_from_dict(item) for item in payload["positives"]]},
        negatives={0: [_state_from_dict(item) for item in payload["negatives"]]},
        neg_groups={
            0: [
                [int(index) for index in group]
                for group in payload["negative_trace_groups"]
            ]
        },
        stats={0: payload.get("stats", {})},
        neg_group_families=(
            {} if families is None else {0: [str(family) for family in families]}
        ),
    )
