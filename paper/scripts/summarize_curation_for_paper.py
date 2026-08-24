#!/usr/bin/env python3
"""Aggregate the v4 curation/synthesis reports into one JSON for the paper.

Every number quoted in the data sections of the paper should come from this
file (``paper/artifacts/v4/paper_data_summary.json``), never be typed by hand.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from paper.scripts.filter_training_by_negative_coverage import _atomic_json  # noqa: E402
from rl_pipeline.common.state import extract_invariants  # noqa: E402
from rl_pipeline.sampler.example_sampler import (  # noqa: E402
    NEGATIVE_SCHEMA_VERSION,
    _ESCAPE_GROUP_BUDGET,
    _NEGATIVE_GROUP_BUDGET,
    _RELATION_GROUP_BUDGET,
)

V4 = ROOT / "paper" / "artifacts" / "v4"


def _load(name: str) -> dict:
    path = V4 / name
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _sft_stats(path: Path) -> dict:
    if not path.is_file():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    counts = sorted(len(extract_invariants(next(t["value"] for t in r["conversations"] if t["from"] == "gpt"))) for r in rows)
    synthesized = sum(1 for r in rows if "synthesis" in r)
    return {
        "rows": len(rows),
        "synthesized_rows": synthesized,
        "archival_rows": len(rows) - synthesized,
        "clauses_per_answer": {
            "mean": round(sum(counts) / len(counts), 2) if counts else None,
            "median": counts[len(counts) // 2] if counts else None,
            "p90": counts[int(0.9 * len(counts))] if counts else None,
            "max": counts[-1] if counts else None,
        },
        "with_relational_clause": sum(1 for r in rows if r.get("synthesis", {}).get("has_transition_law")),
    }


def main() -> None:
    rl = _load("rl_curation_report.json")
    sft_sel = _load("sft_program_selection.json")
    summary = {
        "sampler": {
            "schema_version": NEGATIVE_SCHEMA_VERSION,
            "relation_budget": _RELATION_GROUP_BUDGET,
            "escape_budget": _ESCAPE_GROUP_BUDGET,
            "total_budget": _NEGATIVE_GROUP_BUDGET,
        },
        "canonicalization": {
            "rl": _load("rl_canonicalization.json").get("status"),
            "sft": _load("sft_canonicalization.json").get("status"),
        },
        "generated_programs": {
            "pass1": {k: _load("../../../results/gen_programs/generated_report.json").get(k) for k in
                      ("programs_accepted", "cells_filled", "eval_programs_newly_covered", "rejections")},
            "pass2": {k: _load("../../../results/gen_programs/generated2_report.json").get(k) for k in
                      ("programs_accepted", "cells_filled", "eval_programs_newly_covered", "rejections")},
        },
        "rl": {
            "input_rows": rl.get("input_rows"),
            "unique_programs": rl.get("unique_programs"),
            "output_rows": rl.get("output_rows"),
            "parameters": rl.get("parameters"),
            "first_failing_gate": rl.get("first_failing_gate"),
            "loop_shapes": rl.get("loop_shapes"),
            "eval_cell_distribution": rl.get("eval_cell_distribution"),
            "overlap_with_sft": rl.get("overlap_with_prefer_unseen"),
            "copy_levels_observed": rl.get("copy_levels_observed"),
        },
        "sft_selection": {k: sft_sel.get(k) for k in (
            "target", "from_sft", "dropped_existing_by_difficulty", "dropped_existing_by_quota",
            "from_pool", "total", "rejected", "tv_distance_before", "tv_distance_after",
            "eval_programs_in_covered_cells_before", "eval_programs_in_covered_cells_after")},
        "sft_synthesis": _load("sft_synthesis_report.json"),
        "sft_final": _sft_stats(ROOT / "traindata" / "craft_sft_train.json"),
        "sampler_discrimination_on_training": {k: v for k, v in _load("rl_sampler_discrimination.json").items()
                                               if k not in ("policy", "errors")},
    }
    _atomic_json(summary, V4 / "paper_data_summary.json")
    print(json.dumps(summary, indent=1)[:4000])


if __name__ == "__main__":
    main()
