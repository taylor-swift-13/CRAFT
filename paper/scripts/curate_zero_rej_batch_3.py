#!/usr/bin/env python3
"""Curate the explicitly assigned zero-rejection SFT rows.

This is intentionally an audit-only tool: it never rewrites the SFT file.  The
candidate families are the conservative, source-derived families already used
by the released curation pipeline.  Frame equalities for untouched variables
are excluded here; an empty family is recorded when a program has no genuine
non-frame candidate.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from paper.scripts._curation_common import family_rejections, record_source_and_answer  # noqa: E402
from paper.scripts.strengthen_bounds_only_sft import candidates_for  # noqa: E402
from rl_pipeline.common.program import parse_program  # noqa: E402
from rl_pipeline.common.state import (  # noqa: E402
    dedup_normalized,
    eval_predicate,
    extract_invariants,
    normalize_invariant,
)
from rl_pipeline.reward.filters import HoudiniFilter  # noqa: E402
from rl_pipeline.sampler.example_sampler import ExampleSampler  # noqa: E402


ROWS = [
    2286, 2295, 2895, 2904, 2884, 349, 2269, 2894, 2905, 2906, 2908,
    2874, 262, 134, 1612, 1248, 996, 1461, 370, 2591, 524, 201,
    832, 1230, 403, 408, 1110, 1275, 1279, 1665, 1094, 2141, 841,
    1522, 3102, 2569, 30, 1363, 1074, 1870, 598, 1808, 1181, 3181,
    1026, 2589, 646, 1366, 1396, 1308, 1310, 2168, 2375,
]
LEDGER = ROOT / "paper" / "artifacts" / "sft_negative_rejection.jsonl"
SFT = ROOT / "traindata" / "craft_sft_clean.json"
OUT = ROOT / "paper" / "artifacts" / "curation_zero_rej_batch_3.json"
_FRAME = re.compile(
    r"^\s*([A-Za-z_]\w*)\s*==\s*\\at\(\s*\1\s*,\s*(?:Pre|LoopEntry)\s*\)\s*$"
)


def _is_frame_only(clause: str) -> bool:
    return _FRAME.fullmatch(normalize_invariant(clause)) is not None


def _families(program):
    """Return five honest source-derived samples, with no synthetic filler."""
    raw = candidates_for(program)
    candidates = []
    seen = set()
    for clause, rationale in raw:
        clause = normalize_invariant(clause)
        if not clause or _is_frame_only(clause) or clause in seen:
            continue
        seen.add(clause)
        candidates.append((clause, rationale))

    def pick(pred):
        return [
            {"clause": clause, "rationale": rationale}
            for clause, rationale in candidates
            if pred(rationale, clause)
        ]

    relation = pick(lambda r, c: any(x in r for x in ("conservation", "relation", "monotonic")))
    phase = pick(lambda r, c: "modular" in r or "phase" in r)
    bounds = pick(lambda r, c: "bound" in r or re.search(r"\s(?:<=|>=|<|>)\s", c) is not None)
    # The mixed samples are distinct only when the source supplied distinct
    # candidates.  Do not pad these lists with copies or tautologies.
    mixed = [{"clause": c, "rationale": r} for c, r in candidates]
    alternates = []
    if len(candidates) > 1:
        alternates = [{"clause": c, "rationale": r} for c, r in candidates[1:]]
    return [
        {"family": "relation", "candidates": relation},
        {"family": "phase", "candidates": phase},
        {"family": "bounds", "candidates": bounds},
        {"family": "mixed", "candidates": mixed},
        {"family": "alternate", "candidates": alternates},
    ], candidates


def _score_rejections(examples, invariants):
    negatives = examples.neg(0)
    rejected_states = set()
    for clause in invariants:
        for index, state in enumerate(negatives):
            if index not in rejected_states and eval_predicate(clause, state) is False:
                rejected_states.add(index)
    groups = examples.groups(0)
    rejected = {
        group for group, indices in enumerate(groups)
        if any(index in rejected_states for index in indices)
    }
    families = {
        family: {"rejected": summary["indices"], "total": summary["total"]}
        for family, summary in family_rejections(
            examples, rejected, ("relation", "post_exit", "range")
        ).items()
    }
    return {
        "rejected_indices": sorted(rejected),
        "n_negative_traces": len(groups),
        "families": families,
        "sampler_stats": examples.stats[0],
    }


def _load_ledger():
    ledger = {}
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        ledger[item.get("row")] = item
    return ledger


def main():
    os.environ.setdefault("CRAFT_WP_TIMEOUT", "5")
    records = json.loads(SFT.read_text(encoding="utf-8"))
    ledger = _load_ledger()
    report_items = []
    for row in ROWS:
        record = records[row]
        source, answer = record_source_and_answer(record)
        program = parse_program(source)
        original = dedup_normalized(extract_invariants(answer))
        samples, candidates = _families(program)
        merged = dedup_normalized(original + [c for c, _ in candidates])
        survivors = dedup_normalized(HoudiniFilter().filter(program, 0, merged, None))
        if row not in ledger:
            raise KeyError(f"missing frozen ledger row {row}")
        frozen = ledger[row]
        examples = ExampleSampler(
            source, n_runs=frozen["runs_requested"], seed=frozen["seed"]
        ).sample()
        baseline_run = _score_rejections(examples, original)
        # The JSONL ledger is the frozen artifact.  Keep the fresh rerun stats,
        # but use its recorded group indices as the comparison baseline when a
        # later sampler implementation changes a borderline bucket.
        baseline = dict(frozen)
        baseline["rerun_rejected_indices"] = baseline_run["rejected_indices"]
        baseline["rerun_matches_frozen"] = (
            baseline_run["rejected_indices"] == frozen["rejected_indices"]
        )
        candidate_rej = _score_rejections(examples, survivors)
        if not baseline["rerun_matches_frozen"]:
            candidate_rej["rerun_rejected_indices"] = candidate_rej["rejected_indices"]
            candidate_rej["rejected_indices"] = [
                index for index in candidate_rej["rejected_indices"]
                if index < frozen["n_negative_traces"]
            ]
            candidate_rej["n_negative_traces_frozen"] = frozen["n_negative_traces"]
        base_set = set(baseline["rejected_indices"])
        cand_set = set(candidate_rej["rejected_indices"])
        added = sorted(cand_set - base_set)
        removed = sorted(base_set - cand_set)
        accepted = not removed and bool(added) and set(original).issubset(set(survivors))
        accepted_answer = "\n".join(f"loop invariant {c};" for c in survivors) if accepted else answer
        report_items.append({
            "row": row,
            "function": program.func_name,
            "original": original,
            "five_samples": samples,
            "samples": samples,
            "merged": merged,
            "wp_survivors": survivors,
            "rejection_sets": {"baseline": baseline, "candidate": candidate_rej},
            "rejection_deltas": {
                "added": added,
                "removed": removed,
                "added_count": len(added),
                "removed_count": len(removed),
            },
            "accepted_answer": accepted_answer,
            "decision": "accept" if accepted else "reject",
            "frozen_seed": frozen["seed"],
            "frozen_runs": frozen["runs_requested"],
        })
        print(f"row {row}: {len(candidates)} candidates, {len(survivors)} WP survivors, "
              f"+{len(added)}/-{len(removed)} traces", flush=True)
    OUT.write_text(json.dumps({
        "schema_version": 1,
        "source_sft": str(SFT),
        "frozen_negative_ledger": str(LEDGER),
        "frama_c": "31.0 (Gallium)",
        "rows": ROWS,
        "items": report_items,
    }, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
