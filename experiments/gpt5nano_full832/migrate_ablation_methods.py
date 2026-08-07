#!/usr/bin/env python3
"""Migrate the completed v1 neural runs into the v2 ablation method names."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from .common import (
    DEFAULT_RESULTS_ROOT,
    append_jsonl,
    load_protocol,
    protocol_sha256,
    read_jsonl,
)


MAPPINGS = {
    "autospec": "autospec",
    "clause2inv": "clause2inv",
    "sespec": "sespec",
    "naive": "loopgym_r1_no_houdini",
    "loopgym": "loopgym_r4_houdini",
}


def latest_by_task(path: Path, source_method: str) -> dict[tuple[str, str], dict]:
    latest: dict[tuple[str, str], dict] = {}
    for row in read_jsonl(path):
        if (
            row.get("method") != source_method
            or row.get("protocol") != "loopgym-gpt5nano-full832-v1"
        ):
            continue
        latest[(str(row["suite"]), str(row["case_id"]))] = row
    return latest


def migrate(root: Path) -> dict[str, int]:
    protocol = load_protocol()
    new_hash = protocol_sha256()
    counts: dict[str, int] = {}
    for source_method, destination_method in MAPPINGS.items():
        source_path = root / "events" / f"{source_method}.jsonl"
        destination_path = root / "events" / f"{destination_method}.jsonl"
        source_rows = latest_by_task(source_path, source_method)
        existing = {
            (str(row["suite"]), str(row["case_id"]))
            for row in read_jsonl(destination_path)
            if row.get("method") == destination_method
            and row.get("protocol_sha256") == new_hash
        }
        migrated = 0
        for task_key, source in source_rows.items():
            if task_key in existing:
                continue
            row = dict(source)
            row.update({
                "method": destination_method,
                "protocol": protocol["name"],
                "protocol_sha256": new_hash,
                "event_utc": datetime.now(timezone.utc).isoformat(),
                "method_migration": {
                    "source_method": source_method,
                    "source_protocol": source.get("protocol"),
                    "source_protocol_sha256": source.get("protocol_sha256"),
                    "reason": (
                        "v2 ablation naming; generated candidates, judgments, "
                        "fixed samples, and token accounting are unchanged"
                    ),
                },
            })
            old_artifact = f"/artifacts/{source_method}/"
            new_artifact = f"/artifacts/{destination_method}/"
            for field in ("artifact", "hidden_source", "api_calls_artifact"):
                value = row.get(field)
                if isinstance(value, str):
                    row[field] = value.replace(old_artifact, new_artifact)
            append_jsonl(destination_path, row)
            migrated += 1
        counts[destination_method] = migrated
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    args = parser.parse_args()
    print(json.dumps(migrate(args.results_root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
