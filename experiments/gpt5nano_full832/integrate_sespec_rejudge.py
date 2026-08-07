#!/usr/bin/env python3
"""Promote the corrected SESpec 832 rejudge into the canonical result stream.

The saved SESpec output can contain ``loop invariant`` text in malformed
function contracts as well as in the native annotation immediately preceding
the loop.  ``rejudge_sespec.py`` adjudicates the latter and saves one result per
task.  This script copies those corrected invariant sets and verification
booleans into the append-only SESpec event stream.  It deliberately marks the
old negative-rejection score stale; the normal ``run score`` command then
recomputes that metric against the frozen samples without making model calls.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from .common import (
    DEFAULT_RESULTS_ROOT,
    append_jsonl,
    discover_tasks,
    latest_rows,
)
from .rejudge_sespec import extract_loop_invariants, find_loop_annotation
from .run import event_path


PROTOCOL = (
    "sespec-native-loop-v2: complete clauses from the annotation immediately "
    "preceding the first real loop; generated global logic context retained "
    "for final WP; common loop-assigns frame; original target restored"
)


def read_rejudge(root: Path) -> dict[tuple[str, str], dict]:
    path = root / "sespec832_rejudge_native_loop" / "results.jsonl"
    rows = {}
    for line in path.read_text(errors="ignore").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows[(str(row["suite"]), str(row["case_id"]))] = row
    if len(rows) != 832:
        raise RuntimeError(f"expected 832 corrected SESpec rows, found {len(rows)}")
    return rows


def corrected_invariants(row: dict) -> list[str]:
    artifact = row.get("artifact")
    if not artifact or row.get("status") != "completed":
        return []
    source = Path(str(artifact)).read_text(errors="ignore")
    invariants = extract_loop_invariants(find_loop_annotation(source))
    expected = int(row.get("invariant_count") or 0)
    if len(invariants) != expected:
        raise RuntimeError(
            f"{row['suite']}/{row['case_id']}: parsed {len(invariants)} "
            f"corrected invariants, expected {expected}"
        )
    return invariants


def integrate(root: Path) -> int:
    corrected = read_rejudge(root)
    destination = event_path(root, "sespec")
    current = latest_rows([destination])
    tasks = {(task.suite, task.case_id): task for task in discover_tasks()}
    if set(corrected) != set(tasks):
        raise RuntimeError("corrected SESpec task set does not match the 832 manifest")

    count = 0
    for key, task in tasks.items():
        old = current.get(task.key("sespec"))
        if old is None:
            raise RuntimeError(f"{task.suite}/{task.case_id}: missing canonical SESpec row")
        if old.get("sespec_rejudge_protocol") == PROTOCOL:
            continue
        rejudge = corrected[key]
        invariants = corrected_invariants(rejudge)
        updated = dict(old)
        updated.update(
            {
                "event_utc": datetime.now(timezone.utc).isoformat(),
                "invariants": invariants,
                "invariant_count": len(invariants),
                "verified": bool(rejudge.get("verified")),
                "judge_error": (
                    None
                    if rejudge.get("verified")
                    else rejudge.get("error")
                    or "Frama-C/WP did not prove every scheduled goal"
                ),
                "judge_seconds": float(rejudge.get("seconds") or 0.0),
                "judge_time_accounting": "corrected_native_loop_rejudge",
                "sespec_rejudge_protocol": PROTOCOL,
                "sespec_rejudge_status": rejudge.get("status"),
                "sespec_rejudge_annotated": rejudge.get("annotated"),
                "sespec_rejudge_proved_goals": rejudge.get("proved_goals"),
                "sespec_rejudge_total_goals": rejudge.get("total_goals"),
                "negative_score_status": "stale_corrected_invariants",
                "negative_score_error": (
                    "must be recomputed after corrected SESpec invariant extraction"
                ),
                "negative_rejection_score": None,
                "rejected_negative_count": None,
                "binary_frama_c_validation": None,
                "score_surviving_invariants": [],
            }
        )
        append_jsonl(destination, updated)
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    args = parser.parse_args()
    print(f"integrated SESpec rows={integrate(args.results_root)}")
    print(
        "Run `python -m experiments.gpt5nano_full832.run score --workers N` "
        "to refresh negative-rejection scores, then summarize/report."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
