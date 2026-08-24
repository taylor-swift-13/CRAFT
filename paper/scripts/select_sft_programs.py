#!/usr/bin/env python3
"""Select programs for SFT synthesis: curated SFT programs plus related extras.

The output is an SFT-format JSON (``conversations`` rows).  Rows taken from
the curated SFT file keep their archival answer (the synthesizer merges it
into the rollout union); rows added from the curated RL pool carry an empty
``gpt`` turn.  Extras are ranked by relatedness to the evaluation corpus,
then relation-negative share, then negative-trace count, with a per-shape
cap so no loop shape dominates the additions.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from paper.scripts._curation_common import digest_of as _digest  # noqa: E402
from paper.scripts.filter_training_by_negative_coverage import (  # noqa: E402
    _atomic_json,
    _display_path,
    _source_from_rl,
    _source_from_sft,
)
from paper.scripts.program_fingerprint import (  # noqa: E402
    DEFAULT_DEDUP_LEVELS,
    EvaluationIndex,
    difficulty_verdict,
    fingerprint,
    nla_admissible,
    quota_select,
    relatedness_score,
    tv_distance,
)
from paper.scripts.sanitize_training_prompts import _canonical_user  # noqa: E402
from rl_pipeline.common import prompts  # noqa: E402

NLA_SHAPE_CAP = 64


def _sft_row(source: str, selection: dict) -> dict:
    return {
        "conversations": [
            {"from": "system", "value": prompts.system_prompt()},
            {"from": "human", "value": _canonical_user(source)},
            {"from": "gpt", "value": ""},
        ],
        "selection": selection,
    }


class NlaBudget:
    """Admission counter for the NLA-boost exception (shape-capped)."""

    def __init__(self, budget: int):
        self.remaining = budget
        self.shapes: Counter = Counter()
        self.admitted: Counter = Counter()

    def admit(self, features: dict, shape: str, origin: str) -> bool:
        if self.remaining <= 0 or not nla_admissible(features) or self.shapes[shape] >= NLA_SHAPE_CAP:
            return False
        self.remaining -= 1
        self.shapes[shape] += 1
        self.admitted[origin] += 1
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sft", type=Path, required=True, help="curated SFT json")
    parser.add_argument("--rl", type=Path, required=True, help="curated RL parquet (extra_info.curation)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--target", type=int, default=6000, help="total programs wanted")
    parser.add_argument("--extra-shape-cap", type=int, default=4)
    parser.add_argument("--min-relatedness", type=float, default=0.5)
    parser.add_argument("--keep-existing-all", action="store_true",
                        help="keep every archival row that passes the difficulty screen (bypass the "
                             "cell quota for existing rows; pool extras still follow the quota)")
    parser.add_argument("--nla-extra", type=int, default=0,
                        help="additionally admit up to N nonlinear programs (archival rows first, "
                             "then pool programs), bypassing the cell quota and the "
                             "product-of-modified difficulty rule; too-easy and very-wide loops stay excluded")
    parser.add_argument("--match-eval", action="store_true",
                        help="--rl is a raw pool parquet (no curation column); fill evaluation-cell "
                             "deficits of the existing SFT set by quota, ignoring negative-sample gates")
    args = parser.parse_args()
    if args.match_eval:
        return match_eval(args)

    sft_records = json.loads(args.sft.read_text(encoding="utf-8"))
    chosen = []
    seen = set()
    for record in sft_records:
        digest = _digest(_source_from_sft(record))
        if digest in seen:
            continue
        seen.add(digest)
        chosen.append(record)
    n_sft = len(chosen)

    rl_rows = pq.read_table(args.rl).to_pylist()
    candidates = []
    for row in rl_rows:
        source = _source_from_rl(row)
        digest = _digest(source)
        if digest in seen:
            continue
        curation = json.loads(row["extra_info"]["curation"])
        traces = int(curation.get("n_negative_traces", 0))
        relation = int(curation.get("relation", 0))
        candidates.append((
            -float(curation.get("relatedness", 0.0)),
            -(relation / traces if traces else 0.0),
            -traces,
            digest,
            source,
            curation,
        ))
    candidates.sort()
    by_shape: Counter = Counter()
    extras = []
    skipped = Counter()
    for relatedness, share, traces, digest, source, curation in candidates:
        if len(chosen) + len(extras) >= args.target:
            break
        if -relatedness < args.min_relatedness:
            skipped["below_min_relatedness"] += 1
            continue
        shape = fingerprint(source).alpha_const_loop
        if by_shape[shape] >= args.extra_shape_cap:
            skipped["shape_cap"] += 1
            continue
        by_shape[shape] += 1
        extras.append({
            "conversations": [
                {"from": "system", "value": prompts.system_prompt()},
                {"from": "human", "value": _canonical_user(source)},
                {"from": "gpt", "value": ""},
            ],
            "selection": {"source": "rl", "relatedness": -relatedness,
                          "relation_share": round(-share, 4), "n_negative_traces": -traces},
        })
    output = chosen + extras
    _atomic_json(output, args.output)
    report = {
        "schema_version": 1,
        "sft_input": _display_path(args.sft),
        "rl_input": _display_path(args.rl),
        "output": _display_path(args.output),
        "target": args.target,
        "from_sft": n_sft,
        "from_rl": len(extras),
        "total": len(output),
        "rl_candidates": len(candidates),
        "skipped": dict(skipped),
        "extra_shape_cap": args.extra_shape_cap,
        "min_relatedness": args.min_relatedness,
    }
    _atomic_json(report, args.report)
    print(json.dumps(report, indent=2))


def match_eval(args) -> None:
    index = EvaluationIndex.from_evaluation_dirs()
    sft_records = json.loads(args.sft.read_text(encoding="utf-8"))
    chosen, seen, sft_cells = [], set(), Counter()
    chosen_cells: list = []  # cell per kept archival record, aligned with ``chosen``
    dropped_existing = Counter()
    nla = NlaBudget(args.nla_extra)
    fingerprints: dict = {}
    for record in sft_records:
        source = _source_from_sft(record)
        digest = _digest(source)
        if digest in seen:
            continue
        seen.add(digest)
        fp = fingerprints[digest] = fingerprint(source)
        difficulty = difficulty_verdict(fp.features)
        if difficulty:
            if difficulty == "too_hard" and nla.admit(fp.features, fp.alpha_const_loop, "archival"):
                pass
            else:
                dropped_existing[difficulty] += 1
                continue
        chosen.append(record)
        chosen_cells.append(fp.cell)
        sft_cells[fp.cell] += 1
    # Existing rows are quota candidates too (highest priority, so they are
    # kept first), which trims over-represented cells of the archive.
    demand = Counter()
    for cell, count in index.cell_counts.items():
        want = round(args.target * count / index.n_programs)
        if want > 0:
            demand[cell] = want
    candidates = {}
    extras_nla: list = []
    existing_by_digest = {}
    if not args.keep_existing_all:
        for record in chosen:
            source = _source_from_sft(record)
            digest = _digest(source)
            fp = fingerprints[digest]
            existing_by_digest[digest] = (record, fp.cell)
            candidates[digest] = (fp.cell, fp.alpha_const_loop, 1e9, source)
        chosen = []
        chosen_cells = []
    else:
        # All surviving archival rows are kept; pool quotas shrink accordingly.
        for cell, count in sft_cells.items():
            if cell in demand:
                demand[cell] = max(0, demand[cell] - count)
    rejected = Counter()
    for row in pq.read_table(args.rl, columns=["prompt"]).to_pylist():
        source = _source_from_rl(row)
        digest = _digest(source)
        if digest in seen:
            rejected["already_in_sft"] += 1
            continue
        verdict = index.assess(source, dedup_levels=DEFAULT_DEDUP_LEVELS)
        if verdict["duplicate_level"]:
            rejected["duplicate_of_eval"] += 1
            continue
        features = verdict["fingerprint"]["features"]
        difficulty = difficulty_verdict(features)
        if difficulty:
            # NOTE: unlike the archival stream, gated pool programs are also
            # eligible for the NLA budget when too-easy (vacuous for nonlinear
            # programs, kept for parity with the released selection).
            if nla.admit(features, verdict["fingerprint"]["alpha_const_loop"], "pool"):
                seen.add(digest)
                extras_nla.append(_sft_row(source, {
                    "source": "nla_extra", "cell": verdict["cell"],
                    "relatedness": relatedness_score(verdict),
                }))
            else:
                rejected[difficulty] += 1
            continue
        if demand.get(verdict["cell"], 0) <= 0:
            rejected["cell_not_needed"] += 1
            continue
        seen.add(digest)
        candidates[digest] = (verdict["cell"], verdict["fingerprint"]["alpha_const_loop"],
                              relatedness_score(verdict), source)
    selected = quota_select({d: v[:3] for d, v in candidates.items()}, demand, args.target,
                            args.extra_shape_cap)
    extras = []
    for digest in selected:
        if digest in existing_by_digest:
            record, cell = existing_by_digest[digest]
            chosen.append(record)
            chosen_cells.append(cell)
            continue
        cell, _, relatedness, source = candidates[digest]
        extras.append(_sft_row(source, {"source": "rl_pool", "cell": cell, "relatedness": relatedness}))
    output = chosen + extras + extras_nla
    _atomic_json(output, args.output)
    kept_existing_cells = Counter(chosen_cells)
    final_cells = kept_existing_cells + Counter(c["selection"]["cell"] for c in extras + extras_nla)
    report = {
        "schema_version": 2,
        "mode": "match_eval",
        "sft_input": _display_path(args.sft),
        "pool": _display_path(args.rl),
        "output": _display_path(args.output),
        "target": args.target,
        "from_sft": len(chosen),
        "dropped_existing_by_difficulty": dict(dropped_existing),
        "dropped_existing_by_quota": len(existing_by_digest) - len(chosen),
        "from_pool": len(extras),
        "nla_extra_admitted": dict(nla.admitted),
        "total": len(output),
        "deficit_requested": sum(demand.values()),
        "deficit_unfilled": sum(demand.values()) - len(extras),
        "candidates": len(candidates),
        "rejected": dict(rejected),
        "tv_distance_before": tv_distance(sft_cells, index.cell_counts),
        "tv_distance_after": tv_distance(final_cells, index.cell_counts),
        "eval_programs_in_covered_cells_before": sum(c for cell, c in index.cell_counts.items() if sft_cells.get(cell)),
        "eval_programs_in_covered_cells_after": sum(c for cell, c in index.cell_counts.items() if final_cells.get(cell)),
        "extra_shape_cap": args.extra_shape_cap,
    }
    _atomic_json(report, args.report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
