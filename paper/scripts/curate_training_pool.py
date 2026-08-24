#!/usr/bin/env python3
"""Curate the RL / SFT training pool for gradient-bearing, test-related programs.

Every decision is target-independent and static (no model rollouts).  Inputs
are the released training files, the schema-current negative-coverage ledger
(``audit_training_negative_coverage.py``), and the 832-program evaluation
corpus, which is only ever used through its fingerprints.

Gates (a program is dropped when any *hard* gate fails; every verdict is
recorded in the report and in ``extra_info.curation`` of the output rows):

``duplicate_of_eval``   near-copy of an evaluation program at a dedup level
                        (default ``exact``, ``alpha``, ``alpha_const``).
``unscorable``          the sampler produced no negatives or failed.
``too_few_traces``      fewer than ``--min-traces`` negative traces: the
                        coverage reward takes too few distinct values.
``no_relation_signal``  fewer than ``--min-relation`` relation traces: pure
                        bounds already saturate the reward (no gradient
                        toward relational clauses).
``bounds_saturated``    relation share below ``--min-relation-share``: a
                        bounds-only answer scores >= (1 - share).
``unrelated_to_eval``   structural cell absent from the evaluation corpus
                        and no shared control-flow skeleton.
``shape_cap``           more than ``--per-shape-cap`` programs share one
                        loop shape (alpha-renamed, constant-abstracted guard
                        + body); the surplus is dropped.

Soft tags (kept, reported): ``hard_structure`` (nonlinear with many
variables), ``capped`` (execution capped; range negatives disabled).

Importance weights re-balance the survivors toward the evaluation corpus'
distribution over structural cells; they are stored per row so a trainer can
either sample by weight or ignore them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from paper.scripts.filter_training_by_negative_coverage import (  # noqa: E402
    _atomic_json,
    _display_path,
    _latest_by_digest,
    _source_from_rl,
    _source_from_sft,
)
from paper.scripts.program_fingerprint import (  # noqa: E402
    DEFAULT_DEDUP_LEVELS,
    DUPLICATE_LEVELS,
    EvaluationIndex,
    difficulty_verdict,
    quota_select,
    relatedness_score,
    tv_distance,
)
from rl_pipeline.sampler.example_sampler import NEGATIVE_SCHEMA_VERSION  # noqa: E402

HARD_GATES = (
    "duplicate_of_eval",
    "unscorable",
    "too_few_traces",
    "no_relation_signal",
    "bounds_saturated",
    "unrelated_to_eval",
    "too_easy",
    "too_hard",
    "shape_cap",
    "eval_quota",
)
CURATION_SCHEMA_VERSION = 1


def _digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _load_records(dataset: str, path: Path):
    if dataset == "sft":
        records = json.loads(path.read_text(encoding="utf-8"))
        return records, None, [_source_from_sft(r) for r in records]
    table = pq.read_table(path)
    records = table.to_pylist()
    return records, table, [_source_from_rl(r) for r in records]


def assess_program(
    source: str,
    ledger_row: Optional[dict],
    index: EvaluationIndex,
    *,
    dedup_levels: Sequence[str],
    min_traces: int,
    min_relation: int,
    min_relation_share: float,
) -> dict:
    """Per-program verdict: fingerprints, ledger facts, gate failures, tags."""
    verdict = index.assess(source, dedup_levels=dedup_levels)
    features = verdict["fingerprint"]["features"]
    gates: List[str] = []
    tags: List[str] = []
    if verdict["duplicate_level"] is not None:
        gates.append("duplicate_of_eval")
    if ledger_row is None:
        gates.append("unscorable")
        tags.append("ledger_missing")
        traces = relation = escape = rng = 0
        capped = False
    elif (
        ledger_row.get("coverage_schema_version") != NEGATIVE_SCHEMA_VERSION
    ):
        gates.append("unscorable")
        tags.append("ledger_stale")
        traces = relation = escape = rng = 0
        capped = False
    elif not ledger_row.get("scorable"):
        gates.append("unscorable")
        error = str(ledger_row.get("error", ""))
        tags.append("sampler_memory" if "MemoryError" in error else "sampler_error")
        traces = relation = escape = rng = 0
        capped = False
    else:
        stats = ledger_row.get("sampler_stats", {})
        traces = int(ledger_row.get("n_negative_traces", 0))
        relation = int(stats.get("relation", 0))
        escape = int(stats.get("escape", 0))
        rng = int(stats.get("range", 0))
        capped = bool(stats.get("capped"))
        if traces == 0:
            gates.append("unscorable")
            tags.append("zero_negatives")
        else:
            if traces < min_traces:
                gates.append("too_few_traces")
            if relation < min_relation:
                gates.append("no_relation_signal")
            elif relation / traces < min_relation_share:
                gates.append("bounds_saturated")
    if verdict["cell_eval_count"] == 0 and verdict["related_level"] is None:
        gates.append("unrelated_to_eval")
    difficulty = difficulty_verdict(features)
    if difficulty:
        gates.append(difficulty)
    if features.get("nonlinear") and int(features.get("n_pre_vars", 0)) >= 5:
        tags.append("hard_structure")
    if capped:
        tags.append("capped")
    return {
        "copy_levels": verdict["copy_levels"],
        "duplicate_level": verdict["duplicate_level"],
        "related_level": verdict["related_level"],
        "relatedness": relatedness_score(verdict),
        "similarity": verdict["similarity"],
        "cell": verdict["cell"],
        "cell_eval_count": verdict["cell_eval_count"],
        "stratum_guess": verdict["stratum_guess"],
        "shape": verdict["fingerprint"]["alpha_const_loop"],
        "full_shape": verdict["fingerprint"]["alpha_const"],
        "features": features,
        "n_negative_traces": traces,
        "relation": relation,
        "escape": escape,
        "range": rng,
        "relation_share": round(relation / traces, 4) if traces else 0.0,
        "gates": gates,
        "tags": tags,
    }


def apply_shape_cap(verdicts: Dict[str, dict], cap: int) -> None:
    """Keep at most ``cap`` survivors per loop shape, preferring distinct full
    program shapes, then richer negative sets.  Mutates ``gates`` in place."""
    by_shape: Dict[str, List[str]] = defaultdict(list)
    for digest, verdict in verdicts.items():
        if not verdict["gates"]:
            by_shape[verdict["shape"]].append(digest)
    for shape, digests in by_shape.items():
        if len(digests) <= cap:
            continue
        # Round-robin over full-program shapes so initializer/constant
        # variety survives the cap; within a full shape prefer more negatives.
        buckets: Dict[str, List[str]] = defaultdict(list)
        for digest in sorted(
            digests,
            key=lambda d: (
                -verdicts[d]["n_negative_traces"],
                -verdicts[d]["relation"],
                d,
            ),
        ):
            buckets[verdicts[digest]["full_shape"]].append(digest)
        order = sorted(buckets, key=lambda key: (-len(buckets[key]), key))
        kept: List[str] = []
        position = 0
        while len(kept) < cap:
            progressed = False
            for key in order:
                if position < len(buckets[key]):
                    kept.append(buckets[key][position])
                    progressed = True
                    if len(kept) == cap:
                        break
            if not progressed:
                break
            position += 1
        keep = set(kept)
        for digest in digests:
            if digest not in keep:
                verdicts[digest]["gates"].append("shape_cap")


def apply_eval_quota(verdicts: Dict[str, dict], index: EvaluationIndex, target: int, per_shape_cap: int,
                     seen: Optional[set] = None) -> None:
    """Keep a ``target``-sized subset whose structural-cell distribution
    tracks the evaluation corpus (see ``quota_select``); the rest of the
    survivors get the ``eval_quota`` gate.  Replaces the plain shape cap.
    Programs in ``seen`` (e.g. the SFT set) are taken last within each cell so
    RL keeps programs the SFT stage never showed the model."""
    seen = seen or set()
    candidates = {
        d: (v["cell"], v["shape"],
            (0 if d in seen else 1_000_000) + v["relatedness"] * 1000 + v["relation"] + v["n_negative_traces"] / 1000)
        for d, v in verdicts.items() if not v["gates"]
    }
    keep = set(quota_select(candidates, index.cell_counts, target, per_shape_cap))
    for d in candidates:
        if d not in keep:
            verdicts[d]["gates"].append("eval_quota")


def importance_weights(
    verdicts: Dict[str, dict],
    index: EvaluationIndex,
    *,
    floor: float,
    ceiling: float,
) -> Dict[str, float]:
    """Weight survivors so the weighted cell distribution matches evaluation."""
    survivors = [d for d, v in verdicts.items() if not v["gates"]]
    cell_counts = Counter(verdicts[d]["cell"] for d in survivors)
    total = len(survivors)
    weights = {}
    for digest in survivors:
        cell = verdicts[digest]["cell"]
        eval_share = index.cell_counts.get(cell, 0) / index.n_programs
        train_share = cell_counts[cell] / total if total else 0.0
        raw = (eval_share / train_share) if train_share else 0.0
        weights[digest] = round(min(ceiling, max(floor, raw)), 4) if raw else floor
    return weights


def write_rl(
    table: pa.Table,
    records: List[dict],
    keep_rows: List[int],
    curation_by_row: Dict[int, dict],
    output: Path,
) -> None:
    kept = []
    for row in keep_rows:
        record = json.loads(json.dumps(records[row]))
        extra = dict(record.get("extra_info") or {})
        extra["curation"] = json.dumps(curation_by_row[row], sort_keys=True)
        record["extra_info"] = extra
        kept.append(record)
    fields = [f for f in table.schema if f.name != "extra_info"]
    extra_type = pa.struct(
        list(table.schema.field("extra_info").type) + [pa.field("curation", pa.string())]
    )
    schema = pa.schema(fields + [pa.field("extra_info", extra_type)])
    # Preserve column order of the source table.
    schema = pa.schema([schema.field(name) for name in table.schema.names])
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        pq.write_table(pa.Table.from_pylist(kept, schema=schema), temporary)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dataset", choices=("rl", "sft"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--dedup-levels", default=",".join(DEFAULT_DEDUP_LEVELS))
    # Gradient gates default to RL values; for SFT the synthesizer judges
    # target quality itself, so only the structural gates apply by default.
    parser.add_argument("--min-traces", type=int, default=None, help="rl: 8, sft: 1")
    parser.add_argument("--min-relation", type=int, default=None, help="rl: 4, sft: 0")
    parser.add_argument("--min-relation-share", type=float, default=None, help="rl: 0.10, sft: 0.0")
    parser.add_argument("--per-shape-cap", type=int, default=None, help="rl: 8, sft: 32")
    parser.add_argument("--weight-floor", type=float, default=0.25)
    parser.add_argument("--weight-ceiling", type=float, default=4.0)
    parser.add_argument("--keep-duplicate-rows", action="store_true",
                        help="keep every row of a kept source (default: one row per source)")
    parser.add_argument("--match-eval", action="store_true",
                        help="select --target programs by evaluation-cell quota instead of a plain shape cap")
    parser.add_argument("--target", type=int, default=5000)
    parser.add_argument("--prefer-unseen", type=Path,
                        help="SFT json: its programs fill RL quotas last, so RL keeps unseen programs")
    args = parser.parse_args()

    defaults = {
        "rl": {"min_traces": 8, "min_relation": 4, "min_relation_share": 0.10, "per_shape_cap": 8},
        "sft": {"min_traces": 1, "min_relation": 0, "min_relation_share": 0.0, "per_shape_cap": 32},
    }[args.dataset]
    for name, value in defaults.items():
        if getattr(args, name) is None:
            setattr(args, name, value)
    dedup_levels = tuple(level for level in args.dedup_levels.split(",") if level)
    unknown = set(dedup_levels) - set(DUPLICATE_LEVELS)
    if unknown:
        raise SystemExit(f"unknown dedup levels: {sorted(unknown)}")

    input_path = args.input or (
        ROOT / "traindata" / ("craft_rl_final.parquet" if args.dataset == "rl" else "craft_sft_final.json")
    )
    records, table, sources = _load_records(args.dataset, input_path)
    ledger = _latest_by_digest(args.ledger)
    index = EvaluationIndex.from_evaluation_dirs()

    rows_by_digest: Dict[str, List[int]] = defaultdict(list)
    source_by_digest: Dict[str, str] = {}
    for row, source in enumerate(sources):
        digest = _digest(source)
        rows_by_digest[digest].append(row)
        source_by_digest.setdefault(digest, source)

    verdicts: Dict[str, dict] = {}
    for count, (digest, source) in enumerate(source_by_digest.items(), 1):
        verdicts[digest] = assess_program(
            source,
            ledger.get(digest),
            index,
            dedup_levels=dedup_levels,
            min_traces=args.min_traces,
            min_relation=args.min_relation,
            min_relation_share=args.min_relation_share,
        )
        if count % 5000 == 0:
            print(f"assessed {count}/{len(source_by_digest)} unique programs", flush=True)
    seen_in_sft = set()
    if args.prefer_unseen:
        seen_in_sft = {
            _digest(_source_from_sft(r)) for r in json.loads(args.prefer_unseen.read_text(encoding="utf-8"))
        }
    if args.match_eval:
        apply_eval_quota(verdicts, index, args.target, args.per_shape_cap, seen=seen_in_sft)
    else:
        apply_shape_cap(verdicts, args.per_shape_cap)
    weights = importance_weights(
        verdicts, index, floor=args.weight_floor, ceiling=args.weight_ceiling
    )

    keep_rows: List[int] = []
    curation_by_row: Dict[int, dict] = {}
    dropped_duplicate_rows = 0
    for digest, rows in rows_by_digest.items():
        verdict = verdicts[digest]
        if verdict["gates"]:
            continue
        chosen = rows if args.keep_duplicate_rows else rows[:1]
        dropped_duplicate_rows += len(rows) - len(chosen)
        payload = {
            "schema_version": CURATION_SCHEMA_VERSION,
            "negative_schema_version": NEGATIVE_SCHEMA_VERSION,
            "weight": weights[digest],
            "cell": verdict["cell"],
            "relatedness": verdict["relatedness"],
            "related_level": verdict["related_level"],
            "stratum_guess": verdict["stratum_guess"],
            "n_negative_traces": verdict["n_negative_traces"],
            "relation": verdict["relation"],
            "tags": verdict["tags"],
        }
        for row in chosen:
            keep_rows.append(row)
            curation_by_row[row] = payload
    keep_rows.sort()

    # ---- report -----------------------------------------------------------
    gate_counts = Counter()
    first_gate = Counter()
    for verdict in verdicts.values():
        for gate in verdict["gates"]:
            gate_counts[gate] += 1
        first_gate[verdict["gates"][0] if verdict["gates"] else "kept"] += 1
    survivors = [d for d, v in verdicts.items() if not v["gates"]]
    shapes_before = Counter(v["shape"] for v in verdicts.values())
    shapes_after = Counter(verdicts[d]["shape"] for d in survivors)
    # Size the pool would have under other caps, after every other gate.
    gated_shapes = Counter(
        v["shape"] for v in verdicts.values()
        if not [g for g in v["gates"] if g != "shape_cap"]
    )
    cap_curve = {
        str(cap): sum(min(count, cap) for count in gated_shapes.values())
        for cap in (1, 2, 4, 8, 16, 32, 64)
    }
    copy_levels = Counter(level for v in verdicts.values() for level in v["copy_levels"])
    eval_cells_covered = {verdicts[d]["cell"] for d in survivors}
    pool_cells = Counter(v["cell"] for v in verdicts.values())
    kept_cells = Counter(verdicts[d]["cell"] for d in survivors)
    gaps = [
        {"cell": cell, "eval_programs": count}
        for cell, count in index.cell_counts.most_common()
        if cell not in eval_cells_covered
    ]
    related_levels = Counter(str(v["related_level"]) for d, v in verdicts.items() if d in set(survivors))
    stratum_guess = Counter(verdicts[d]["stratum_guess"] for d in survivors)
    tags = Counter(tag for d in survivors for tag in verdicts[d]["tags"])
    weight_values = sorted(weights.values())

    def quantile(values, p):
        return values[min(len(values) - 1, int(p * len(values)))] if values else None

    report = {
        "schema_version": CURATION_SCHEMA_VERSION,
        "negative_schema_version": NEGATIVE_SCHEMA_VERSION,
        "dataset": args.dataset,
        "input": _display_path(input_path),
        "output": _display_path(args.output),
        "ledger": _display_path(args.ledger),
        "parameters": {
            "match_eval": args.match_eval,
            "target": args.target if args.match_eval else None,
            "dedup_levels": list(dedup_levels),
            "min_traces": args.min_traces,
            "min_relation": args.min_relation,
            "min_relation_share": args.min_relation_share,
            "per_shape_cap": args.per_shape_cap,
            "weight_floor": args.weight_floor,
            "weight_ceiling": args.weight_ceiling,
        },
        "input_rows": len(records),
        "unique_programs": len(verdicts),
        "kept_programs": len(survivors),
        "output_rows": len(keep_rows),
        "dropped_duplicate_rows": dropped_duplicate_rows,
        "gate_failures": dict(gate_counts),
        "first_failing_gate": dict(first_gate),
        "copy_levels_observed": dict(copy_levels),
        "loop_shapes": {
            "distinct_before": len(shapes_before),
            "distinct_after": len(shapes_after),
            "programs_by_cap_after_other_gates": cap_curve,
            "top_before": [
                {"programs": count, "kept": shapes_after.get(shape, 0)}
                for shape, count in shapes_before.most_common(10)
            ],
        },
        "relatedness_of_kept": dict(related_levels),
        "stratum_guess_of_kept": dict(stratum_guess),
        "eval_cell_distribution": {
            "tv_distance_pool": tv_distance(pool_cells, index.cell_counts),
            "tv_distance_kept": tv_distance(kept_cells, index.cell_counts),
            "eval_programs_in_pool_cells": sum(c for cell, c in index.cell_counts.items() if pool_cells.get(cell)),
            "eval_programs_in_kept_cells": sum(c for cell, c in index.cell_counts.items() if kept_cells.get(cell)),
        },
        "evaluation_cells": {
            "total": len(index.cell_counts),
            "covered_by_kept": len(eval_cells_covered & set(index.cell_counts)),
            "uncovered": gaps,
            "uncovered_eval_programs": sum(g["eval_programs"] for g in gaps),
        },
        "soft_tags_of_kept": dict(tags),
        "overlap_with_prefer_unseen": {
            "kept_also_in_sft": sum(1 for d in survivors if d in seen_in_sft),
            "kept_unseen_by_sft": sum(1 for d in survivors if d not in seen_in_sft),
        } if args.prefer_unseen else None,
        "weights": {
            "min": quantile(weight_values, 0.0),
            "p25": quantile(weight_values, 0.25),
            "median": quantile(weight_values, 0.5),
            "p75": quantile(weight_values, 0.75),
            "max": quantile(weight_values, 1.0),
        },
        "policy": (
            "drop near-copies of evaluation programs; drop programs whose "
            "negative set cannot carry a gradient (unscorable, < min_traces "
            "traces, < min_relation relation traces, relation share < "
            "min_relation_share); drop programs structurally unrelated to the "
            "evaluation corpus; cap programs per loop shape; weight survivors "
            "toward the evaluation cell distribution"
        ),
    }

    if args.dataset == "sft":
        kept_records = [records[row] for row in keep_rows]
        _atomic_json(kept_records, args.output)
    else:
        write_rl(table, records, keep_rows, curation_by_row, args.output)
    _atomic_json(report, args.report)
    print(json.dumps({k: report[k] for k in (
        "input_rows", "unique_programs", "kept_programs", "output_rows",
        "gate_failures", "first_failing_gate",
    )}, indent=2))


if __name__ == "__main__":
    main()
