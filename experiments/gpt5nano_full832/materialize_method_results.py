#!/usr/bin/env python3
"""Materialize one self-contained final result index per evaluated method."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import shutil

from .common import DEFAULT_RESULTS_ROOT, METHODS


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(errors="ignore").splitlines()
        if line.strip()
    ]


def materialize(root: Path) -> None:
    rows = read_jsonl(root / "latest.jsonl")
    with (root / "summary.csv").open(newline="") as handle:
        summaries = list(csv.DictReader(handle))
    methods_root = root / "methods"
    methods_root.mkdir(parents=True, exist_ok=True)

    for method in METHODS:
        destination = methods_root / method
        destination.mkdir(parents=True, exist_ok=True)
        selected = [row for row in rows if row["method"] == method]
        if len(selected) != 832:
            raise RuntimeError(f"{method}: expected 832 rows, found {len(selected)}")
        method_summaries = [row for row in summaries if row["method"] == method]
        if len(method_summaries) != 4:
            raise RuntimeError(
                f"{method}: expected four suite summaries, found {len(method_summaries)}"
            )

        with (destination / "results.jsonl").open("w") as handle:
            for row in selected:
                handle.write(json.dumps(row, sort_keys=True) + "\n")

        fields = sorted({key for row in selected for key in row})
        with (destination / "results.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(selected)

        with (destination / "summary.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(method_summaries[0]))
            writer.writeheader()
            writer.writerows(method_summaries)
        all_summary = next(row for row in method_summaries if row["suite"] == "all")
        (destination / "summary.json").write_text(
            json.dumps(all_summary, indent=2, sort_keys=True) + "\n"
        )

        artifact_link = destination / "artifacts"
        if artifact_link.is_symlink() or artifact_link.exists():
            if artifact_link.is_dir() and not artifact_link.is_symlink():
                shutil.rmtree(artifact_link)
            else:
                artifact_link.unlink()
        artifact_target = root / "artifacts" / method
        if artifact_target.is_dir():
            artifact_link.symlink_to(Path("..") / ".." / "artifacts" / method)

    index = {
        method: {
            "results": f"methods/{method}/results.jsonl",
            "summary": f"methods/{method}/summary.json",
            "artifacts": (
                f"methods/{method}/artifacts"
                if (root / "artifacts" / method).is_dir()
                else None
            ),
        }
        for method in METHODS
    }
    (methods_root / "index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    args = parser.parse_args()
    materialize(args.results_root)
    print(args.results_root / "methods")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
