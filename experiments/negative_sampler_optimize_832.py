"""Evaluate target-hidden negative-score redesigns on the saved Full-832 pool.

This experiment reuses:

* the immutable current-sampler negative states for all 832 tasks;
* exact target-free Houdini survivors for each archived candidate; and
* the common target-bearing Frama-C/WP verdicts.

No model generation or target verification is rerun.  Candidate predicates are
only reevaluated on the saved negative states.  Family labels are reconstructed
from the sampler's archived per-family counts; the sampler emits groups in the
same order (relation, over-run, escape, random, frame).

Run:
    python3 -m experiments.negative_sampler_optimize_832
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
from typing import Iterable

from experiments.negative_sampler_quality_832 import (
    auc,
    pair_auc,
    quantile,
    selection,
)
from experiments.gpt5nano_full832.samples import _state_from_dict
from rl_pipeline.common.state import dedup_normalized, eval_predicate


REPO = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO / "results" / "negative_sampler_current832"
DEFAULT_OUTPUT = REPO / "results" / "negative_sampler_optimized832.json"
DEFAULT_REPORT = REPO / "results" / "negative_sampler_optimized832.md"
FAMILIES = ("relation", "overrun", "escape", "frame")
CORE_FAMILIES = ("relation", "overrun", "escape")
EXPECTED_TASKS = 832
EXPECTED_CANDIDATES = 6190


def task_key(row: dict) -> tuple[str, str]:
    return str(row["suite"]), str(row["case_id"])


def load_candidate_rows(root: Path) -> dict[tuple[str, str], list[dict]]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    path = root / "candidate_scores.jsonl"
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("score_error") is not None:
            raise RuntimeError(f"candidate score error: {task_key(row)}")
        grouped[task_key(row)].append(row)
    if sum(map(len, grouped.values())) != EXPECTED_CANDIDATES:
        raise RuntimeError("candidate ledger is not the expected 6190 rows")
    return grouped


def load_manifest(root: Path) -> dict[tuple[str, str], dict]:
    rows = [
        json.loads(line)
        for line in (root / "samples_manifest.jsonl").read_text().splitlines()
        if line.strip()
    ]
    if len(rows) != EXPECTED_TASKS:
        raise RuntimeError("sample manifest is not the expected 832 tasks")
    return {task_key(row): row for row in rows}


def payload_path(root: Path, manifest_row: dict) -> Path:
    path = Path(manifest_row["sample_artifact"])
    if path.is_file():
        return path
    suite, case_id = task_key(manifest_row)
    return root / "samples" / suite / f"{case_id}.json.gz"


def group_families(payload: dict) -> list[str]:
    stats = payload["stats"]
    counts = {
        "relation": int(stats.get("relation", 0)),
        "overrun": int(stats.get("bound_overrun", 0)),
        "escape": int(stats.get("bound_escape", 0)),
        "random": int(stats.get("random", 0)),
        "frame": int(stats.get("frame", 0)),
    }
    families: list[str] = []
    for family in ("relation", "overrun", "escape", "random", "frame"):
        families.extend([family] * counts[family])
    groups = payload["negative_trace_groups"]
    if len(families) != len(groups):
        raise RuntimeError(
            f"family/group mismatch for {payload['suite']}/{payload['case_id']}: "
            f"{len(families)} != {len(groups)}"
        )
    return families


def candidate_rejections(payload: dict, row: dict) -> list[int]:
    negatives = [_state_from_dict(item) for item in payload["negatives"]]
    groups = payload["negative_trace_groups"]
    rejected_states: set[int] = set()
    for invariant in dedup_normalized(row["survivors"]):
        for index, state in enumerate(negatives):
            if index not in rejected_states and (
                eval_predicate(invariant, state) is False
            ):
                rejected_states.add(index)
    rejected = [
        int(any(index in rejected_states for index in group))
        for group in groups
    ]
    archived = row.get("current_negative_score")
    if groups and archived is not None:
        reproduced = sum(rejected) / len(rejected)
        if not math.isclose(reproduced, archived, rel_tol=0.0, abs_tol=1e-15):
            raise RuntimeError(
                f"current score mismatch for {row['suite']}/{row['case_id']} "
                f"{row['method']}: {reproduced} != {archived}"
            )
    return rejected


def mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


def weighted_family_score(
    family_scores: dict[str, float | None],
    weights: dict[str, float],
) -> float | None:
    present = [
        family for family, weight in weights.items()
        if weight > 0 and family_scores.get(family) is not None
    ]
    denominator = sum(weights[family] for family in present)
    if not denominator:
        return None
    return sum(
        weights[family] * float(family_scores[family]) for family in present
    ) / denominator


def make_scored_row(source: dict, score: float) -> dict:
    return {
        "task": task_key(source),
        "suite": str(source["suite"]),
        "case_id": str(source["case_id"]),
        "method": source["method"],
        "verified": bool(source["verified"]),
        "score": float(score),
    }


def evaluate_task(payload: dict, source_rows: list[dict]) -> list[dict]:
    families = group_families(payload)
    rejections = [candidate_rejections(payload, row) for row in source_rows]
    family_indices = {
        family: [i for i, value in enumerate(families) if value == family]
        for family in FAMILIES
    }

    # A negative group is behaviorally redundant for this candidate pool when
    # it has the same reject/accept vector as another group.  Signature scores
    # give each distinct behavior one vote within a family.  The informative
    # version additionally removes all-zero/all-one signatures, which cannot
    # create relative reward inside this candidate set.
    signatures: dict[str, list[tuple[int, ...]]] = {}
    informative_signatures: dict[str, list[tuple[int, ...]]] = {}
    entropy_weights: dict[str, list[tuple[int, float]]] = {}
    for family, indices in family_indices.items():
        unique = sorted({tuple(row[index] for row in rejections) for index in indices})
        signatures[family] = unique
        informative_signatures[family] = [
            signature for signature in unique
            if 0 < sum(signature) < len(signature)
        ]
        weighted = []
        for index in indices:
            prevalence = sum(row[index] for row in rejections) / len(rejections)
            weight = 4.0 * prevalence * (1.0 - prevalence)
            if weight:
                weighted.append((index, weight))
        entropy_weights[family] = weighted

    output = []
    for candidate_index, source in enumerate(source_rows):
        reject = rejections[candidate_index]
        family_scores = {
            family: mean(reject[index] for index in indices)
            for family, indices in family_indices.items()
        }
        signature_scores = {
            family: mean(signature[candidate_index] for signature in values)
            for family, values in signatures.items()
        }
        informative_scores = {
            family: mean(signature[candidate_index] for signature in values)
            for family, values in informative_signatures.items()
        }
        entropy_scores: dict[str, float | None] = {}
        for family, values in entropy_weights.items():
            denominator = sum(weight for _, weight in values)
            entropy_scores[family] = (
                sum(reject[index] * weight for index, weight in values) / denominator
                if denominator else None
            )
        core_count = sum(
            len(family_indices[family]) for family in CORE_FAMILIES
        )
        core_rejected = sum(
            reject[index]
            for family in CORE_FAMILIES
            for index in family_indices[family]
        )
        output.append({
            "source": {
                "suite": source["suite"],
                "case_id": source["case_id"],
                "method": source["method"],
                "verified": source["verified"],
                "current_negative_score": source.get("current_negative_score"),
            },
            "current_raw_score": source.get("current_negative_score"),
            "core_raw_score": (
                core_rejected / core_count if core_count else None
            ),
            "family_scores": family_scores,
            "signature_scores": signature_scores,
            "informative_scores": informative_scores,
            "entropy_scores": entropy_scores,
            "family_counts": {
                family: len(indices) for family, indices in family_indices.items()
            },
        })
    return output


def metrics(rows: list[dict]) -> dict:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["task"]].append(row)
    groups = list(grouped.values())
    choose = selection(groups)
    full = [row for row in rows if math.isclose(row["score"], 1.0)]
    mixed = [
        group for group in groups
        if any(row["verified"] for row in group)
        and not all(row["verified"] for row in group)
    ]
    score_values = [row["score"] for row in rows]
    return {
        "candidates": len(rows),
        "tasks": len(groups),
        "mixed_label_tasks": len(mixed),
        "pooled_auc": auc(rows),
        "within_task_auc": pair_auc(groups),
        "top1": choose["expected_top1_random_tie_break"],
        "random": choose["random_candidate_baseline"],
        "top1_lift": (
            choose["expected_top1_random_tie_break"]
            - choose["random_candidate_baseline"]
        ),
        "mixed_top1": choose["mixed_expected_top1"],
        "mixed_random": choose["mixed_random_candidate"],
        "top_tie_tasks": choose["top_score_tie_tasks"],
        "mixed_top_tie_tasks": choose["mixed_label_top_tie_tasks"],
        "tasks_with_multiple_scores": sum(
            len({row["score"] for row in group}) > 1 for group in groups
        ),
        "full_score_candidates": len(full),
        "full_score_precision": (
            sum(row["verified"] for row in full) / len(full) if full else None
        ),
        "zero_score_candidates": sum(score == 0 for score in score_values),
        "intermediate_score_candidates": sum(
            0 < score < 1 for score in score_values
        ),
        "unique_score_values": len(set(score_values)),
    }


def task_cluster_bootstrap(
    left: list[dict],
    right: list[dict],
    repetitions: int,
    seed: int,
) -> dict:
    """Paired task bootstrap of right-minus-left on their common task set."""
    by_variant = []
    for rows in (left, right):
        grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for row in rows:
            grouped[row["task"]].append(row)
        by_variant.append(grouped)
    common = sorted(set(by_variant[0]) & set(by_variant[1]))
    pairs = [(by_variant[0][task], by_variant[1][task]) for task in common]
    keys = ("pooled_auc", "within_task_auc", "top1", "top1_lift")
    left_point = metrics([row for pair in pairs for row in pair[0]])
    right_point = metrics([row for pair in pairs for row in pair[1]])
    estimates = {key: right_point[key] - left_point[key] for key in keys}
    samples = {key: [] for key in keys}
    rng = random.Random(seed)
    for _ in range(repetitions):
        drawn = [rng.choice(pairs) for _ in pairs]
        left_metrics = metrics([row for pair in drawn for row in pair[0]])
        right_metrics = metrics([row for pair in drawn for row in pair[1]])
        for key in keys:
            samples[key].append(right_metrics[key] - left_metrics[key])
    return {
        "common_tasks": len(common),
        "differences_right_minus_left": {
            key: {
                "estimate": estimates[key],
                "task_cluster_bootstrap_95_ci": [
                    quantile(samples[key], 0.025),
                    quantile(samples[key], 0.975),
                ],
            }
            for key in keys
        },
    }


def fold_for(task: tuple[str, str], folds: int) -> int:
    digest = hashlib.sha256(f"{task[0]}/{task[1]}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % folds


def weight_grid(step: int = 10) -> list[dict[str, float]]:
    output = []
    # Keep relation as the primary signal and prevent any family from silently
    # becoming the whole objective during supervised weight search.
    for relation in range(40, 91, step):
        for overrun in range(0, 101 - relation, step):
            escape = 100 - relation - overrun
            output.append({
                "relation": relation / 100,
                "overrun": overrun / 100,
                "escape": escape / 100,
            })
    return output


def score_records(
    records: list[dict],
    score_field: str,
    weights: dict[str, float],
    allowed_tasks: set[tuple[str, str]] | None = None,
) -> list[dict]:
    rows = []
    for record in records:
        source = record["source"]
        if allowed_tasks is not None and task_key(source) not in allowed_tasks:
            continue
        score = weighted_family_score(record[score_field], weights)
        if score is not None:
            rows.append(make_scored_row(source, score))
    return rows


def direct_score_records(
    records: list[dict],
    score_field: str,
    allowed_tasks: set[tuple[str, str]] | None = None,
) -> list[dict]:
    rows = []
    for record in records:
        source = record["source"]
        if allowed_tasks is not None and task_key(source) not in allowed_tasks:
            continue
        score = record.get(score_field)
        if score is not None:
            rows.append(make_scored_row(source, float(score)))
    return rows


def cross_validated_weights(
    records: list[dict],
    score_field: str,
    folds: int,
) -> tuple[list[dict], list[dict]]:
    all_tasks = {task_key(record["source"]) for record in records}
    grid = weight_grid()
    heldout_rows = []
    choices = []
    for fold in range(folds):
        train_tasks = {task for task in all_tasks if fold_for(task, folds) != fold}
        test_tasks = all_tasks - train_tasks
        ranked = []
        for weights in grid:
            train_rows = score_records(records, score_field, weights, train_tasks)
            result = metrics(train_rows)
            ranked.append((
                result["within_task_auc"],
                result["top1"],
                -result["top_tie_tasks"],
                weights,
            ))
        _, _, _, best = max(ranked, key=lambda item: item[:3])
        fold_rows = score_records(records, score_field, best, test_tasks)
        heldout_rows.extend(fold_rows)
        choices.append({
            "fold": fold,
            "weights": best,
            "train_tasks": len(train_tasks),
            "heldout_tasks": len(test_tasks),
            "heldout_scorable_tasks": len({row["task"] for row in fold_rows}),
        })
    return heldout_rows, choices


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.2f}%"


def report_markdown(report: dict) -> str:
    current = report["variants"]["current_raw_pooled"]
    core = report["variants"]["core_raw_pooled"]
    paired = report["paired_bootstrap"]["core_raw_vs_current_raw"][
        "differences_right_minus_left"
    ]
    frame = report["variants"]["frame_only"]
    lines = [
        "# Full-832 负例评分优化实验",
        "",
        "所有方案复用相同负例、相同 Houdini survivors 和相同最终 Frama-C/WP 标签。",
        "family 权重搜索使用按任务划分的 5-fold out-of-fold 评估。",
        "",
        "## 结论",
        "",
        "推荐主分数只使用 relation、over-run、escape，frame 仅作为诊断。"
        f"可评分任务从 {current['tasks']} 降至 {core['tasks']}，但同任务 AUC 从 "
        f"{current['within_task_auc']:.4f} 提升至 {core['within_task_auc']:.4f}，"
        f"top-1 从 {pct(current['top1'])} 提升至 {pct(core['top1'])}。",
        "",
        "在两个方案共同可评分的任务上，去 frame 的同任务 AUC 提升为 "
        f"{paired['within_task_auc']['estimate']:.4f}，95% CI "
        f"[{paired['within_task_auc']['task_cluster_bootstrap_95_ci'][0]:.4f}, "
        f"{paired['within_task_auc']['task_cluster_bootstrap_95_ci'][1]:.4f}]；"
        "top-1 提升为 "
        f"{pct(paired['top1']['estimate'])}，95% CI "
        f"[{pct(paired['top1']['task_cluster_bootstrap_95_ci'][0])}, "
        f"{pct(paired['top1']['task_cluster_bootstrap_95_ci'][1])}]。",
        "",
        f"frame 单族同任务 AUC={frame['within_task_auc']:.4f}、"
        f"top-1 lift={pct(frame['top1_lift'])}，与最终目标质量呈反向关系。"
        "固定 family 权重和候选分歧/熵加权没有在共同任务上显示出稳健的额外收益，"
        "因此不加入默认算法。",
        "",
        "零主负例任务必须返回 `scorable=false` 并从 GRPO 中 mask，不能回退为"
        "二值满分；这项 anti-trivial 修复由独立 tautology 实验支持。",
        "",
        "## 全部方案",
        "",
        "| variant | tasks | candidates | pooled AUC | within-task AUC | top-1 | random | lift | top ties | full precision |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, values in report["variants"].items():
        lines.append(
            f"| {name} | {values['tasks']} | {values['candidates']} | "
            f"{values['pooled_auc']:.4f} | {values['within_task_auc']:.4f} | "
            f"{pct(values['top1'])} | {pct(values['random'])} | "
            f"{pct(values['top1_lift'])} | {values['top_tie_tasks']} | "
            f"{pct(values['full_score_precision'])} |"
        )
    lines += [
        "",
        "## Cross-validation family weights",
        "",
        "```json",
        json.dumps(report["cross_validation_choices"], indent=2, sort_keys=True),
        "```",
        "",
        "## Paired bootstrap",
        "",
        "比较均在两个方案共同可评分的任务上进行；区间为 right-minus-left。",
        "",
        "```json",
        json.dumps(report["paired_bootstrap"], indent=2, sort_keys=True),
        "```",
        "",
        "## Protocol boundary",
        "",
        "这是冻结候选池的离线重评分，不是重新训练模型。权重搜索虽为 out-of-fold，"
        "但 832 例此前已参与算法诊断，因此结果仍应在新留出的任务或完整多 seed 重训练中确认。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--bootstrap", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260823)
    args = parser.parse_args()

    candidates = load_candidate_rows(args.root)
    manifest = load_manifest(args.root)
    if set(candidates) != set(manifest):
        raise RuntimeError("candidate and sample task sets differ")

    records = []
    for index, key in enumerate(sorted(manifest), 1):
        path = payload_path(args.root, manifest[key])
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        records.extend(evaluate_task(payload, candidates[key]))
        if index % 25 == 0 or index == len(manifest):
            print(f"evaluated [{index}/{len(manifest)}]", flush=True)

    variants: dict[str, list[dict]] = {}
    variants["current_raw_pooled"] = direct_score_records(
        records, "current_raw_score"
    )
    variants["all_equal_family"] = score_records(
        records,
        "family_scores",
        {family: 1.0 for family in FAMILIES},
    )
    variants["core_raw_pooled"] = direct_score_records(
        records, "core_raw_score"
    )
    variants["core_equal_family"] = score_records(
        records,
        "family_scores",
        {family: 1.0 for family in CORE_FAMILIES},
    )
    for family in FAMILIES:
        variants[f"{family}_only"] = score_records(
            records, "family_scores", {family: 1.0}
        )
    variants["core_fixed_60_30_10"] = score_records(
        records,
        "family_scores",
        {"relation": 0.60, "overrun": 0.30, "escape": 0.10},
    )
    variants["core_fixed_60_10_30"] = score_records(
        records,
        "family_scores",
        {"relation": 0.60, "overrun": 0.10, "escape": 0.30},
    )
    variants["core_signature_60_30_10"] = score_records(
        records,
        "signature_scores",
        {"relation": 0.60, "overrun": 0.30, "escape": 0.10},
    )
    variants["core_informative_signature_60_30_10"] = score_records(
        records,
        "informative_scores",
        {"relation": 0.60, "overrun": 0.30, "escape": 0.10},
    )
    variants["core_entropy_60_30_10"] = score_records(
        records,
        "entropy_scores",
        {"relation": 0.60, "overrun": 0.30, "escape": 0.10},
    )

    cv_rows, cv_choices = cross_validated_weights(
        records, "family_scores", folds=5
    )
    variants["core_family_weight_cv_oof"] = cv_rows
    signature_cv_rows, signature_cv_choices = cross_validated_weights(
        records, "signature_scores", folds=5
    )
    variants["core_signature_weight_cv_oof"] = signature_cv_rows

    variant_metrics = {name: metrics(rows) for name, rows in variants.items()}
    paired = {
        "core_raw_vs_current_raw": task_cluster_bootstrap(
            variants["current_raw_pooled"],
            variants["core_raw_pooled"],
            args.bootstrap,
            args.seed,
        ),
        "fixed_60_10_30_vs_current_raw": task_cluster_bootstrap(
            variants["current_raw_pooled"],
            variants["core_fixed_60_10_30"],
            args.bootstrap,
            args.seed + 1,
        ),
        "signature_vs_fixed_60_30_10": task_cluster_bootstrap(
            variants["core_fixed_60_30_10"],
            variants["core_signature_60_30_10"],
            args.bootstrap,
            args.seed + 2,
        ),
        "cv_vs_current_raw": task_cluster_bootstrap(
            variants["current_raw_pooled"],
            variants["core_family_weight_cv_oof"],
            args.bootstrap,
            args.seed + 3,
        ),
        "entropy_vs_fixed_60_30_10": task_cluster_bootstrap(
            variants["core_fixed_60_30_10"],
            variants["core_entropy_60_30_10"],
            args.bootstrap,
            args.seed + 4,
        ),
    }
    output = {
        "protocol": "full832_same_pool_family_score_optimization_v1",
        "tasks": len(manifest),
        "candidate_rows": len(records),
        "anti_trivial_policy": {
            "core_scorable_tasks": variant_metrics["core_raw_pooled"]["tasks"],
            "unscorable_tasks": (
                len(manifest) - variant_metrics["core_raw_pooled"]["tasks"]
            ),
            "tautology_reward_on_scorable_tasks": 0.0,
            "tautology_reward_on_unscorable_tasks": 0.0,
            "tautology_full_reward_tasks": 0,
            "unscorable_action": "mask_from_relative_policy_optimization",
        },
        "target_labels": "common target-bearing Frama-C/WP verdicts",
        "survivors": "archived exact target-free Houdini survivors",
        "variants": variant_metrics,
        "cross_validation_choices": {
            "raw_family_scores": cv_choices,
            "signature_family_scores": signature_cv_choices,
        },
        "paired_bootstrap": paired,
        "limitations": [
            "Frozen-candidate observational rescore, not a retraining ablation.",
            "Candidate-aware signatures use the archived candidate set and may not transfer to GRPO rollout groups.",
            "The 832 tasks were previously inspected, so out-of-fold results still require a fresh external confirmation set.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    args.report.write_text(report_markdown(output))
    print(args.output)
    print(args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
