"""Restore latest rows after an interrupted raw-negative append-only trial."""
from __future__ import annotations

import argparse
from pathlib import Path

from .common import DEFAULT_RESULTS_ROOT, METHODS, append_jsonl, read_jsonl, row_key
from .daikon_adapter import METHOD as DAIKON_METHOD
from .recompute_raw_negative import RAW_SCORE_PROTOCOL
from .run import event_path


def restore(root: Path) -> None:
    for method in (*METHODS, DAIKON_METHOD):
        path = event_path(root, method)
        previous = {}
        latest = {}
        for row in read_jsonl(path):
            key = row_key(row)
            latest[key] = row
            if row.get("raw_negative_score_protocol") != RAW_SCORE_PROTOCOL:
                previous[key] = row
        restored = 0
        for key, row in latest.items():
            if row.get("raw_negative_score_protocol") != RAW_SCORE_PROTOCOL:
                continue
            original = previous.get(key)
            if original is None:
                raise RuntimeError(f"no pre-raw row for {method}:{key}")
            append_jsonl(path, original)
            restored += 1
        print(f"{method}: restored={restored}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    args = parser.parse_args()
    restore(args.results_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
