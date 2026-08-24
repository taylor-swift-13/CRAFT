#!/usr/bin/env python3
"""Stage RL/SFT datasets containing only loops with reliable negatives."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rl_pipeline.sampler.example_sampler import NEGATIVE_SCHEMA_VERSION  # noqa: E402

PROGRAM_MARKER = "Program:\n"


def _display_path(path: Path) -> str:
    """Record repository-relative paths when an artifact lives in this checkout."""
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def _latest_by_digest(path: Path) -> dict[str, dict]:
    latest = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                item = json.loads(line)
                latest[item["source_sha256"]] = item
    return latest


def _source_from_rl(record: dict) -> str:
    user = next(turn["content"] for turn in record["prompt"] if turn["role"] == "user")
    return user.split(PROGRAM_MARKER, 1)[1]


def _source_from_sft(record: dict) -> str:
    human = next(
        turn["value"]
        for turn in record["conversations"]
        if turn["from"] == "human"
    )
    return human.split(PROGRAM_MARKER, 1)[1]


def _atomic_json(value, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _partition(records, source_of, latest: dict, label: str):
    """Split records into (kept, dropped row indices); fail closed otherwise."""
    keep, dropped, missing, stale, unscorable = [], [], [], [], []
    for row, record in enumerate(records):
        digest = hashlib.sha256(source_of(record).encode("utf-8")).hexdigest()
        result = latest.get(digest)
        if result is None:
            missing.append(row)
        elif result.get("coverage_schema_version") != NEGATIVE_SCHEMA_VERSION:
            stale.append(row)
        elif not result.get("scorable"):
            unscorable.append(row)
        elif int(result["n_negative_traces"]) > 0:
            keep.append(record)
        else:
            dropped.append(row)
    if missing:
        raise ValueError(
            f"{label} coverage ledger is incomplete: {len(missing)} rows missing"
        )
    if stale:
        raise ValueError(
            f"{label} coverage ledger uses a stale sampler schema for "
            f"{len(stale)} rows; rerun the coverage audit"
        )
    if unscorable:
        raise ValueError(
            f"{label} coverage ledger contains "
            f"{len(unscorable)} unscorable rows; retry the audit before filtering"
        )
    return keep, dropped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="dataset", required=True)
    sft = subparsers.add_parser("sft")
    sft.add_argument("--input", type=Path, default=ROOT / "traindata/craft_sft_clean.json")
    sft.add_argument(
        "--ledger",
        type=Path,
        default=ROOT / "paper/artifacts/rl_negative_coverage.jsonl",
    )
    sft.add_argument("--output", type=Path, default=ROOT / "traindata/craft_sft_negative_complete.json")
    sft.add_argument("--report", type=Path, default=ROOT / "paper/artifacts/sft_negative_filter.json")
    rl = subparsers.add_parser("rl")
    rl.add_argument("--input", type=Path, default=ROOT / "traindata/craft_rl_clean.parquet")
    rl.add_argument("--ledger", type=Path, default=ROOT / "paper/artifacts/rl_negative_coverage.jsonl")
    rl.add_argument("--output", type=Path, default=ROOT / "traindata/craft_rl_negative_complete.parquet")
    rl.add_argument("--report", type=Path, default=ROOT / "paper/artifacts/rl_negative_filter.json")
    args = parser.parse_args()

    latest = _latest_by_digest(args.ledger)
    if args.dataset == "sft":
        records = json.loads(args.input.read_text(encoding="utf-8"))
        keep, dropped = _partition(records, _source_from_sft, latest, "SFT")
        _atomic_json(keep, args.output)
    else:
        table = pq.read_table(args.input)
        records = table.to_pylist()
        keep, dropped = _partition(records, _source_from_rl, latest, "RL")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{args.output.name}.", dir=args.output.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            pq.write_table(pa.Table.from_pylist(keep, schema=table.schema), temporary)
            os.replace(temporary, args.output)
        finally:
            temporary.unlink(missing_ok=True)

    report = {
        "schema_version": 1,
        "negative_schema_version": NEGATIVE_SCHEMA_VERSION,
        "dataset": args.dataset,
        "input": _display_path(args.input),
        "output": _display_path(args.output),
        "input_rows": len(records),
        "output_rows": len(records) - len(dropped),
        "dropped_rows": dropped,
        "coverage_ledger": _display_path(args.ledger),
        "policy": (
            "retain iff sampler succeeded and n_negative_traces > 0; "
            "all generated traces are scored"
        ),
    }
    _atomic_json(report, args.report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
