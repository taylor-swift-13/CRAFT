#!/usr/bin/env python3
"""Audit final SFT answers by deterministic negative-trace rejection.

The JSONL ledger is append-only and resumable. Each row records the exact
rejected trace indices, not only a scalar count, so a candidate replacement
can be required to preserve the baseline set and add new rejections.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from paper.scripts._curation_common import record_source_and_answer  # noqa: E402
from rl_pipeline.common.state import eval_predicate, extract_invariants, normalize_invariant  # noqa: E402
from rl_pipeline.sampler.example_sampler import ExampleSampler  # noqa: E402


DEFAULT_SFT = ROOT / "traindata" / "craft_sft_clean.json"
DEFAULT_LEDGER = ROOT / "paper" / "artifacts" / "sft_negative_rejection.jsonl"


def _rejected_states(negatives, invariants: list[str]) -> set[int]:
    rejected: set[int] = set()
    for invariant in invariants:
        condition = normalize_invariant(invariant)
        for index, state in enumerate(negatives):
            if index not in rejected and eval_predicate(condition, state) is False:
                rejected.add(index)
    return rejected


def _score(job: tuple[int, str, str, str, list[str], int, int]) -> dict:
    row, source_sha256, answer_sha256, source, invariants, runs, seed = job
    try:
        examples = ExampleSampler(source, n_runs=runs, seed=seed).sample()
    except Exception as error:
        return {
            "row": row,
            "source_sha256": source_sha256,
            "answer_sha256": answer_sha256,
            "seed": seed,
            "runs_requested": runs,
            "scorable": False,
            "error": f"{type(error).__name__}: {error}",
        }
    negatives = examples.neg(0)
    groups = examples.groups(0)
    rejected_states = _rejected_states(negatives, invariants)
    rejected = {
        group for group, indices in enumerate(groups)
        if any(index in rejected_states for index in indices)
    }
    stats = examples.stats[0]
    relation_count = int(stats.get("relation", 0))
    post_count = int(stats.get("bound_overrun", 0))
    range_count = int(stats.get("bound_escape", 0))
    frame_count = int(stats.get("frame", 0))
    # Groups are emitted in this exact order by ExampleSampler._negatives.
    boundaries = {
        "relation": (0, relation_count),
        "post_exit": (relation_count, relation_count + post_count),
        "range": (
            relation_count + post_count,
            relation_count + post_count + range_count,
        ),
        "frame": (
            relation_count + post_count + range_count,
            relation_count + post_count + range_count + frame_count,
        ),
    }
    by_family = {}
    for family, (start, end) in boundaries.items():
        family_rejected = sorted(group - start for group in rejected if start <= group < end)
        by_family[family] = {
            "total": end - start,
            "rejected": len(family_rejected),
            "indices": family_rejected,
        }
    return {
        "row": row,
        "source_sha256": source_sha256,
        "answer_sha256": answer_sha256,
        "scorable": True,
        "seed": seed,
        "runs_requested": runs,
        "n_positive_states": len(examples.pos(0)),
        "n_negative_traces": len(groups),
        "rejected": len(rejected),
        "rejected_indices": sorted(rejected),
        "coverage": len(rejected) / len(groups) if groups else None,
        "families": by_family,
        "sampler_stats": stats,
    }


def _load_latest(path: Path) -> dict[int, dict]:
    if not path.is_file():
        return {}
    latest = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                item = json.loads(line)
                latest[int(item["row"])] = item
    return latest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sft", type=Path, default=DEFAULT_SFT)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--jobs", type=int, default=16)
    parser.add_argument("--runs", type=int, default=12)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--stop", type=int)
    parser.add_argument("--retry-unscorable", action="store_true")
    parser.add_argument(
        "--retry-zero-negatives",
        action="store_true",
        help="rescore completed rows whose latest result has no negative trace",
    )
    args = parser.parse_args()
    records = json.loads(args.sft.read_text(encoding="utf-8"))
    stop = min(args.stop if args.stop is not None else len(records), len(records))
    latest = _load_latest(args.ledger)
    jobs = []
    for row in range(args.start, stop):
        record = records[row]
        source, answer = record_source_and_answer(record)
        source_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()
        answer_sha256 = hashlib.sha256(answer.encode("utf-8")).hexdigest()
        previous = latest.get(row)
        if previous is not None:
            exact_record = (
                previous.get("source_sha256") == source_sha256
                and previous.get("answer_sha256") == answer_sha256
            )
            retry = (
                args.retry_unscorable and not previous.get("scorable", False)
            ) or (
                args.retry_zero_negatives
                and previous.get("scorable", False)
                and int(previous.get("n_negative_traces", 0)) == 0
            )
            if exact_record and not retry:
                continue
        jobs.append((
            row,
            source_sha256,
            answer_sha256,
            source,
            extract_invariants(answer),
            args.runs,
            args.seed,
        ))
    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    with args.ledger.open("a", encoding="utf-8") as handle:
        with ProcessPoolExecutor(max_workers=args.jobs) as executor:
            futures = {executor.submit(_score, job): job for job in jobs}
            for count, future in enumerate(as_completed(futures), 1):
                try:
                    result = future.result()
                except BrokenProcessPool:
                    raise
                except Exception as error:
                    # Preserve row attribution and allow the resumable audit to
                    # continue past an isolated compiler/executor failure.
                    job = futures[future]
                    row = job[0]
                    result = {
                        "row": row,
                        "source_sha256": job[1],
                        "answer_sha256": job[2],
                        "seed": args.seed,
                        "runs_requested": args.runs,
                        "scorable": False,
                        "error": f"{type(error).__name__}: {error}",
                    }
                handle.write(json.dumps(result, sort_keys=True) + "\n")
                handle.flush()
                if count % 25 == 0 or count == len(futures):
                    print(f"scored {count}/{len(futures)} pending rows", flush=True)


if __name__ == "__main__":
    main()
