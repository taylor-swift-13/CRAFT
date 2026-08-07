#!/usr/bin/env python3
"""Run a small stratified AutoSpec probe with exact provider token usage."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import tempfile

import tiktoken

from experiments.gpt5nano_full832.common import discover_tasks
from experiments.gpt5nano_full832.native import run_autospec


DEFAULT_AUTOSPEC_ROOT = Path(
    "/home/yangfp/TRASH/SESpecTrash/represent/external/autospec"
)
DEFAULT_CASES = (
    ("linear", "1"),
    ("linear", "87"),
    ("NLA_lipus", "1"),
    ("NLA_lipus", "25"),
    ("Loopy", "36"),
    ("Loopy", "391"),
)
USAGE_RE = re.compile(r"AUTOSPEC_API_USAGE=(\{[^\n]+\})")


def _seconds(text: str, name: str) -> float | None:
    matches = re.findall(
        rf"^{re.escape(name)}\s*=\s*(\d+):(\d+):(\d+(?:\.\d+)?)\s*$",
        text,
        flags=re.MULTILINE,
    )
    if not matches:
        return None
    hours, minutes, seconds = matches[-1]
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _visible_output_tokens(output: Path) -> int:
    encoding = tiktoken.get_encoding("o200k_base")
    total = 0
    for path in sorted(output.glob("*_gen_*.c")):
        clauses = []
        for line in path.read_text(errors="ignore").splitlines():
            stripped = line.strip()
            if stripped.startswith(("loop invariant", "loop assigns")):
                clauses.append(stripped)
        total += len(encoding.encode("\n".join(clauses)))
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--autospec-root", type=Path, default=DEFAULT_AUTOSPEC_ROOT)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        metavar="SUITE/ID",
        help="case to run; repeat as needed (default: six stratified cases)",
    )
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required")

    tasks = {(task.suite, task.case_id): task for task in discover_tasks()}
    requested = []
    for value in args.case:
        suite, separator, case_id = value.partition("/")
        if not separator or (suite, case_id) not in tasks:
            parser.error(f"unknown case: {value}")
        requested.append((suite, case_id))
    selected = [tasks[key] for key in (requested or DEFAULT_CASES)]
    args.output_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="autospec_exact_usage_") as tmp:
        patched_root = Path(tmp) / "autospec"
        subprocess.run(
            ["cp", "-a", str(args.autospec_root), str(patched_root)], check=True
        )
        patch_path = Path(__file__).with_name("autospec_exact_usage.patch")
        subprocess.run(
            ["patch", "-p1", "-i", str(patch_path)], cwd=patched_root, check=True
        )

        rows = []
        for index, task in enumerate(selected, 1):
            directory = args.output_root / task.suite / task.case_id
            result = run_autospec(
                task,
                directory,
                autospec_root=patched_root,
                timeout=args.timeout,
            )
            log_path = directory / "command.log"
            log_text = log_path.read_text(errors="ignore")
            matches = USAGE_RE.findall(log_text)
            usage = json.loads(matches[-1]) if matches else {}
            row = {
                "suite": task.suite,
                "case_id": task.case_id,
                "status": result["generation_status"],
                "wall_seconds": result["generation_seconds"],
                "llm_seconds": _seconds(log_text, "llms_query_times"),
                "framac_seconds": _seconds(log_text, "total_solve_time"),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "reasoning_tokens": usage.get("reasoning_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "visible_output_tokens": _visible_output_tokens(
                    directory / "autospec_out"
                ),
                "choices": len(list((directory / "autospec_out").glob("*_gen_*.c"))),
            }
            rows.append(row)
            print(
                f"[{index}/{len(selected)}] {task.suite}/{task.case_id} "
                f"status={row['status']} wall={row['wall_seconds']:.2f}s "
                f"tokens={row['total_tokens']}",
                flush=True,
            )

    numeric = [row for row in rows if row["total_tokens"] is not None]
    summary = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "model": "gpt-5-nano",
        "cases": len(rows),
        "exact_usage_cases": len(numeric),
        "rows": rows,
        "means": {
            field: statistics.mean(row[field] for row in numeric)
            for field in (
                "wall_seconds",
                "llm_seconds",
                "framac_seconds",
                "prompt_tokens",
                "completion_tokens",
                "reasoning_tokens",
                "total_tokens",
                "visible_output_tokens",
            )
            if all(row[field] is not None for row in numeric)
        },
    }
    summary_path = args.output_root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(summary_path)
    print(json.dumps(summary["means"], indent=2, sort_keys=True))
    return 0 if len(numeric) == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
