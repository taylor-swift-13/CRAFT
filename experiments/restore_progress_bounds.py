#!/usr/bin/env python3
"""Restore pruned progress bounds to synthesized SFT answers that lack one.

The coverage-greedy core drops bounds that reject no negative trace.  With
the v7 sampler (no range family) a guard-derived progress bound such as
``i <= n`` can be coverage-free yet still be required by a full inductive
proof.  For every synthesized row whose answer has no informative-progress
clause, this script re-adds the informative-progress clauses recorded in the
core's ``dropped`` lists (they are Houdini survivors of the rollout union),
re-runs Houdini on the augmented set, and keeps the augmentation only when
every clause survives.  Rows are updated in place; a report records counts.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper.scripts._curation_common import digest_of, limit_memory  # noqa: E402
from paper.scripts.audit_sft_invariant_quality import _clause_features  # noqa: E402
from paper.scripts.filter_training_by_negative_coverage import _atomic_json, _source_from_sft  # noqa: E402
from rl_pipeline.common.program import parse_program, strip_postcondition  # noqa: E402
from rl_pipeline.common.state import dedup_normalized, extract_invariants  # noqa: E402
from rl_pipeline.reward.filters import HoudiniFilter  # noqa: E402
from rl_pipeline.sampler.example_sampler import ExampleSampler  # noqa: E402

MAX_RESTORED = 3


_init = limit_memory


def _job(job: Tuple[str, str, List[str], List[str]]) -> dict:
    digest, source, answer, candidates = job
    try:
        program = parse_program(strip_postcondition(source))
        augmented = dedup_normalized(answer + candidates)
        survivors = dedup_normalized(HoudiniFilter().filter(program, 0, augmented, None))
        if set(augmented) == set(survivors):
            return {"digest": digest, "status": "restored", "answer": survivors}
        # keep any restored clause that survived together with the full answer
        kept = [c for c in survivors if c in set(answer) or c in set(candidates)]
        if set(answer) <= set(kept) and len(kept) > len(answer):
            return {"digest": digest, "status": "partially_restored", "answer": kept}
        return {"digest": digest, "status": "not_inductive", "answer": answer}
    except Exception as error:
        return {"digest": digest, "status": "error", "error": f"{type(error).__name__}: {str(error)[:150]}",
                "answer": answer}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--cores", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--memory-cap", type=int, default=3_000_000_000)
    parser.add_argument("--wp-timeout", type=int, default=5)
    args = parser.parse_args()

    os.environ.setdefault("CRAFT_WP_TIMEOUT", str(args.wp_timeout))
    os.environ.setdefault("CRAFT_WP_PAR", "2")

    cores: Dict[str, dict] = {}
    with args.cores.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            cores[row["digest"]] = row
    rows = json.loads(args.input.read_text(encoding="utf-8"))
    jobs = []
    row_by_digest = {}
    status = Counter()
    for record in rows:
        source = _source_from_sft(record)
        digest = digest_of(source)
        row_by_digest[digest] = record
        if "synthesis" not in record:
            status["archival_untouched"] += 1
            continue
        answer = extract_invariants(next(t["value"] for t in record["conversations"] if t["from"] == "gpt"))
        try:
            program = parse_program(strip_postcondition(source))
            modified = set(ExampleSampler._modified_vars(program))
        except Exception:
            status["unparsable"] += 1
            continue
        if any(_clause_features(c, program, modified)["informative_progress"] for c in answer):
            status["already_has_progress"] += 1
            continue
        core = cores.get(digest) or {}
        dropped = core.get("dropped", {})
        pool = dedup_normalized(dropped.get("no_coverage_gain", []) + dropped.get("implied", []))
        candidates = [c for c in pool
                      if _clause_features(c, program, modified)["informative_progress"]][:MAX_RESTORED]
        if not candidates:
            status["no_candidate"] += 1
            continue
        jobs.append((digest, source, answer, candidates))
    print(f"rows: {len(rows)}, to restore: {len(jobs)}, {dict(status)}", flush=True)

    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx,
                             initializer=_init, initargs=(args.memory_cap,)) as pool:
        futures = [pool.submit(_job, job) for job in jobs]
        for count, future in enumerate(as_completed(futures), 1):
            result = future.result()
            status[result["status"]] += 1
            if result["status"] in ("restored", "partially_restored"):
                record = row_by_digest[result["digest"]]
                for turn in record["conversations"]:
                    if turn["from"] == "gpt":
                        turn["value"] = "\n".join(f"loop invariant {c};" for c in result["answer"])
                record["synthesis"]["progress_restored"] = True
                record["synthesis"]["n_final"] = len(result["answer"])
            if count % 100 == 0 or count == len(futures):
                print(f"[restore] {count}/{len(futures)} {dict(status)}", flush=True)

    _atomic_json(rows, args.output)
    report = {"schema_version": 1, "rows": len(rows), "status": dict(status),
              "policy": "re-add up to %d informative-progress clauses from the core's dropped lists; "
                        "keep only if the augmented set fully survives Houdini" % MAX_RESTORED}
    _atomic_json(report, args.report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
