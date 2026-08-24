#!/usr/bin/env python3
"""Rewrite the ``while (1) { if (COND) break; ... }`` idiom in a training pool.

The evaluation corpus never uses this idiom; ~40% of the RL pool does.  The
rewrite (``program_fingerprint.canonicalize_break_idiom``) is
semantics-preserving and restores a real loop guard, which the negative
sampler needs for guard-preserving relation witnesses.  Both the visible
prompt program and ``reward_model.ground_truth.raw_code`` are rewritten; a
record is left untouched (and counted) when the two would disagree after
rewriting, when the rewritten program no longer parses, or when the visible
program changes its single-loop structure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from paper.scripts.filter_training_by_negative_coverage import (  # noqa: E402
    PROGRAM_MARKER,
    _atomic_json,
    _display_path,
)
from paper.scripts.program_fingerprint import canonicalize_break_idiom  # noqa: E402
from rl_pipeline.common.program import parse_program, strip_postcondition  # noqa: E402


def _replace_program(message: str, source: str) -> str:
    prefix, _ = message.split(PROGRAM_MARKER, 1)
    return prefix + PROGRAM_MARKER + source


def _rewrite_visible(source: str) -> tuple[str, str]:
    """Return (rewritten source, status)."""
    rewritten, changed = canonicalize_break_idiom(source)
    if not changed:
        return source, "unchanged"
    try:
        program = parse_program(rewritten)
    except Exception:
        return source, "rewrite_unparsable"
    if len(program.loops) != 1:
        return source, "rewrite_loop_count"
    return rewritten, "rewritten"


def canonicalize_rl(records: list[dict]) -> Counter:
    status = Counter()
    for record in records:
        user = next(turn for turn in record["prompt"] if turn["role"] == "user")
        visible = user["content"].split(PROGRAM_MARKER, 1)[1]
        new_visible, verdict = _rewrite_visible(visible)
        if verdict != "rewritten":
            status[verdict] += 1
            continue
        raw = record["reward_model"]["ground_truth"]["raw_code"]
        new_raw, raw_changed = canonicalize_break_idiom(raw)
        if not raw_changed or strip_postcondition(new_raw).strip() != strip_postcondition(new_visible).strip():
            # raw_code carries comments/targets; if the idiom rewrite does not
            # line up exactly, keep the record as-is rather than desync them.
            if strip_postcondition(raw).strip() == visible.strip():
                new_raw = new_visible
            else:
                status["raw_code_mismatch"] += 1
                continue
        user["content"] = _replace_program(user["content"], new_visible)
        record["reward_model"]["ground_truth"]["raw_code"] = new_raw
        status["rewritten"] += 1
    return status


def canonicalize_sft(records: list[dict]) -> Counter:
    status = Counter()
    for record in records:
        human = next(turn for turn in record["conversations"] if turn["from"] == "human")
        visible = human["value"].split(PROGRAM_MARKER, 1)[1]
        new_visible, verdict = _rewrite_visible(visible)
        status[verdict] += 1
        if verdict == "rewritten":
            human["value"] = _replace_program(human["value"], new_visible)
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", choices=("rl", "sft"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() == args.input.resolve():
        raise SystemExit("refusing to overwrite the input in place")

    if args.dataset == "rl":
        table = pq.read_table(args.input)
        records = table.to_pylist()
        status = canonicalize_rl(records)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{args.output.name}.", dir=args.output.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            pq.write_table(pa.Table.from_pylist(records, schema=table.schema), temporary)
            os.replace(temporary, args.output)
        finally:
            temporary.unlink(missing_ok=True)
    else:
        records = json.loads(args.input.read_text(encoding="utf-8"))
        status = canonicalize_sft(records)
        _atomic_json(records, args.output)

    report = {
        "schema_version": 1,
        "dataset": args.dataset,
        "input": _display_path(args.input),
        "output": _display_path(args.output),
        "rows": len(records),
        "status": dict(status),
        "policy": (
            "rewrite `while (1) { if (COND) break; REST }` to "
            "`while (!COND) { REST }` when REST has no other break and no "
            "else is attached to the exit test; keep the record unchanged "
            "when the rewrite does not parse or raw_code would desync"
        ),
    }
    _atomic_json(report, args.report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
