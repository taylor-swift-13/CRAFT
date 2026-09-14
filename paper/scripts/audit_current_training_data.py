#!/usr/bin/env python3
"""Audit current RL/SFT sources and refresh both papers' distribution artifact.

Reads the canonical datasets without changing them. Uses the established
fingerprints, including break-idiom canonicalization, for instance matching
and structural cells. Historical v4 reports remain frozen.
"""
import hashlib
import json
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from paper.scripts.audit_train_test_overlap import load_evaluation_sources, corpus_sha256
from paper.scripts.program_fingerprint import fingerprint, tv_distance
from paper.scripts.filter_training_by_negative_coverage import _source_from_rl, _source_from_sft
from experiments.build_rl_pool_10k import family


def main():
    rl_path = ROOT / "traindata/craft_rl.parquet"
    sft_path = ROOT / "traindata/craft_sft.json"
    rl = [_source_from_rl(r) for r in pq.read_table(rl_path).to_pylist()]
    sft = [_source_from_sft(r) for r in json.loads(sft_path.read_text())]
    eval_paths, evaluation = load_evaluation_sources()
    fp = lru_cache(None)(fingerprint)
    datasets = {"evaluation": evaluation, "rl": rl, "sft": sft, "union": rl + sft}
    fps = {}
    for name, sources in datasets.items():
        fps[name] = [fp(s) for s in sources]
        print(f"Fingerprinting {name}: {len(sources)} records", flush=True)
    eval_cells = Counter(f.cell for f in fps["evaluation"])
    def marginals(values):
        features = [f.features for f in values]
        return {
            "guard_kind": dict(Counter(f["guard_kind"] for f in features)),
            "nonlinear": dict(Counter(str(f["nonlinear"]).lower() for f in features)),
            "nondeterministic": dict(Counter(str(f["nondet"]).lower() for f in features)),
            "variable_band": dict(Counter("v<=2" if f["n_pre_vars"] <= 2 else
                "v3-4" if f["n_pre_vars"] <= 4 else "v5+" for f in features)),
        }
    def stats(name):
        values = fps[name]
        cells = Counter(f.cell for f in values)
        shared = set(cells) & set(eval_cells)
        covered = sum(eval_cells[c] for c in shared)
        supported = sum(cells[c] for c in shared)
        matches = {}
        for level in ("exact", "alpha", "alpha_const"):
            train_keys = {getattr(f, level) for f in values}
            matches[level] = sum(getattr(f, level) in train_keys for f in fps["evaluation"])
        return {
            "records": len(values),
            "distinct_program_sources": len(set(datasets[name])),
            "evaluation_matches": matches,
            "train_structural_cells": len(cells),
            "shared_evaluation_cells": len(shared),
            "evaluation_programs_in_shared_cells": covered,
            "evaluation_programs_in_shared_cells_rate": covered / len(evaluation),
            "training_records_in_evaluation_supported_cells": supported,
            "training_records_in_evaluation_supported_cells_rate": supported / len(values),
            "tv_distance": tv_distance(cells, eval_cells),
            "marginals": marginals(values),
        }
    result = {
        "schema_version": 2,
        "inputs": {
            "rl": {"path": str(rl_path.relative_to(ROOT)), "sha256": hashlib.sha256(rl_path.read_bytes()).hexdigest()},
            "sft": {"path": str(sft_path.relative_to(ROOT)), "sha256": hashlib.sha256(sft_path.read_bytes()).hexdigest()},
            "evaluation_programs": len(evaluation),
            "evaluation_corpus_sha256": corpus_sha256(eval_paths),
        },
        "definition": {
            "instance_levels": ["exact target-hidden tokens", "alpha-renamed identifiers", "alpha-renamed identifiers with nontrivial integer constants abstracted"],
            "canonicalization": "program_fingerprint.fingerprint: target hiding and break-idiom canonicalization",
            "structural_cell": ["coarse control-flow profile", "guard kind", "nonlinearity", "nondeterminism", "variable-count band"],
            "distribution_weighting": "training records versus evaluation programs",
            "source_identity": "exact visible source string from each training prompt",
        },
        "audit_scope": {
            "note": "Instance matching does not establish semantic disjointness or evaluation-independent selection. Aggregate target-hidden evaluation complexity informed the RL selection policy.",
        },
        "evaluation": {"programs": len(evaluation), "structural_cells": len(eval_cells), "marginals": marginals(fps["evaluation"])},
        **{name: stats(name) for name in ("rl", "sft", "union")},
        "rl_sft_exact_source_overlap": len(set(rl) & set(sft)),
        "rl_distinct_loop_shapes": len({f.alpha_const_loop for f in fps["rl"]}),
        "rl_source_families_retaining_constants": len({family(s) for s in rl}),
        "rl_source_families_abstracting_constants": len({family(s, True) for s in rl}),
    }
    rendered = json.dumps(result, indent=2) + "\n"
    for version in ("paper", "paper_no_shapley"):
        (ROOT / version / "artifacts/train_eval_distribution_current.json").write_text(rendered)
    print(json.dumps({k:v for k,v in result.items() if k not in ("inputs", "definition", "audit_scope")}, indent=2))


if __name__ == "__main__":
    main()
