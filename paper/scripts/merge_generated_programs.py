#!/usr/bin/env python3
"""Append generated programs (``generate_cell_programs.py``) to the RL pool.

Rows follow the released RL parquet schema exactly (same system prompt, the
canonical user prompt, ``reward_model.ground_truth.raw_code`` = the source,
``extra_info.file_id`` = ``gen_<sha12>``).  Sources already present in the
pool are skipped.  The same programs are also written as SFT-format rows with
an empty answer so ``synthesize_sft_from_rollouts.py`` can produce targets.
"""

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

from paper.scripts.filter_training_by_negative_coverage import (  # noqa: E402
    PROGRAM_MARKER,
    _atomic_json,
    _display_path,
)
from paper.scripts.sanitize_training_prompts import _canonical_source, _canonical_user  # noqa: E402
from rl_pipeline.common import prompts  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", type=Path, required=True)
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sft-output", type=Path, help="SFT-format rows with empty answers")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() == args.pool.resolve():
        raise SystemExit("refusing to overwrite the pool in place")

    table = pq.read_table(args.pool)
    records = table.to_pylist()
    existing = {
        hashlib.sha256(next(t["content"] for t in r["prompt"] if t["role"] == "user")
                       .split(PROGRAM_MARKER, 1)[1].encode("utf-8")).hexdigest()
        for r in records
    }
    system = prompts.system_prompt()
    added = 0
    skipped = 0
    sft_rows = []
    for item in json.loads(args.generated.read_text(encoding="utf-8")):
        source = _canonical_source(item["source"])
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        if digest in existing:
            skipped += 1
            continue
        existing.add(digest)
        records.append({
            "data_source": "loopgym",
            "prompt": [{"content": system, "role": "system"},
                       {"content": _canonical_user(source), "role": "user"}],
            "ability": "loop_invariant",
            "reward_model": {"ground_truth": {"raw_code": source}, "style": "frama-c"},
            "extra_info": {"file_id": f"gen_{digest[:12]}"},
        })
        sft_rows.append({
            "conversations": [
                {"from": "system", "value": system},
                {"from": "human", "value": _canonical_user(source)},
                {"from": "gpt", "value": ""},
            ],
            "selection": {"source": "generated", "cell": item.get("cell")},
        })
        added += 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{args.output.name}.", dir=args.output.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        pq.write_table(pa.Table.from_pylist(records, schema=table.schema), temporary)
        os.replace(temporary, args.output)
    finally:
        temporary.unlink(missing_ok=True)
    if args.sft_output:
        _atomic_json(sft_rows, args.sft_output)
    report = {
        "schema_version": 1,
        "generated": _display_path(args.generated),
        "pool": _display_path(args.pool),
        "output": _display_path(args.output),
        "pool_rows": table.num_rows,
        "added": added,
        "skipped_existing": skipped,
        "output_rows": len(records),
    }
    _atomic_json(report, args.report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
