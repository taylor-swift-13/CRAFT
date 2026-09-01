#!/usr/bin/env python3
"""Audit whether target-independent negative coverage predicts target success.

The input is the saved candidate ledger produced by the test-set sampler
rescore. Every row contains a candidate invariant set scored without the
target and its final target-bearing Frama-C/WP verdict. The script reports
pooled and within-program ranking metrics, uses a program-clustered bootstrap
for the primary confidence intervals, and renders a diagnostic figure.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import rankdata
from sklearn.metrics import average_precision_score, roc_auc_score, roc_curve


REPO = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = (
    REPO
    / "results"
    / "negative_sampler_relation_escape_832"
    / "candidate_scores.jsonl"
)
DEFAULT_JSON = (
    REPO / "paper" / "artifacts" / "v4" / "negative_coverage_predictiveness.json"
)
DEFAULT_FIGURE = (
    REPO / "paper" / "figures" / "negative_coverage_predictiveness.pdf"
)
SUITE_LABELS = {
    "linear": "Linear",
    "NLA_lipus": "NLA",
    "Loopy": "Loopy",
}


def interval(values: list[float]) -> list[float]:
    return [float(x) for x in np.quantile(values, [0.025, 0.975])]


def wilson(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total == 0:
        return (float("nan"), float("nan"))
    p = successes / total
    denominator = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denominator
    radius = z * np.sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total))
    radius /= denominator
    return (float(center - radius), float(center + radius))


def load_rows(path: Path) -> tuple[list[dict], list[dict]]:
    all_rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not all(row.get("score_error") is None for row in all_rows):
        raise RuntimeError("candidate ledger contains scoring errors")
    scoreable = [
        row for row in all_rows if row.get("current_negative_score") is not None
    ]
    return all_rows, scoreable


def ranking_metrics(rows: list[dict]) -> dict:
    labels = np.asarray([bool(row["verified"]) for row in rows], dtype=int)
    scores = np.asarray(
        [float(row["current_negative_score"]) for row in rows], dtype=float
    )
    output = {
        "rows": len(rows),
        "positives": int(labels.sum()),
        "positive_rate": float(labels.mean()),
    }
    if labels.min() != labels.max():
        output.update({
            "auroc": float(roc_auc_score(labels, scores)),
            "auprc": float(average_precision_score(labels, scores)),
        })
    return output


def task_groups(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row["suite"], str(row["case_id"]))].append(row)
    return grouped


def within_task_auc(grouped: dict[tuple[str, str], list[dict]]) -> dict:
    values = {}
    pair_counts = {}
    for key, rows in grouped.items():
        labels = np.asarray([bool(row["verified"]) for row in rows], dtype=int)
        if labels.min() == labels.max():
            continue
        scores = np.asarray(
            [float(row["current_negative_score"]) for row in rows], dtype=float
        )
        values[key] = float(roc_auc_score(labels, scores))
        pair_counts[key] = int(labels.sum() * (len(labels) - labels.sum()))
    return {
        "values": values,
        "pair_counts": pair_counts,
        "tasks": len(values),
        "macro_auroc": float(np.mean(list(values.values()))),
        "pair_weighted_auroc": float(
            np.average(
                list(values.values()),
                weights=[pair_counts[key] for key in values],
            )
        ),
    }


def clustered_bootstrap(
    rows: list[dict],
    grouped: dict[tuple[str, str], list[dict]],
    within_values: dict[tuple[str, str], float],
    *,
    replicates: int,
    seed: int,
) -> dict:
    keys = sorted(grouped)
    key_to_index = {key: index for index, key in enumerate(keys)}
    row_task = np.asarray(
        [key_to_index[(row["suite"], str(row["case_id"]))] for row in rows],
        dtype=int,
    )
    labels = np.asarray([bool(row["verified"]) for row in rows], dtype=int)
    scores = np.asarray(
        [float(row["current_negative_score"]) for row in rows], dtype=float
    )
    strata = {
        suite: np.asarray(
            [key_to_index[key] for key in keys if key[0] == suite], dtype=int
        )
        for suite in sorted({key[0] for key in keys})
    }
    within_array = np.asarray(
        [within_values.get(key, np.nan) for key in keys], dtype=float
    )

    rng = np.random.default_rng(seed)
    auc_values: list[float] = []
    ap_values: list[float] = []
    within_auc_values: list[float] = []
    for _ in range(replicates):
        counts = np.zeros(len(keys), dtype=int)
        for indices in strata.values():
            sampled = rng.choice(indices, size=len(indices), replace=True)
            counts += np.bincount(sampled, minlength=len(keys))
        weights = counts[row_task]
        auc_values.append(float(roc_auc_score(labels, scores, sample_weight=weights)))
        ap_values.append(
            float(average_precision_score(labels, scores, sample_weight=weights))
        )
        valid = (~np.isnan(within_array)) & (counts > 0)
        within_auc_values.append(
            float(np.average(within_array[valid], weights=counts[valid]))
        )

    return {
        "replicates": replicates,
        "seed": seed,
        "scheme": (
            "stratified program-cluster bootstrap; all candidate rows for a "
            "sampled program move together"
        ),
        "pooled_auroc_ci95": interval(auc_values),
        "pooled_auprc_ci95": interval(ap_values),
        "within_program_macro_auroc_ci95": interval(within_auc_values),
    }


def within_task_permutation(
    grouped: dict[tuple[str, str], list[dict]],
    observed: float,
    *,
    replicates: int,
    seed: int,
) -> dict:
    """One-sided randomization test, permuting verdicts within each program."""
    rng = np.random.default_rng(seed)
    null_sum = np.zeros(replicates, dtype=float)
    informative = 0
    for rows in grouped.values():
        labels = np.asarray([bool(row["verified"]) for row in rows], dtype=int)
        positives = int(labels.sum())
        negatives = len(labels) - positives
        if positives == 0 or negatives == 0:
            continue
        scores = np.asarray(
            [float(row["current_negative_score"]) for row in rows], dtype=float
        )
        ranks = rankdata(scores, method="average")
        random_order = rng.random((replicates, len(rows)))
        selected = np.argpartition(
            random_order, kth=positives - 1, axis=1
        )[:, :positives]
        rank_sums = ranks[selected].sum(axis=1)
        null_sum += (
            rank_sums - positives * (positives + 1) / 2.0
        ) / (positives * negatives)
        informative += 1
    null_values = null_sum / informative
    exceedances = int(np.count_nonzero(null_values >= observed))
    return {
        "replicates": replicates,
        "seed": seed,
        "scheme": "target verdicts permuted within each informative program",
        "informative_programs": informative,
        "null_mean": float(null_values.mean()),
        "one_sided_p": float((exceedances + 1) / (replicates + 1)),
        "exceedances": exceedances,
    }


def coverage_bands(rows: list[dict]) -> list[dict]:
    scores = np.asarray(
        [float(row["current_negative_score"]) for row in rows], dtype=float
    )
    labels = np.asarray([bool(row["verified"]) for row in rows], dtype=int)
    definitions = [
        ("0", scores == 0.0),
        ("(0,.25]", (scores > 0.0) & (scores <= 0.25)),
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
            "successes": successes,
            "success_rate": successes / total,
            "wilson_ci95": list(wilson(successes, total)),
        })
    return output


def render_figure(rows: list[dict], bands: list[dict], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure, axes = plt.subplots(1, 2, figsize=(9.2, 3.45))

    import sys

    sys.path.insert(0, str(REPO / "paper" / "figures"))
    from paper_style import FAINT, GREEN, GREEN_TINT, INK, MUTED, OCHRE, RUST, TEAL

    curves = [("All", rows)] + [
        (SUITE_LABELS[suite], [row for row in rows if row["suite"] == suite])
        for suite in ("linear", "NLA_lipus", "Loopy")
    ]
    colors = [GREEN, TEAL, OCHRE, RUST]
    for (label, subset), color in zip(curves, colors):
        labels = np.asarray([bool(row["verified"]) for row in subset], dtype=int)
        scores = np.asarray(
            [float(row["current_negative_score"]) for row in subset], dtype=float
        )
        false_positive, true_positive, _ = roc_curve(labels, scores)
        auc = roc_auc_score(labels, scores)
        axes[0].plot(
            false_positive,
            true_positive,
            linewidth=1.8,
            color=color,
            label=f"{label} ({auc:.3f})",
        )
    axes[0].plot([0, 1], [0, 1], linestyle="--", color=MUTED, linewidth=1)
    axes[0].set(
        xlabel="False-positive rate",
        ylabel="True-positive rate",
        title="(a) Hidden-target prediction",
        xlim=(0, 1),
        ylim=(0, 1),
    )
    axes[0].legend(title="AUROC", frameon=False, fontsize=8, title_fontsize=8)

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-figure", type=Path, default=DEFAULT_FIGURE)
    parser.add_argument("--bootstrap", type=int, default=5000)
    parser.add_argument("--permutations", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    all_rows, rows = load_rows(args.input)
    grouped = task_groups(rows)
    pooled = ranking_metrics(rows)
    within = within_task_auc(grouped)
    bootstrap = clustered_bootstrap(
        rows,
        grouped,
        within["values"],
        replicates=args.bootstrap,
        seed=args.seed,
    )
    permutation = within_task_permutation(
        grouped,
        within["macro_auroc"],
        replicates=args.permutations,
        seed=args.seed + 1,
    )
    by_suite = {
        SUITE_LABELS[suite]: ranking_metrics(
            [row for row in rows if row["suite"] == suite]
        )
        for suite in ("linear", "NLA_lipus", "Loopy")
    }
    by_method = {
        method: ranking_metrics([row for row in rows if row["method"] == method])
        for method in sorted({row["method"] for row in rows})
    }
    bands = coverage_bands(rows)
    high = [
        row for row in rows if float(row["current_negative_score"]) >= 0.90
    ]
    low = [
        row for row in rows if float(row["current_negative_score"]) < 0.50
    ]

    result = {
        "schema_version": 1,
        "input": str(args.input.relative_to(REPO)),
        "protocol": (
            "target-independent relation/post-exit negative coverage predicts "
            "restored-target Frama-C/WP verification"
        ),
        "candidate_rows": len(all_rows),
        "scoreable_candidate_rows": len(rows),
        "scoreable_programs": len(grouped),
        "excluded_zero_negative_rows": len(all_rows) - len(rows),
        "excluded_zero_negative_programs": 832 - len(grouped),
        "exclusion_reason": (
            "no retained negative groups; these rows use a binary fallback and "
            "have no continuous coverage score"
        ),
        "pooled": pooled,
        "within_program": {
            key: value
            for key, value in within.items()
            if key not in {"values", "pair_counts"}
        },
        "cluster_bootstrap": bootstrap,
        "within_program_permutation": permutation,
        "by_suite": by_suite,
        "by_method": by_method,
        "coverage_bands": bands,
        "threshold_diagnostics": {
            "coverage_ge_0.90": ranking_metrics(high),
            "coverage_lt_0.50": ranking_metrics(low),
        },
        "interpretation": (
            "AUC is an associational diagnostic, not a causal estimate; the "
            "within-program analysis controls task-level difficulty by ranking "
            "candidates only against other candidates for the same program."
        ),
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    render_figure(rows, bands, args.output_figure)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
