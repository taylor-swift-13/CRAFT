#!/usr/bin/env python3
"""Recompute compose@k / pass@k on a new k-grid from saved 10-rollout archives.

For every program in a run's ``r10_results.csv``, compose@k unions the FIRST
``k`` saved rollouts (API call order), applies exactly the inference filter
chain (PreFrama + Houdini on the target-hidden program), and judges the
survivors on the original program — i.e. ``InferenceFramework._attempt``
replayed at a smaller budget.  pass@k is the standard unbiased estimator
``1 - C(n-c, k)/C(n, k)`` over the archived per-rollout verdicts
(``pass10_judged.jsonl``, n = 10).

Resumable: ``<run>/grid_recompute.jsonl`` is keyed by (suite, case_id, k);
``<run>/grid_summary.json`` aggregates counts overall and per suite.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import math
import multiprocessing
import os
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper.scripts._curation_common import limit_memory  # noqa: E402
from paper.scripts.filter_training_by_negative_coverage import _atomic_json  # noqa: E402

DEFAULT_KS = (1, 4, 8)

_TASKS: Optional[dict] = None
_FILTER = None


def _init(memory_cap: int) -> None:
    global _TASKS, _FILTER
    limit_memory(memory_cap)
    from experiments.gpt5nano_full832.common import discover_tasks
    from rl_pipeline.reward.filters import auto_filter
    _TASKS = {(t.suite, t.case_id): t for t in discover_tasks()}
    _FILTER = auto_filter()


def _job(job: Tuple[str, str, int, List[List[str]]]) -> dict:
    suite, case_id, k, rollouts = job
    started = time.perf_counter()
    try:
        from experiments.gpt5nano_full832.common import judge_invariants
        from rl_pipeline.common.program import parse_program, strip_postcondition
        from rl_pipeline.common.state import dedup_normalized
        assert _TASKS is not None and _FILTER is not None
        task = _TASKS[(suite, case_id)]
        union = dedup_normalized(c for r in rollouts[:k] for c in r)
        masked = parse_program(strip_postcondition(task.source_path.read_text(errors="ignore")))
        survivors = _FILTER.filter(masked, 0, union, None) if union else []
        judged = judge_invariants(task, survivors) if survivors else {"verified": False}
        return {"suite": suite, "case_id": case_id, "k": k, "status": "ok",
                "verified": bool(judged.get("verified")),
                "n_union": len(union), "n_survivors": len(survivors),
                "seconds": round(time.perf_counter() - started, 2)}
    except Exception as error:
        return {"suite": suite, "case_id": case_id, "k": k, "status": "error",
                "error": f"{type(error).__name__}: {str(error)[:200]}",
                "seconds": round(time.perf_counter() - started, 2)}


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k estimator (Chen et al., 2021)."""
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def summarize(run: Path, ks: Tuple[int, ...]) -> dict:
    ledger: Dict[Tuple[str, str, int], dict] = {}
    with (run / "grid_recompute.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            ledger[(row["suite"], row["case_id"], row["k"])] = row
    compose = {k: Counter() for k in ks}
    for (suite, _case, k), row in ledger.items():
        if k in compose and row["status"] == "ok":
            compose[k][suite] += bool(row["verified"])
            compose[k]["all"] += bool(row["verified"])
            compose[k]["scored_" + suite] += 1
            compose[k]["scored"] += 1
    passes = {k: {"all": 0.0} for k in ks}
    pass_path = run / "pass10_judged.jsonl"
    n_pass_rows = 0
    if pass_path.is_file():
        with pass_path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                verdicts = [bool(r.get("verified")) for r in row.get("per_rollout", [])]
                if not verdicts:
                    continue
                n_pass_rows += 1
                for k in ks:
                    value = pass_at_k(len(verdicts), sum(verdicts), min(k, len(verdicts)))
                    passes[k]["all"] += value
                    passes[k][row["suite"]] = passes[k].get(row["suite"], 0.0) + value
    return {
        "schema_version": 1,
        "run": run.name,
        "ks": list(ks),
        "compose": {str(k): dict(v) for k, v in compose.items()},
        "pass_estimate": {str(k): {s: round(v, 2) for s, v in d.items()} for k, d in passes.items()},
        "pass_rows": n_pass_rows,
        "policy": "compose@k = union of the FIRST k archived rollouts -> inference filter chain -> "
                  "target judge; pass@k = unbiased estimator over the archived per-rollout verdicts",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=Path, required=True, help="results/<run dir> with r10_results.csv")
    parser.add_argument("--ks", default=",".join(map(str, DEFAULT_KS)))
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--memory-cap", type=int, default=3_000_000_000)
    parser.add_argument("--wp-timeout", type=int, default=5)
    parser.add_argument("--wp-par", type=int, default=2)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    ks = tuple(int(k) for k in args.ks.split(","))
    os.environ.setdefault("CRAFT_WP_TIMEOUT", str(args.wp_timeout))
    os.environ.setdefault("CRAFT_WP_PAR", str(args.wp_par))

    ledger_path = args.run / "grid_recompute.jsonl"
    if not args.summary_only:
        done = set()
        if ledger_path.is_file():
            with ledger_path.open(encoding="utf-8") as handle:
                for line in handle:
                    row = json.loads(line)
                    if row["status"] == "ok":
                        done.add((row["suite"], row["case_id"], row["k"]))
        jobs = []
        with (args.run / "r10_results.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                rollouts = ast.literal_eval(row["rollouts"])
                for k in ks:
                    if (row["suite"], row["case_id"], k) not in done:
                        jobs.append((row["suite"], row["case_id"], k, rollouts))
        print(f"{args.run.name}: {len(jobs)} jobs pending (ks={ks})", flush=True)
        counts = Counter()
        with ProcessPoolExecutor(
            max_workers=args.workers, mp_context=multiprocessing.get_context("spawn"),
            initializer=_init, initargs=(args.memory_cap,),
        ) as pool, ledger_path.open("a", encoding="utf-8") as out:
            futures = [pool.submit(_job, job) for job in jobs]
            for count, future in enumerate(as_completed(futures), 1):
                row = future.result()
                counts[row["status"]] += 1
                out.write(json.dumps(row, sort_keys=True) + "\n")
                out.flush()
                if count % 100 == 0 or count == len(futures):
                    print(f"[grid] {count}/{len(futures)} {dict(counts)}", flush=True)

    summary = summarize(args.run, ks)
    _atomic_json(summary, args.run / "grid_summary.json")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
