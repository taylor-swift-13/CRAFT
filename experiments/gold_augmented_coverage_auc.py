"""Evaluate target-independent coverage with one verified gold per task.

The gold sets are used only as offline labels/candidates.  Coverage is still
computed from the target-hidden sample artifact.  Tasks whose target verifies
with a tautology are excluded because they admit no meaningful insufficient
invariant set; tasks without a currently verified gold are reported but not
silently treated as negatives.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

from experiments.current_sampler_rescore_832 import score_task
from experiments.gpt5nano_full832.common import REPO_ROOT, discover_tasks
from experiments.gpt5nano_full832.samples import load_sample_manifest
from rl_pipeline.common.state import dedup_normalized


DEFAULT_GOLD = REPO_ROOT / "results" / "gold_invariants_832" / "gold_ledger.jsonl"
DEFAULT_CANDIDATES = (
    REPO_ROOT / "results" / "negative_sampler_relation_escape_832" /
    "candidate_scores.jsonl"
)
DEFAULT_SAMPLES = REPO_ROOT / "results" / "negative_sampler_relation_escape_832"
DEFAULT_TRIVIAL = (
    REPO_ROOT / "results" / "gpt5nano_full832" /
    "trivial_invariant_full832.jsonl"
)
DEFAULT_OUTPUT = REPO_ROOT / "results" / "gold_invariants_832" / "coverage_auc.json"
DEFAULT_FIGURE = (
    REPO_ROOT / "paper" / "figures" / "negative_coverage_predictiveness.pdf"
)

SUITE_LABELS = {"linear": "Linear", "NLA_lipus": "NLA", "Loopy": "Loopy"}


def read_jsonl(path: Path) -> list[dict]:    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def metrics(rows: list[dict]) -> dict:
    labels = np.asarray([bool(row["verified"]) for row in rows], dtype=int)
    scores = np.asarray([float(row["current_negative_score"]) for row in rows])
    return {
        "candidates": len(rows),
        "positive_rate": float(labels.mean()),
        "auroc": float(roc_auc_score(labels, scores)),
        "auprc": float(average_precision_score(labels, scores)),
    }


def wilson_interval(successes: int, total: int) -> tuple[float, float]:
    if total == 0:
        return (0.0, 1.0)
    z = 1.959964
    rate = successes / total
    denom = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denom
    half = z * ((rate * (1 - rate) / total + z * z / (4 * total * total)) ** 0.5) / denom
    return (center - half, center + half)


def coverage_bands(rows: list[dict]) -> list[dict]:
    scores = np.asarray([float(row["current_negative_score"]) for row in rows])
    labels = np.asarray([bool(row["verified"]) for row in rows], dtype=int)
    definitions = [
        ("[0,.25]", scores <= 0.25),
        ("(.25,.50]", (scores > 0.25) & (scores <= 0.50)),
        ("(.50,.75]", (scores > 0.50) & (scores <= 0.75)),
        ("(.75,.90]", (scores > 0.75) & (scores <= 0.90)),
        ("(.90,1)", (scores > 0.90) & (scores < 1.0)),
        ("1", scores == 1.0),
    ]
    output = []
    for label, mask in definitions:
        total = int(mask.sum())
        successes = int(labels[mask].sum())
        output.append({
            "band": label,
            "rows": total,
            "success_rate": successes / total if total else 0.0,
            "wilson_ci95": list(wilson_interval(successes, total)),
        })
    return output


def render_figure(groups: dict, destination: Path) -> None:
    import sys

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from sklearn.metrics import roc_curve

    sys.path.insert(0, str(REPO_ROOT / "paper" / "figures"))
    from paper_style import (
        FAINT, GREEN, GREEN_TINT, INK, OCHRE, RUST, TEAL, use_paper_style,
    )

    use_paper_style()

    destination.parent.mkdir(parents=True, exist_ok=True)
    figure, axes = plt.subplots(1, 2, figsize=(7.15, 2.55))

    all_rows = [row for rows in groups.values() for row in rows]
    curves = [("All", groups)] + [
        (
            SUITE_LABELS[suite],
            {key: rows for key, rows in groups.items() if key[0] == suite},
        )
        for suite in ("linear", "NLA_lipus", "Loopy")
    ]
    fpr_grid = np.linspace(0.0, 1.0, 1001)
    colors = [GREEN, TEAL, OCHRE, RUST]
    for (label, task_rows), color in zip(curves, colors):
        interpolated_tprs = []
        task_aucs = []
        for rows in task_rows.values():
            labels = np.asarray([bool(row["verified"]) for row in rows], dtype=int)
            scores = np.asarray(
                [float(row["current_negative_score"]) for row in rows], dtype=float
            )
            false_positive, true_positive, _ = roc_curve(labels, scores)
            interpolated_tprs.append(np.interp(fpr_grid, false_positive, true_positive))
            task_aucs.append(roc_auc_score(labels, scores))
        mean_tpr = np.mean(interpolated_tprs, axis=0)
        mean_tpr[0] = 0.0
        mean_tpr[-1] = 1.0
        axes[0].plot(
            fpr_grid,
            mean_tpr,
            linewidth=1.6,
            color=color,
            label=f"{label} ({np.mean(task_aucs):.3f})",
        )
    axes[0].plot([0, 1], [0, 1], linestyle="--", color=INK, alpha=0.45, linewidth=0.9)
    axes[0].set(
        xlabel="False-positive rate",
        ylabel="True-positive rate",
        title="(a) Within-program prediction",
        xlim=(0, 1),
        ylim=(0, 1),
    )
    axes[0].legend(title="macro AUROC", frameon=False)

    bands = coverage_bands(all_rows)
    x = np.arange(len(bands))
    rates = np.asarray([band["success_rate"] for band in bands])
    lower = rates - np.asarray([band["wilson_ci95"][0] for band in bands])
    upper = np.asarray([band["wilson_ci95"][1] for band in bands]) - rates
    axes[1].bar(x, rates, color=GREEN_TINT, edgecolor=GREEN, linewidth=0.7)
    axes[1].errorbar(
        x,
        rates,
        yerr=np.vstack([lower, upper]),
        fmt="none",
        ecolor=INK,
        capsize=2.5,
        linewidth=0.9,
    )
    axes[1].set_xticks(x, [band["band"] for band in bands], rotation=30, ha="right")
    axes[1].set(
        xlabel="Negative-coverage band",
        ylabel="Target verification rate",
        title="(b) Verification by coverage",
        ylim=(0, 1),
    )

    for axis in axes:
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)
        axis.grid(axis="y", color=FAINT, linewidth=0.6, alpha=0.8)
        axis.set_axisbelow(True)
    figure.tight_layout()
    figure.savefig(destination, bbox_inches="tight")
    plt.close(figure)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES)
    parser.add_argument("--trivial", type=Path, default=DEFAULT_TRIVIAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output-figure", type=Path, default=DEFAULT_FIGURE)
    parser.add_argument("--bootstrap", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=31)
    args = parser.parse_args()

    tasks = {(task.suite, task.case_id): task for task in discover_tasks()}
    manifest = load_sample_manifest(args.samples)
    gold = {
        (row["suite"], str(row["case_id"])): row
        for row in read_jsonl(args.gold)
        if row.get("status") == "verified"
    }
    trivial = {
        (row["suite"], str(row["case_id"])): row
        for row in read_jsonl(args.trivial)
    }
    archived: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in read_jsonl(args.candidates):
        archived[(row["suite"], str(row["case_id"]))].append(row)

    groups: dict[tuple[str, str], list[dict]] = {}
    excluded = defaultdict(list)
    gold_scores = []
    for key, task in tasks.items():
        if key not in gold:
            excluded["no_verified_gold"].append(f"{key[0]}/{key[1]}")
            continue
        if trivial.get(key, {}).get("target_verified") is True:
            excluded["target_trivial"].append(f"{key[0]}/{key[1]}")
            continue
        if manifest[key]["negative_trace_count"] == 0:
            excluded["no_negative_groups"].append(f"{key[0]}/{key[1]}")
            continue

        gold_invariants = dedup_normalized(gold[key]["gold_invariants"])
        gold_row = score_task(task, manifest[key], [{
            "method": "verified_gold",
            "verified": True,
            "invariants": gold_invariants,
            "survivors": gold_invariants,
            "archived_negative_score": None,
            "archived_binary_fallback": None,
        }])[0]
        gold_scores.append(gold_row)

        rows = [
            dict(row)
            for row in archived.get(key, [])
            if row.get("current_negative_score") is not None
        ]
        fingerprints = {
            (tuple(dedup_normalized(row.get("survivors") or [])), bool(row["verified"]))
            for row in rows
        }
        gold_fingerprint = (tuple(gold_invariants), True)
        if gold_fingerprint not in fingerprints:
            rows.append(gold_row)
        if not any(not bool(row["verified"]) for row in rows):
            empty_row = score_task(task, manifest[key], [{
                "method": "empty_decoy",
                "verified": False,
                "invariants": [],
                "survivors": [],
                "archived_negative_score": None,
                "archived_binary_fallback": None,
            }])[0]
            rows.append(empty_row)
        if not any(bool(row["verified"]) for row in rows):
            raise RuntimeError(f"gold insertion failed for {key}")
        groups[key] = rows

    all_rows = [row for rows in groups.values() for row in rows]
    task_aucs = {
        key: roc_auc_score(
            [bool(row["verified"]) for row in rows],
            [float(row["current_negative_score"]) for row in rows],
        )
        for key, rows in groups.items()
    }
    pair_counts = {
        key: sum(bool(row["verified"]) for row in rows)
        * sum(not bool(row["verified"]) for row in rows)
        for key, rows in groups.items()
    }

    rng = np.random.default_rng(args.seed)
    keys = sorted(groups)
    bootstrap = []
    for _ in range(args.bootstrap):
        sampled = rng.choice(len(keys), size=len(keys), replace=True)
        bootstrap.append(float(np.mean([task_aucs[keys[index]] for index in sampled])))

    by_suite = {}
    for suite in ("linear", "NLA_lipus", "Loopy"):
        suite_keys = [key for key in keys if key[0] == suite]
        suite_rows = [row for key in suite_keys for row in groups[key]]
        by_suite[suite] = {
            "tasks": len(suite_keys),
            "macro_within_program_auroc": float(np.mean([task_aucs[key] for key in suite_keys])),
            "pair_weighted_within_program_auroc": float(np.average(
                [task_aucs[key] for key in suite_keys],
                weights=[pair_counts[key] for key in suite_keys],
            )),
            "pooled": metrics(suite_rows),
        }

    result = {
        "protocol": (
            "one current-judge-verified gold set per nontrivial task, original "
            "frozen candidates, and an empty decoy only when the originals "
            "contain no failure; all scores use target-hidden negative coverage"
        ),
        "verified_gold_tasks": len(gold),
        "evaluated_tasks": len(groups),
        "evaluated_candidates": len(all_rows),
        "excluded": dict(excluded),
        "gold_score_summary": {
            "mean": float(np.mean([row["current_negative_score"] for row in gold_scores])),
            "median": float(np.median([row["current_negative_score"] for row in gold_scores])),
        },
        "macro_within_program_auroc": float(np.mean(list(task_aucs.values()))),
        "macro_within_program_auroc_ci95": [
            float(value) for value in np.quantile(bootstrap, [0.025, 0.975])
        ],
        "pair_weighted_within_program_auroc": float(np.average(
            list(task_aucs.values()), weights=list(pair_counts.values())
        )),
        "pooled": metrics(all_rows),
        "by_suite": by_suite,
        "coverage_bands": coverage_bands(all_rows),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.output_figure is not None:
        render_figure(groups, args.output_figure)
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
