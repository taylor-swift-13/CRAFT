#!/usr/bin/env python3
"""Audit negative-example coverage for every unique RL or SFT loop.

The ledger is append-only and resumable.  Dataset records are mapped to a
canonical source hash so duplicate RL prompts are executed once while their
row membership remains recoverable from the manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from paper.scripts._curation_common import limit_memory  # noqa: E402
from paper.scripts.sanitize_training_prompts import PROGRAM_MARKER  # noqa: E402
from rl_pipeline.common.program import parse_program  # noqa: E402
from rl_pipeline.sampler.example_sampler import (  # noqa: E402
    NEGATIVE_SCHEMA_VERSION,
    ExampleSampler,
)


DEFAULT_RL = ROOT / "traindata" / "craft_rl_clean.parquet"
DEFAULT_SFT = ROOT / "traindata" / "craft_sft_clean.json"
DEFAULT_ARTIFACT_DIR = ROOT / "paper" / "artifacts"


def _display_path(path: Path) -> str:
    """Record repository-relative paths when an artifact lives in this checkout."""
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def _source_from_user(message: str) -> str:
    if PROGRAM_MARKER not in message:
        raise ValueError("prompt has no Program marker")
    return message.split(PROGRAM_MARKER, 1)[1]


def _load_sources(dataset: str, path: Path) -> tuple[dict[str, str], dict[str, list[int]]]:
    sources: dict[str, str] = {}
    rows: dict[str, list[int]] = {}
    if dataset == "sft":
        records = json.loads(path.read_text(encoding="utf-8"))
        iterator = []
        for row, record in enumerate(records):
            human = next(
                turn["value"]
                for turn in record["conversations"]
                if turn["from"] == "human"
            )
            iterator.append((row, _source_from_user(human)))
    else:
        records = pq.read_table(path, columns=["prompt"]).to_pylist()
        iterator = []
        for row, record in enumerate(records):
            user = next(
                turn["content"]
                for turn in record["prompt"]
                if turn["role"] == "user"
            )
            iterator.append((row, _source_from_user(user)))
    for row, source in iterator:
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        sources.setdefault(digest, source)
        rows.setdefault(digest, []).append(row)
    return sources, rows


def _latest(path: Path) -> dict[str, dict]:
    latest = {}
    if path.is_file():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    item = json.loads(line)
                    latest[item["source_sha256"]] = item
    return latest


def _error_result(digest: str, runs: int, seed: int, error: BaseException) -> dict:
    return {
        "source_sha256": digest,
        "scorable": False,
        "seed": seed,
        "runs_requested": runs,
        "error": f"{type(error).__name__}: {error}",
    }


def _score(job: tuple[str, str, int, int]) -> dict:
    digest, source, runs, seed = job
    try:
        program = parse_program(source)
        examples = ExampleSampler(source, n_runs=runs, seed=seed).sample()
        return {
            "source_sha256": digest,
            "scorable": True,
            "function": program.func_name,
            "loop_count": len(program.loops),
            "n_positive_states": len(examples.pos(0)),
            "n_negative_traces": len(examples.groups(0)),
            "n_negative_states": len(examples.neg(0)),
            "seed": seed,
            "runs_requested": runs,
            "sampler_stats": examples.stats[0],
        }
    except Exception as error:
        return _error_result(digest, runs, seed, error)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", choices=("rl", "sft"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--runs", type=int, default=12)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--stop", type=int)
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--memory-cap", type=int, default=2_500_000_000,
                        help="per-worker address-space cap; pathological programs fail "
                             "with MemoryError instead of taking the pool down (0 = off)")
    parser.add_argument("--retry-zero-negatives", action="store_true")
    args = parser.parse_args()

    default_input = DEFAULT_RL if args.dataset == "rl" else DEFAULT_SFT
    input_path = args.input or default_input
    ledger = args.ledger or (
        DEFAULT_ARTIFACT_DIR / f"{args.dataset}_negative_coverage.jsonl"
    )
    manifest = args.manifest or (
        DEFAULT_ARTIFACT_DIR / f"{args.dataset}_negative_coverage_manifest.json"
    )
    sources, rows = _load_sources(args.dataset, input_path)
    ordered = sorted(sources)
    stop = min(args.stop if args.stop is not None else len(ordered), len(ordered))
    latest = _latest(ledger)

    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "dataset": args.dataset,
                "input": _display_path(input_path),
                "record_count": sum(len(value) for value in rows.values()),
                "unique_loop_count": len(sources),
                "rows_by_source_sha256": rows,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    jobs = []
    for digest in ordered[args.start:stop]:
        previous = latest.get(digest)
        if previous is not None:
            retry = (
                previous.get("coverage_schema_version")
                != NEGATIVE_SCHEMA_VERSION
            ) or (
                args.retry_errors and not previous.get("scorable", False)
            ) or (
                args.retry_zero_negatives
                and previous.get("scorable", False)
                and int(previous.get("n_negative_traces", 0)) == 0
            )
            if not retry:
                continue
        jobs.append((digest, sources[digest], args.runs, args.seed))

    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        try:
            # spawn: workers must not inherit the parent's multi-GB parquet heap
            with ProcessPoolExecutor(
                max_workers=args.jobs,
                mp_context=multiprocessing.get_context("spawn"),
                initializer=limit_memory,
                initargs=(args.memory_cap,),
            ) as executor:
                futures = {executor.submit(_score, job): job[0] for job in jobs}
                for count, future in enumerate(as_completed(futures), 1):
                    digest = futures[future]
                    try:
                        result = future.result()
                    except Exception as error:
                        result = _error_result(digest, args.runs, args.seed, error)
                    # the ledger field name is historical; it carries the sampler version
                    result["coverage_schema_version"] = NEGATIVE_SCHEMA_VERSION
                    handle.write(json.dumps(result, sort_keys=True) + "\n")
                    handle.flush()
                    if count % 25 == 0 or count == len(futures):
                        print(f"scored {count}/{len(futures)} pending loops", flush=True)
        except BrokenProcessPool:
            print("worker pool failed; completed ledger entries were preserved", file=sys.stderr)
            raise


if __name__ == "__main__":
    main()
