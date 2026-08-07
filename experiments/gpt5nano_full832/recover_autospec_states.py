"""Recover exact AutoSpec invariants from the final state printed in logs.

AutoSpec prints its persisted ``SAVE_PICKLE`` state after local Frama-C
filtering.  This utility is intentionally conservative: completed rows are
copied only when that final state contains loop-invariant clauses.  Ambiguous
completed rows are omitted so the normal runner will regenerate them.  Native
failures are retained because their API calls and costs are valid failures.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

from rl_pipeline.common.state import dedup_normalized, extract_invariants


_TIMEDELTA = re.compile(r"datetime\.timedelta\([^)]*\)")


def _latest(path: Path) -> dict[tuple[str, str], dict]:
    rows: dict[tuple[str, str], dict] = {}
    for line in path.read_text(errors="ignore").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows[(str(row["suite"]), str(row["case_id"]))] = row
    return rows


def _final_state_invariants(log_path: Path) -> tuple[bool, list[str]]:
    states: list[dict] = []
    for line in log_path.read_text(errors="ignore").splitlines():
        if not line.startswith("{'CurTaskID':"):
            continue
        try:
            states.append(ast.literal_eval(_TIMEDELTA.sub("0", line)))
        except (SyntaxError, ValueError):
            continue
    if not states:
        return False, []

    state = states[-1]
    clauses: list[str] = []
    has_final_loop_specs = False
    for index, task_type in enumerate(state.get("TaskList", []), 1):
        if task_type not in (2, 3):
            continue
        specs = state.get(str(index), [])
        has_final_loop_specs = has_final_loop_specs or bool(specs)
        clauses.extend(extract_invariants("\n".join(specs)))
    return has_final_loop_specs, dedup_normalized(clauses)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--dest-root", type=Path, required=True)
    args = parser.parse_args()

    source_events = args.source_root / "events" / "autospec.jsonl"
    dest_events = args.dest_root / "events" / "autospec.jsonl"
    rows = _latest(source_events)
    if len(rows) != 832:
        raise RuntimeError(f"expected 832 source rows, found {len(rows)}")

    copied_failed = 0
    recovered = 0
    omitted = 0
    output: list[dict] = []
    for key in sorted(rows):
        row = dict(rows[key])
        if row.get("generation_status") != "completed":
            output.append(row)
            copied_failed += 1
            continue

        hidden_source = Path(str(row["hidden_source"]))
        log_path = hidden_source.parent / "command.log"
        recovered_state, invariants = _final_state_invariants(log_path)
        if not recovered_state:
            omitted += 1
            continue
        row.update({
            "invariants": invariants,
            "invariant_recovery": "autospec_final_save_pickle_state",
            "invariant_recovery_log": str(log_path.resolve()),
        })
        output.append(row)
        recovered += 1

    dest_events.parent.mkdir(parents=True, exist_ok=True)
    with dest_events.open("w", encoding="utf-8") as handle:
        for row in output:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    print(json.dumps({
        "source_rows": len(rows),
        "recovered_completed": recovered,
        "retained_failures": copied_failed,
        "omitted_for_regeneration": omitted,
        "destination_rows": len(output),
        "destination": str(dest_events),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
