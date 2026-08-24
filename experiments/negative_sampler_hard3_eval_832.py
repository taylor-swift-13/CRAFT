"""Evaluate the hard relation/escape/range sampler on the frozen Full-832 pool.

The model candidates, target-hidden Houdini survivors, and final target-bearing
Frama-C/WP labels are unchanged.  Only the synthetic negative traces differ.
The baseline is the previous sampler with frame removed from its score.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import gzip
import json
import math
from pathlib import Path
import statistics

from experiments.negative_sampler_optimize_832 import (
    direct_score_records,
    evaluate_task,
    load_candidate_rows,
    load_manifest,
    payload_path,
)
from experiments.negative_sampler_quality_832 import (
    auc,
    pair_counts,
    pair_auc,
    quantile,
    selection,
)


REPO = Path(__file__).resolve().parents[1]
DEFAULT_NEW_ROOT = REPO / "results" / "negative_sampler_hard3v4_832"
DEFAULT_OLD_ROOT = REPO / "results" / "negative_sampler_current832"
DEFAULT_OUTPUT = REPO / "results" / "negative_sampler_hard3v4_832.json"
DEFAULT_REPORT = REPO / "results" / "negative_sampler_hard3v4_832.md"
EXPECTED_CANDIDATES = 6190


def task_key(row: dict) -> tuple[str, str]:
    return str(row["suite"]), str(row["case_id"])


def load_new_rows(root: Path) -> list[dict]:
    rows = []
    for line in (root / "candidate_scores.jsonl").read_text().splitlines():
        source = json.loads(line)
        if source.get("score_error") is not None:
            raise RuntimeError(
                f"new sampler score failed for {task_key(source)}: "
                f"{source['score_error']}"
            )
        score = source.get("current_negative_score")
        if score is None:
            continue
        rows.append({
            "task": task_key(source),
            "suite": str(source["suite"]),
            "case_id": str(source["case_id"]),
            "method": source["method"],
            "verified": bool(source["verified"]),
            "score": float(score),
        })
    return rows


def load_old_core_rows(root: Path) -> list[dict]:
    candidates = load_candidate_rows(root)
    manifest = load_manifest(root)
    records = []
    for key in sorted(manifest):
        with gzip.open(payload_path(root, manifest[key]), "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        # The archived current score used the pre-fix predicate evaluator and
        # cannot be an equality oracle once C identifiers such as ``in`` are
        # evaluated correctly. Recompute both sides from the saved survivors.
        source_rows = [
            {**row, "current_negative_score": None}
            for row in candidates[key]
        ]
        records.extend(evaluate_task(payload, source_rows))
    return direct_score_records(records, "core_raw_score")


def distribution(values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "min": min(values),
        "q05": quantile(values, 0.05),
        "q10": quantile(values, 0.10),
        "q25": quantile(values, 0.25),
        "median": quantile(values, 0.50),
        "mean": statistics.fmean(values),
        "q75": quantile(values, 0.75),
        "max": max(values),
        "at_least_0_8": sum(value >= 0.8 for value in values) / len(values),
        "at_least_0_9": sum(value >= 0.9 for value in values) / len(values),
    }


def metrics(rows: list[dict]) -> dict:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["task"]].append(row)
    groups = list(grouped.values())
    higher, tied, lower = pair_counts(groups)
    pair_total = higher + tied + lower
    mixed = [
        group for group in groups
        if any(row["verified"] for row in group)
        and not all(row["verified"] for row in group)
    ]
    strict_task_inversions = 0
    tied_or_inverted_tasks = 0
    for group in mixed:
        passed = [row["score"] for row in group if row["verified"]]
        failed = [row["score"] for row in group if not row["verified"]]
        strict_task_inversions += max(failed) > min(passed)
        tied_or_inverted_tasks += max(failed) >= min(passed)
    choose = selection(groups)
    verified = [row["score"] for row in rows if row["verified"]]
    failed = [row["score"] for row in rows if not row["verified"]]
    full = [row for row in rows if math.isclose(row["score"], 1.0)]
    return {
        "tasks": len(groups),
        "candidates": len(rows),
        "mixed_label_tasks": len(mixed),
        "pooled_auc": auc(rows),
        "within_task_auc": pair_auc(groups),
        "top1": choose["expected_top1_random_tie_break"],
        "random": choose["random_candidate_baseline"],
        "top1_lift": (
            choose["expected_top1_random_tie_break"]
            - choose["random_candidate_baseline"]
        ),
        "pair_order": {
            "pass_higher": higher,
            "tie": tied,
            "fail_higher": lower,
            "pairs": pair_total,
            "strict_inversion_rate": lower / pair_total if pair_total else None,
            "tie_or_inversion_rate": (
                (lower + tied) / pair_total if pair_total else None
            ),
        },
        "task_order": {
            "strictly_inverted_tasks": strict_task_inversions,
            "tied_or_inverted_tasks": tied_or_inverted_tasks,
            "strict_inversion_rate": (
                strict_task_inversions / len(mixed) if mixed else None
            ),
            "tie_or_inversion_rate": (
                tied_or_inverted_tasks / len(mixed) if mixed else None
            ),
        },
        "verified_score_distribution": distribution(verified),
        "failed_score_distribution": distribution(failed),
        "full_score_candidates": len(full),
        "full_score_precision": (
            sum(row["verified"] for row in full) / len(full) if full else None
        ),
    }


def common_task_rows(
    left: list[dict], right: list[dict]
) -> tuple[list[dict], list[dict]]:
    left_tasks = {row["task"] for row in left}
    right_tasks = {row["task"] for row in right}
    common = left_tasks & right_tasks
    return (
        [row for row in left if row["task"] in common],
        [row for row in right if row["task"] in common],
    )


def score_changes(old: list[dict], new: list[dict]) -> dict:
    def key(row: dict) -> tuple[str, str, str]:
        return row["task"] + (str(row["method"]),)

    old_by_key = {key(row): row for row in old}
    new_by_key = {key(row): row for row in new}
    common = sorted(set(old_by_key) & set(new_by_key))
    changes = [
        new_by_key[item]["score"] - old_by_key[item]["score"]
        for item in common
    ]
    return {
        "candidates": len(common),
        "mean_new_minus_old": statistics.fmean(changes) if changes else None,
        "increased": sum(change > 0 for change in changes),
        "unchanged": sum(change == 0 for change in changes),
        "decreased": sum(change < 0 for change in changes),
    }


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.2f}%"


def render(report: dict) -> str:
    old = report["common_tasks"]["old_core"]
    new = report["common_tasks"]["hard3"]
    new_all = report["all_scorable_hard3"]
    passed = new_all["verified_score_distribution"]
    return "\n".join([
        "# Full-832 hard relation / escape / range evaluation",
        "",
        "The frozen invariant candidates and final target-verification labels are unchanged.",
        "The old baseline already excludes frame from its score; the new sampler also removes",
        "guard/range-changing relation fallbacks and uses context-conditional envelopes.",
        "",
        "## Hard3 result",
        "",
        f"- Scorable tasks: {new_all['tasks']}; candidates: {new_all['candidates']}.",
        f"- Verified-score floor/min: {passed.get('min', 0):.4f}; q10: {passed.get('q10', 0):.4f}; median: {passed.get('median', 0):.4f}.",
        f"- Verified candidates with score >= 0.8: {pct(passed.get('at_least_0_8'))}; >= 0.9: {pct(passed.get('at_least_0_9'))}.",
        f"- Pair strict inversion rate: {pct(new_all['pair_order']['strict_inversion_rate'])}; tie-or-inversion: {pct(new_all['pair_order']['tie_or_inversion_rate'])}.",
        f"- Mixed-task worst-case strict inversion: {pct(new_all['task_order']['strict_inversion_rate'])}.",
        f"- Within-task AUC: {new_all['within_task_auc']:.4f}; top-1: {pct(new_all['top1'])}.",
        "",
        "## Paired common-task comparison",
        "",
        "| metric | old core | hard3 | delta |",
        "|---|---:|---:|---:|",
        f"| within-task AUC | {old['within_task_auc']:.4f} | {new['within_task_auc']:.4f} | {new['within_task_auc'] - old['within_task_auc']:+.4f} |",
        f"| top-1 | {pct(old['top1'])} | {pct(new['top1'])} | {pct(new['top1'] - old['top1'])} |",
        f"| pair strict inversion | {pct(old['pair_order']['strict_inversion_rate'])} | {pct(new['pair_order']['strict_inversion_rate'])} | {pct(new['pair_order']['strict_inversion_rate'] - old['pair_order']['strict_inversion_rate'])} |",
        f"| verified q10 | {old['verified_score_distribution']['q10']:.4f} | {new['verified_score_distribution']['q10']:.4f} | {new['verified_score_distribution']['q10'] - old['verified_score_distribution']['q10']:+.4f} |",
        "",
        "A finite target-hidden sampler cannot guarantee zero inversions; the inversion metrics",
        "measure that limitation directly instead of treating AUC as sufficient.",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new-root", type=Path, default=DEFAULT_NEW_ROOT)
    parser.add_argument("--old-root", type=Path, default=DEFAULT_OLD_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    new_rows = load_new_rows(args.new_root)
    old_rows = load_old_core_rows(args.old_root)
    if not new_rows or not old_rows:
        raise RuntimeError("both old and new Full-832 score sets must be present")
    old_common, new_common = common_task_rows(old_rows, new_rows)
    result = {
        "protocol": "full832_hard3v4_vs_old_core_v4",
        "frozen_candidate_rows": EXPECTED_CANDIDATES,
        "all_scorable_hard3": metrics(new_rows),
        "all_scorable_old_core": metrics(old_rows),
        "common_tasks": {
            "count": len({row["task"] for row in old_common}),
            "old_core": metrics(old_common),
            "hard3": metrics(new_common),
            "candidate_score_changes": score_changes(old_common, new_common),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.report.write_text(render(result), encoding="utf-8")
    print(args.output)
    print(args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
