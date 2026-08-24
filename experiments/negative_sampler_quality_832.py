"""Reproducible quality audit for the frozen full-832 negative samples.

The audit is observational: it joins archived target-verification labels with
archived negative-coverage scores.  It never treats coverage as proof and does
not call a model or Frama-C.

Run:
    python3 -m experiments.negative_sampler_quality_832
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import gzip
import json
import math
from pathlib import Path
import random
import statistics


REPO = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO / "results" / "gpt5nano_full832"
EXPECTED_TASKS = 832


def quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def auc(rows: list[dict]) -> float | None:
    """Tie-aware ROC AUC."""
    positives = sum(row["verified"] for row in rows)
    negatives = len(rows) - positives
    if not positives or not negatives:
        return None
    groups: dict[float, list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        groups[row["score"]][int(row["verified"])] += 1
    negatives_below = 0
    concordance = 0.0
    for score in sorted(groups):
        n_negative, n_positive = groups[score]
        concordance += n_positive * negatives_below
        concordance += 0.5 * n_positive * n_negative
        negatives_below += n_negative
    return concordance / (positives * negatives)


def pair_counts(task_groups: list[list[dict]]) -> tuple[int, int, int]:
    higher = tied = lower = 0
    for rows in task_groups:
        good = [row["score"] for row in rows if row["verified"]]
        bad = [row["score"] for row in rows if not row["verified"]]
        for good_score in good:
            for bad_score in bad:
                higher += good_score > bad_score
                tied += good_score == bad_score
                lower += good_score < bad_score
    return higher, tied, lower


def pair_auc(task_groups: list[list[dict]]) -> float | None:
    higher, tied, lower = pair_counts(task_groups)
    total = higher + tied + lower
    return (higher + 0.5 * tied) / total if total else None


def selection(task_groups: list[list[dict]]) -> dict:
    expected_top = random_baseline = 0.0
    optimistic = pessimistic = oracle = 0
    top_ties = mixed_top_ties = mixed_tasks = 0
    mixed_top = mixed_random = 0.0
    for rows in task_groups:
        labels = [row["verified"] for row in rows]
        maximum = max(row["score"] for row in rows)
        top = [row["verified"] for row in rows if row["score"] == maximum]
        top_rate = sum(top) / len(top)
        random_rate = sum(labels) / len(labels)
        expected_top += top_rate
        random_baseline += random_rate
        optimistic += any(top)
        pessimistic += all(top)
        oracle += any(labels)
        top_ties += len(top) > 1
        mixed_top_ties += any(top) and not all(top)
        if any(labels) and not all(labels):
            mixed_tasks += 1
            mixed_top += top_rate
            mixed_random += random_rate
    count = len(task_groups)
    return {
        "tasks": count,
        "expected_top1_random_tie_break": expected_top / count,
        "random_candidate_baseline": random_baseline / count,
        "oracle_any_verified": oracle / count,
        "optimistic_any_top_verified": optimistic / count,
        "pessimistic_all_top_verified": pessimistic / count,
        "top_score_tie_tasks": top_ties,
        "mixed_label_top_tie_tasks": mixed_top_ties,
        "mixed_label_tasks": mixed_tasks,
        "mixed_expected_top1": mixed_top / mixed_tasks,
        "mixed_random_candidate": mixed_random / mixed_tasks,
    }


def point_metrics(task_groups: list[list[dict]]) -> dict[str, float]:
    rows = [row for group in task_groups for row in group]
    full = [row for row in rows if row["score"] == 1.0]
    choose = selection(task_groups)
    return {
        "pooled_auc": auc(rows),
        "within_task_pair_auc": pair_auc(task_groups),
        "top1_expected": choose["expected_top1_random_tie_break"],
        "random_candidate": choose["random_candidate_baseline"],
        "top1_lift": (
            choose["expected_top1_random_tie_break"]
            - choose["random_candidate_baseline"]
        ),
        "full_score_precision": sum(row["verified"] for row in full) / len(full),
    }


def bootstrap(
    task_groups: list[list[dict]], repetitions: int, seed: int
) -> dict:
    """Task-cluster bootstrap: candidates for the same program move together."""
    point = point_metrics(task_groups)
    if repetitions == 0:
        return {
            key: {"estimate": value, "cluster_bootstrap_95_ci": None}
            for key, value in point.items()
        }
    rng = random.Random(seed)
    samples = {key: [] for key in point}
    for _ in range(repetitions):
        drawn = [rng.choice(task_groups) for _ in task_groups]
        for key, value in point_metrics(drawn).items():
            samples[key].append(value)
    return {
        key: {
            "estimate": point[key],
            "cluster_bootstrap_95_ci": [
                quantile(samples[key], 0.025),
                quantile(samples[key], 0.975),
            ],
        }
        for key in point
    }


def paired_sampler_bootstrap(
    frozen_rows: list[dict],
    current_rows: list[dict],
    repetitions: int,
    seed: int,
) -> dict | None:
    """Task-paired bootstrap of current-minus-frozen metric changes."""
    if not frozen_rows or not current_rows:
        return None

    def keyed(rows: list[dict]) -> dict[tuple[str, str], dict[str, dict]]:
        output: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
        for row in rows:
            output[row["task"]][row["method"]] = row
        return output

    frozen = keyed(frozen_rows)
    current = keyed(current_rows)
    if set(frozen) != set(current):
        raise RuntimeError("matched sampler comparison has different tasks")
    paired = []
    for task in sorted(frozen):
        if set(frozen[task]) != set(current[task]):
            raise RuntimeError(
                f"matched sampler comparison has different methods: {task}"
            )
        methods = sorted(frozen[task])
        frozen_group = [frozen[task][method] for method in methods]
        current_group = [current[task][method] for method in methods]
        if [row["verified"] for row in frozen_group] != [
            row["verified"] for row in current_group
        ]:
            raise RuntimeError(
                f"matched sampler comparison has different labels: {task}"
            )
        paired.append((frozen_group, current_group))

    keys = ("pooled_auc", "within_task_pair_auc", "top1_expected")
    frozen_point = point_metrics([pair[0] for pair in paired])
    current_point = point_metrics([pair[1] for pair in paired])
    estimates = {
        key: current_point[key] - frozen_point[key] for key in keys
    }
    if repetitions == 0:
        return {
            key: {"estimate": value, "cluster_bootstrap_95_ci": None}
            for key, value in estimates.items()
        }

    rng = random.Random(seed)
    samples = {key: [] for key in keys}
    for _ in range(repetitions):
        drawn = [rng.choice(paired) for _ in paired]
        frozen_metrics = point_metrics([pair[0] for pair in drawn])
        current_metrics = point_metrics([pair[1] for pair in drawn])
        for key in keys:
            samples[key].append(current_metrics[key] - frozen_metrics[key])
    return {
        key: {
            "estimate": estimates[key],
            "cluster_bootstrap_95_ci": [
                quantile(samples[key], 0.025),
                quantile(samples[key], 0.975),
            ],
        }
        for key in keys
    }


def sample_summary(manifest: list[dict]) -> dict:
    def summarize(rows: list[dict]) -> dict:
        traces = [int(row["negative_trace_count"]) for row in rows]
        zero = sum(value == 0 for value in traces)
        return {
            "tasks": len(rows),
            "completed": sum(row["sample_status"] == "completed" for row in rows),
            "with_negatives": len(rows) - zero,
            "zero_negatives": zero,
            "zero_negative_rate": zero / len(rows),
            "trace_mean": statistics.mean(traces),
            "trace_median": statistics.median(traces),
            "trace_min": min(traces),
            "trace_max": max(traces),
            "positive_states": sum(int(row["positive_state_count"]) for row in rows),
            "negative_witness_states": sum(
                int(row["negative_state_count"]) for row in rows
            ),
        }

    suites = sorted({row["suite"] for row in manifest})
    return {
        "all": summarize(manifest),
        "by_suite": {
            suite: summarize([row for row in manifest if row["suite"] == suite])
            for suite in suites
        },
    }


def sampler_version(root: Path, manifest: list[dict]) -> dict:
    first = manifest[0]
    path = Path(first["sample_artifact"])
    if not path.is_file():
        path = root / "samples" / first["suite"] / f"{first['case_id']}.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        archived_stats = json.load(handle)["stats"]
    from rl_pipeline.sampler import example_sampler as sampler_module
    current_negative_budget = sampler_module._NEGATIVE_GROUP_BUDGET
    current_frame_budget = getattr(sampler_module, "_FRAME_GROUP_BUDGET", 0)
    return {
        "archived_config": first["sampler"],
        "archived_negative_budget": archived_stats["negative_budget"],
        "archived_frame_budget": archived_stats.get("frame_budget", 0),
        "current_code_negative_budget": current_negative_budget,
        "current_code_frame_budget": current_frame_budget,
        "archive_matches_current_budget": (
            archived_stats["negative_budget"] == current_negative_budget
        ),
    }


def load_candidates(path: Path) -> tuple[list[dict], list[dict]]:
    graded, fallback = [], []
    with path.open(newline="", encoding="utf-8") as handle:
        for source in csv.DictReader(handle):
            if source["verified"] not in {"True", "False"}:
                continue
            row = {
                "task": (source["suite"], source["case_id"]),
                "suite": source["suite"],
                "method": source["method"],
                "verified": source["verified"] == "True",
            }
            if (
                source["negative_trace_count"] not in {"", "0"}
                and source["negative_rejection_score"] != ""
            ):
                graded.append({
                    **row,
                    "score": float(source["negative_rejection_score"]),
                })
            elif (
                source["negative_trace_count"] == "0"
                and source["binary_frama_c_validation"] != ""
            ):
                fallback.append({
                    **row,
                    "score": float(source["binary_frama_c_validation"]),
                })
    return graded, fallback


def load_current_candidates(
    path: Path, *, require_archived_score: bool = False
) -> tuple[list[dict], list[dict]]:
    graded, fallback = [], []
    if not path.is_file():
        return graded, fallback
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        source = json.loads(line)
        if source.get("score_error") is not None:
            continue
        if (
            require_archived_score
            and source.get("archived_negative_score") is None
        ):
            continue
        row = {
            "task": (source["suite"], str(source["case_id"])),
            "suite": source["suite"],
            "method": source["method"],
            "verified": bool(source["verified"]),
        }
        if source.get("current_negative_score") is not None:
            graded.append({
                **row,
                "score": float(source["current_negative_score"]),
            })
        elif source.get("current_binary_fallback") is not None:
            fallback.append({
                **row,
                "score": float(source["current_binary_fallback"]),
            })
    return graded, fallback


def current_score_shift(path: Path) -> dict | None:
    if not path.is_file():
        return None
    rows = [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    matched = [
        row
        for row in rows
        if row.get("archived_negative_score") is not None
        and row.get("current_negative_score") is not None
    ]
    deltas = [
        row["current_negative_score"] - row["archived_negative_score"]
        for row in matched
    ]

    def label_delta(verified: bool) -> dict:
        values = [
            row["current_negative_score"] - row["archived_negative_score"]
            for row in matched
            if row["verified"] is verified
        ]
        return {
            "rows": len(values),
            "mean": statistics.mean(values),
            "median": statistics.median(values),
        }

    return {
        "matched_rows": len(matched),
        "mean": statistics.mean(deltas),
        "median": statistics.median(deltas),
        "increased": sum(delta > 0 for delta in deltas),
        "unchanged": sum(delta == 0 for delta in deltas),
        "decreased": sum(delta < 0 for delta in deltas),
        "verified": label_delta(True),
        "failed": label_delta(False),
    }


def sliced(rows: list[dict]) -> dict:
    by_task: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        by_task[row["task"]].append(row)
    groups = list(by_task.values())
    full = [row for row in rows if row["score"] == 1.0]
    return {
        "candidates": len(rows),
        "tasks": len(groups),
        "mixed_label_tasks": sum(
            any(row["verified"] for row in group)
            and not all(row["verified"] for row in group)
            for group in groups
        ),
        "verified": sum(row["verified"] for row in rows),
        "verified_rate": sum(row["verified"] for row in rows) / len(rows),
        "auc": auc(rows),
        "within_task_pair_auc": pair_auc(groups),
        "full_score_candidates": len(full),
        "full_score_verified": sum(row["verified"] for row in full),
        "full_score_precision": (
            sum(row["verified"] for row in full) / len(full) if full else None
        ),
    }


def candidate_summary(rows: list[dict], repetitions: int, seed: int) -> dict:
    by_task: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        by_task[row["task"]].append(row)
    groups = list(by_task.values())
    scores = [row["score"] for row in rows]
    verified = [row for row in rows if row["verified"]]
    full = [row for row in rows if row["score"] == 1.0]
    multiple = [group for group in groups if len(group) >= 2]
    unique = [len({row["score"] for row in group}) for group in multiple]
    ranges = [
        max(row["score"] for row in group) - min(row["score"] for row in group)
        for group in multiple
    ]
    higher, tied, lower = pair_counts(groups)
    thresholds = []
    for threshold in (0, 0.1, 0.25, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1):
        selected = [row for row in rows if row["score"] >= threshold]
        positives = sum(row["verified"] for row in selected)
        thresholds.append({
            "threshold": threshold,
            "selected": len(selected),
            "precision": positives / len(selected),
            "recall": positives / len(verified),
            "tasks": len({row["task"] for row in selected}),
        })
    return {
        **sliced(rows),
        "methods": sorted({row["method"] for row in rows}),
        "score_mean": statistics.mean(scores),
        "score_median": statistics.median(scores),
        "unique_score_values": len(set(scores)),
        "score_zero": sum(score == 0 for score in scores),
        "score_between_zero_and_one": sum(0 < score < 1 for score in scores),
        "score_one": len(full),
        "full_score_failed": sum(not row["verified"] for row in full),
        "full_score_recall": sum(row["verified"] for row in full) / len(verified),
        "verified_with_zero_score": sum(
            row["verified"] and row["score"] == 0 for row in rows
        ),
        "within_task_pairs": {
            "verified_higher": higher,
            "tied": tied,
            "verified_lower": lower,
            "pair_auc": (higher + 0.5 * tied) / (higher + tied + lower),
        },
        "selection": selection(groups),
        "gradient": {
            "tasks_with_multiple_candidates": len(multiple),
            "tasks_with_multiple_scores": sum(value > 1 for value in unique),
            "tasks_all_scores_tied": sum(value == 1 for value in unique),
            "unique_scores_per_task_median": statistics.median(unique),
            "score_range_per_task_median": statistics.median(ranges),
            "score_range_per_task_mean": statistics.mean(ranges),
        },
        "thresholds": thresholds,
        "by_suite": {
            suite: sliced([row for row in rows if row["suite"] == suite])
            for suite in sorted({row["suite"] for row in rows})
        },
        "by_method": {
            method: sliced([row for row in rows if row["method"] == method])
            for method in sorted({row["method"] for row in rows})
        },
        "verified_quartiles": [
            quantile([row["score"] for row in verified], p)
            for p in (0.25, 0.5, 0.75)
        ],
        "failed_quartiles": [
            quantile([row["score"] for row in rows if not row["verified"]], p)
            for p in (0.25, 0.5, 0.75)
        ],
        "task_cluster_bootstrap": bootstrap(groups, repetitions, seed),
    }


def fallback_summary(rows: list[dict]) -> dict:
    passed = [row for row in rows if row["score"] == 1]
    failed = [row for row in rows if row["score"] == 0]
    return {
        "candidate_rows": len(rows),
        "tasks": len({row["task"] for row in rows}),
        "binary_pass": len(passed),
        "binary_fail": len(failed),
        "target_verified": sum(row["verified"] for row in rows),
        "target_precision_when_binary_passes": (
            sum(row["verified"] for row in passed) / len(passed)
        ),
        "target_rate_when_binary_fails": (
            sum(row["verified"] for row in failed) / len(failed)
        ),
    }


def mislabel_summary(path: Path, retry_path: Path | None = None) -> dict | None:
    """Summarize the current-sampler finite reachability collision audit."""
    if not path.is_file():
        return None
    rows = json.loads(path.read_text())
    original_errors = [row for row in rows if "error" in row]
    retry_payload = None
    if retry_path is not None and retry_path.is_file():
        retry_payload = json.loads(retry_path.read_text())
        retries = {
            row["program"]: row for row in retry_payload["results"]
        }
        rows = [retries.get(row["program"], row) for row in rows]
    completed = [row for row in rows if "error" not in row]
    errors = [row for row in rows if "error" in row]
    hard = [row for row in completed if row.get("pair_mislabels", 0)]
    soft = [
        row
        for row in completed
        if row.get("vars_overlap", 0) and not row.get("pair_mislabels", 0)
    ]
    return {
        "artifact": str(path.relative_to(REPO)),
        "retry_artifact": (
            str(retry_path.relative_to(REPO)) if retry_payload else None
        ),
        "base_runs": 12,
        "audit_runs_per_seed": 24,
        "audit_seeds": [0, 9],
        "canonical_seed_errors": len(original_errors),
        "fallback_audit_seed": (
            retry_payload["fallback_audit_seed"] if retry_payload else None
        ),
        "retried_programs": (
            len(retry_payload["results"]) if retry_payload else 0
        ),
        "programs": len(rows),
        "completed": len(completed),
        "errors": len(errors),
        "with_negative_programs": sum(
            row.get("n_neg", 0) > 0 for row in completed
        ),
        "zero_negative_programs": sum(
            row.get("n_neg", 0) == 0 for row in completed
        ),
        "zero_programs": sorted(
            row["program"]
            for row in completed
            if row.get("n_neg", 0) == 0
        ),
        "negative_states": sum(row.get("n_neg", 0) for row in completed),
        "hard_mislabel_programs": len(hard),
        "hard_mislabel_states": sum(row["pair_mislabels"] for row in hard),
        "vars_only_overlap_programs_without_hard": len(soft),
        "vars_only_overlap_states_without_hard": sum(
            row["vars_overlap"] for row in soft
        ),
    }


def frame_control_summary(path: Path) -> dict | None:
    """Keep the decisive rows from the current frame-family control."""
    if not path.is_file():
        return None
    payload = json.loads(path.read_text())
    rows = {row["name"]: row for row in payload["rows"]}
    return {
        "artifact": str(path.relative_to(REPO)),
        "protocol": payload["protocol"],
        "program": payload["program"],
        "sampler_stats": payload["sampler_stats"],
        "trivial": rows["trivial"],
        "frame_only": rows["frame_only"],
        "gold": rows["gold"],
        "gold_plus_frame": rows["gold_plus_frame"],
    }


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


def markdown(report: dict) -> str:
    samples = report["samples"]
    current_samples = report["current_samples"]
    candidates = report["graded_candidates"]
    current_candidates = report["current_four_family_candidates"]
    current_matched = report[
        "current_four_family_matched_frozen_candidates"
    ]
    current_shift = report["current_four_family_matched_score_shift"]
    paired_version_boot = report[
        "current_minus_frozen_paired_bootstrap"
    ]
    current_sensitivity = report[
        "current_four_family_sensitivity_excluding_trivial_target_tasks"
    ]
    sensitivity = report["sensitivity_excluding_trivial_target_tasks"]
    trivial = report["trivial_control"]
    fallback = report["zero_negative_fallback"]
    current_fallback = report[
        "current_four_family_zero_negative_fallback"
    ]
    current_rescore = report["current_four_family_rescore_provenance"]
    training = report["released_training_guard"]
    version = report["sampler_version"]
    controlled = report["controlled_discrimination"]
    frame = report["frame_control"]
    mislabels = report["current_mislabel_audit"]
    pair = candidates["within_task_pairs"]
    choose = candidates["selection"]
    gradient = candidates["gradient"]
    boot = candidates["task_cluster_bootstrap"]
    thresholds = {
        row["threshold"]: row for row in candidates["thresholds"]
    }
    paired_sampler = controlled["summary"][
        "paired_current_structured_vs_random"
    ]
    current_choose = (
        current_candidates["selection"] if current_candidates else None
    )
    current_matched_choose = (
        current_matched["selection"] if current_matched else None
    )
    current_boot = (
        current_candidates["task_cluster_bootstrap"]
        if current_candidates else None
    )
    current_gradient = (
        current_candidates["gradient"] if current_candidates else None
    )
    houdini_methods = (
        "loopgym_r1_houdini",
        "loopgym_r5_houdini",
        "loopgym_r10_houdini",
    )
    prepruned_full_failures = sum(
        candidates["by_method"][method]["full_score_candidates"]
        - candidates["by_method"][method]["full_score_verified"]
        for method in houdini_methods
    )
    lines = [
        "# 832 例负例采样质量审计",
        "",
        "## 结论",
        "",
        "负例覆盖分是有用的同任务候选排序信号，但不是目标验证器；"
        "当前零负例回退也不满足严格的 anti-trivial 要求。",
        "",
        f"- 冻结三族样本的 832 例中，{samples['all']['with_negatives']} 例有连续覆盖分，"
        f"{samples['all']['zero_negatives']} 例（{pct(samples['all']['zero_negative_rate'])}）"
        "没有负例，只能退化为二值归纳性。",
        f"- {candidates['candidates']} 个真实候选上，池化 ROC-AUC="
        f"{candidates['auc']:.3f}；同任务 verified-vs-failed 配对 AUC="
        f"{pair['pair_auc']:.3f}。",
        f"- 同任务取最高分并随机打破并列，期望目标通过率 "
        f"{pct(choose['expected_top1_random_tie_break'])}，随机候选基线 "
        f"{pct(choose['random_candidate_baseline'])}，候选池 oracle 上限 "
        f"{pct(choose['oracle_any_verified'])}。",
        f"- 覆盖满分的 {candidates['score_one']} 个候选中仍有 "
        f"{candidates['full_score_failed']} 个未通过目标验证；满分精度 "
        f"{pct(candidates['full_score_precision'])}，召回 "
        f"{pct(candidates['full_score_recall'])}。",
        f"- 冻结样本中，1 == 1 在 {samples['all']['with_negatives']} 个有负例任务上"
        "覆盖分严格为 0；"
        f"在 {samples['all']['zero_negatives']} 个零负例任务上，现有回退会给它满分 1，"
        f"其中 {trivial['empirical_zero_negative_target_failed_tasks']} 个仍无法通过目标。",
        f"- 真实 Frama-C 对照还发现 1 == 1 本身能让 {trivial['empirical_target_verified_tasks']}"
        " / 832 个目标通过，说明“目标通过”标签也包含不需要有用不变式的任务。",
        "- 当前四族 sampler 的 10 例受控阶梯中，gold 都高于 loose，trivial 都为 0；"
        "但结果未提供优于同预算 random 基线的证据，并出现了 frame 家族奖励无关"
        "样板子句的反例。",
        (
            f"- 当前代码重采样 832 例后，{mislabels['with_negative_programs']} 例有负例、"
            f"{mislabels['zero_negative_programs']} 例仍为零；扩大正例审计未观察到完整状态"
            f"碰撞（{mislabels['hard_mislabel_states']} / "
            f"{mislabels['negative_states']}）。"
            if mislabels else
            "- 当前代码的 832 例重采样审计尚未完成。"
        ),
        (
            f"- 用当前四族负例重评 {current_candidates['candidates']} 个候选后，池化 "
            f"AUC={current_candidates['auc']:.3f}、同任务配对 "
            f"AUC={current_candidates['within_task_pairs']['pair_auc']:.3f}，"
            f"top-1 期望通过率={pct(current_choose['expected_top1_random_tie_break'])}；"
            f"匹配旧版同一批候选时 AUC={current_matched['auc']:.3f}。当前覆盖满分"
            f"候选中仍有 {current_candidates['full_score_failed']} 个目标失败。"
            if current_candidates else
            "- 当前四族样本的全候选重评分尚无结果文件。"
        ),
        "",
        "因此可以用该分数在同一循环内排序或保留 top-k；不能用全局阈值代替"
        "最终 Frama-C/WP 目标验证，也不能声称 trivial invariant 不会满分。",
        "",
        "## 算法实际测量的量",
        "",
        "reward 路径先剥离 assert/ensures，再用 12 次、seed=0 的具体执行收集可达"
        "loop-head 正状态。当前 structured sampler 在这些状态附近构造 relation、"
        "over-run、escape、frame 四族候选；冻结 832 结果则是尚无 frame 的三族版本。",
        "",
        "候选子句先经过目标隐藏的 Houdini/Frama-C 归纳性筛选。base score 是被剩余"
        "子句拒绝的合成负轨迹组比例；完整训练 reward 再加 0.3 倍组内 Shapley credit，"
        "并减重复与超长惩罚。换言之，它估计的是特定 sampler 分布下的经验排斥强度，"
        "不是可达集补集上的完备语义强度，也没有读取验证目标。",
        "",
        "实际 inference 不调用负例 sampler：它合并 rollout、用 Houdini 裁剪，最后才"
        "把原 assert/ensures 恢复给 Frama-C/WP。因此 target pass 必须作为独立终审，"
        "负例分最多是训练信号或候选排序器。",
        "",
        "## 样本覆盖",
        "",
        "| version | suite | tasks | 有负例 | 零负例 | 零负例率 | 负例数中位数 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for suite, values in samples["by_suite"].items():
        lines.append(
            f"| frozen | {suite} | {values['tasks']} | "
            f"{values['with_negatives']} | "
            f"{values['zero_negatives']} | {pct(values['zero_negative_rate'])} | "
            f"{values['trace_median']:.1f} |"
        )
    if current_samples:
        for suite, values in current_samples["by_suite"].items():
            lines.append(
                f"| current | {suite} | {values['tasks']} | "
                f"{values['with_negatives']} | {values['zero_negatives']} | "
                f"{pct(values['zero_negative_rate'])} | "
                f"{values['trace_median']:.1f} |"
            )
    lines += [
        "",
        "## 目标筛选能力",
        "",
        "| sample version | candidates | tasks | pooled AUC | within-task AUC | top-1 | random | full precision |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| frozen 3-family | {candidates['candidates']} | {candidates['tasks']} | "
        f"{candidates['auc']:.3f} | {candidates['within_task_pair_auc']:.3f} | "
        f"{pct(choose['expected_top1_random_tie_break'])} | "
        f"{pct(choose['random_candidate_baseline'])} | "
        f"{pct(candidates['full_score_precision'])} |",
    ]
    if current_matched:
        lines.append(
            f"| current 4-family, matched | {current_matched['candidates']} | "
            f"{current_matched['tasks']} | {current_matched['auc']:.3f} | "
            f"{current_matched['within_task_pair_auc']:.3f} | "
            f"{pct(current_matched_choose['expected_top1_random_tie_break'])} | "
            f"{pct(current_matched_choose['random_candidate_baseline'])} | "
            f"{pct(current_matched['full_score_precision'])} |"
        )
    if current_candidates:
        lines.append(
            f"| current 4-family | {current_candidates['candidates']} | "
            f"{current_candidates['tasks']} | {current_candidates['auc']:.3f} | "
            f"{current_candidates['within_task_pair_auc']:.3f} | "
            f"{pct(current_choose['expected_top1_random_tie_break'])} | "
            f"{pct(current_choose['random_candidate_baseline'])} | "
            f"{pct(current_candidates['full_score_precision'])} |"
        )
    if current_shift and current_matched:
        lines += [
            "",
            "在完全相同的 5131 个候选上，四族版相对三族版的 AUC 从 "
            f"{candidates['auc']:.3f} 降到 {current_matched['auc']:.3f}，"
            "同任务 AUC 从 "
            f"{candidates['within_task_pair_auc']:.3f} 降到 "
            f"{current_matched['within_task_pair_auc']:.3f}，top-1 从 "
            f"{pct(choose['expected_top1_random_tie_break'])} 降到 "
            f"{pct(current_matched_choose['expected_top1_random_tie_break'])}。"
            f"verified 候选平均分变化 {current_shift['verified']['mean']:+.3f}，"
            f"failed 候选为 {current_shift['failed']['mean']:+.3f}。",
            "任务配对 bootstrap 的 current-minus-frozen 95% CI：AUC "
            f"[{paired_version_boot['pooled_auc']['cluster_bootstrap_95_ci'][0]:+.3f}, "
            f"{paired_version_boot['pooled_auc']['cluster_bootstrap_95_ci'][1]:+.3f}]，"
            "同任务 AUC "
            f"[{paired_version_boot['within_task_pair_auc']['cluster_bootstrap_95_ci'][0]:+.3f}, "
            f"{paired_version_boot['within_task_pair_auc']['cluster_bootstrap_95_ci'][1]:+.3f}]，"
            "top-1 "
            f"[{paired_version_boot['top1_expected']['cluster_bootstrap_95_ci'][0]:+.3f}, "
            f"{paired_version_boot['top1_expected']['cluster_bootstrap_95_ci'][1]:+.3f}]。",
            "这使新版下降不能用新增的 59 个可评分任务解释；在匹配候选内已经存在。"
            "该观察与 frame 分母稀释反例一致，但不能单凭相关性把全部下降都归因于 frame。",
        ]
    lines += [
        "",
        "冻结三族样本的 suite 分解：",
        "",
        "| suite | candidates | mixed tasks | verified rate | pooled AUC | within-task AUC | full precision |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for suite, values in candidates["by_suite"].items():
        lines.append(
            f"| {suite} | {values['candidates']} | {values['mixed_label_tasks']} | "
            f"{pct(values['verified_rate'])} | "
            f"{values['auc']:.3f} | {values['within_task_pair_auc']:.3f} | "
            f"{pct(values['full_score_precision'])} |"
        )
    if current_candidates:
        lines += [
            "",
            "当前四族样本的 suite 分解：",
            "",
            "| suite | candidates | mixed tasks | verified rate | pooled AUC | within-task AUC | full precision |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for suite, values in current_candidates["by_suite"].items():
            lines.append(
                f"| {suite} | {values['candidates']} | "
                f"{values['mixed_label_tasks']} | "
                f"{pct(values['verified_rate'])} | {values['auc']:.3f} | "
                f"{values['within_task_pair_auc']:.3f} | "
                f"{pct(values['full_score_precision'])} |"
            )
    lines += [
        "",
        "| version | score threshold | selected | precision | recall | covered tasks |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    threshold_versions = [("frozen", thresholds)]
    if current_candidates:
        threshold_versions.append((
            "current",
            {
                row["threshold"]: row
                for row in current_candidates["thresholds"]
            },
        ))
    for version_name, version_thresholds in threshold_versions:
        for threshold in (0.5, 0.9, 1):
            values = version_thresholds[threshold]
            lines.append(
                f"| {version_name} | {threshold:.1f} | "
                f"{values['selected']} | {pct(values['precision'])} | "
                f"{pct(values['recall'])} | {values['tasks']} |"
            )
    lines += [
        "",
        "任务簇 bootstrap 95% CI：池化 AUC "
        f"[{boot['pooled_auc']['cluster_bootstrap_95_ci'][0]:.3f}, "
        f"{boot['pooled_auc']['cluster_bootstrap_95_ci'][1]:.3f}]；"
        "同任务配对 AUC "
        f"[{boot['within_task_pair_auc']['cluster_bootstrap_95_ci'][0]:.3f}, "
        f"{boot['within_task_pair_auc']['cluster_bootstrap_95_ci'][1]:.3f}]；"
        "top-1 相对随机候选的提升为 "
        f"{pct(boot['top1_lift']['estimate'])}，95% CI "
        f"[{pct(boot['top1_lift']['cluster_bootstrap_95_ci'][0])}, "
        f"{pct(boot['top1_lift']['cluster_bootstrap_95_ci'][1])}]。",
        (
            "当前四族任务簇 bootstrap 95% CI：池化 AUC "
            f"[{current_boot['pooled_auc']['cluster_bootstrap_95_ci'][0]:.3f}, "
            f"{current_boot['pooled_auc']['cluster_bootstrap_95_ci'][1]:.3f}]；"
            "同任务配对 AUC "
            f"[{current_boot['within_task_pair_auc']['cluster_bootstrap_95_ci'][0]:.3f}, "
            f"{current_boot['within_task_pair_auc']['cluster_bootstrap_95_ci'][1]:.3f}]；"
            "top-1 lift "
            f"{pct(current_boot['top1_lift']['estimate'])}，95% CI "
            f"[{pct(current_boot['top1_lift']['cluster_bootstrap_95_ci'][0])}, "
            f"{pct(current_boot['top1_lift']['cluster_bootstrap_95_ci'][1])}]。"
            if current_boot else
            "当前四族候选尚未计算 bootstrap。"
        ),
        "",
        "NLA 的 pooled AUC 很低但 within-task AUC 较高；后者只来自 8 个 mixed-label "
        "任务。总体上，分数主要度量某个循环内的约束强度，不同程序的目标难度不同，"
        "跨任务绝对分数不可直接校准。",
        "",
        f"只看同时含成功和失败候选的 {choose['mixed_label_tasks']} 个任务，最高分随机"
        f"打破并列后的期望成功率是 {pct(choose['mixed_expected_top1'])}，随机候选是 "
        f"{pct(choose['mixed_random_candidate'])}。这是更接近实际候选筛选的比较。",
        (
            f"当前四族在 {current_choose['mixed_label_tasks']} 个 mixed-label 任务上，"
            f"top-1 为 {pct(current_choose['mixed_expected_top1'])}，随机候选为 "
            f"{pct(current_choose['mixed_random_candidate'])}。"
            if current_choose else
            ""
        ),
        "",
        "剔除上述 trivial-target 任务后，候选池 AUC 为 "
        f"{sensitivity['auc']:.3f}，同任务配对 AUC 为 "
        f"{sensitivity['within_task_pairs']['pair_auc']:.3f}；满分精度仍只有 "
        f"{pct(sensitivity['full_score_precision'])}，所以主结论不依赖这些简单目标。",
        (
            "当前四族分数剔除 trivial-target 后，候选池 AUC="
            f"{current_sensitivity['auc']:.3f}、同任务配对 AUC="
            f"{current_sensitivity['within_task_pairs']['pair_auc']:.3f}、"
            f"满分精度={pct(current_sensitivity['full_score_precision'])}。"
            if current_sensitivity else
            ""
        ),
        "",
        "满分失败项中至少有 "
        f"{prepruned_full_failures} 个来自已经过 Houdini 裁剪的 CRAFT-H 输出，"
        "因此不能把全部假阳性归因于评分器替候选剔除了不归纳子句。另一方面，"
        f"有 {candidates['verified_with_zero_score']} 个目标通过候选得 0 分；剔除"
        f" trivial-target 任务后仍有 {sensitivity['verified_with_zero_score']} 个。",
        (
            f"当前四族有 {current_candidates['verified_with_zero_score']} 个目标通过候选"
            f"得 0 分；剔除 trivial-target 后仍有 "
            f"{current_sensitivity['verified_with_zero_score']} 个。"
            if current_candidates and current_sensitivity else
            ""
        ),
        "",
        "## 梯度与饱和",
        "",
        f"- 全部候选有 {candidates['unique_score_values']} 个不同分值；"
        f"0 分 {candidates['score_zero']} 个，中间分 "
        f"{candidates['score_between_zero_and_one']} 个，1 分 {candidates['score_one']} 个。",
        f"- {gradient['tasks_with_multiple_candidates']} 个可比较任务中，"
        f"{gradient['tasks_with_multiple_scores']} 个有多个分值，"
        f"{gradient['tasks_all_scores_tied']} 个完全并列；分值跨度中位数 "
        f"{gradient['score_range_per_task_median']:.3f}。",
        f"- 最高分在 {choose['top_score_tie_tasks']} / {choose['tasks']} 个任务上并列，"
        f"其中 {choose['mixed_label_top_tie_tasks']} 个并列集合同时含 verified 和 failed。",
        (
            f"- 当前四族：{current_candidates['unique_score_values']} 个不同分值，"
            f"{current_candidates['score_between_zero_and_one']} 个中间分；"
            f"{current_gradient['tasks_with_multiple_scores']} / "
            f"{current_gradient['tasks_with_multiple_candidates']} 个任务有多个分值，"
            f"最高分在 {current_choose['top_score_tie_tasks']} / "
            f"{current_choose['tasks']} 个任务并列。"
            if current_candidates else
            "- 当前四族候选梯度尚未重算。"
        ),
        "",
        "整体确有梯度，但顶端饱和明显，满分不能唯一识别可证明目标的候选。",
        "当前四族虽然把有多分值的任务从 621/688 提高到 707/747，但目标排序指标反而"
        "下降；所以“数值不相同”不等于“梯度方向与证明质量一致”。",
        "",
        "这里统计的是归档的 standalone base coverage。训练时的 Shapley 项取决于同组"
        "其他 rollout，因而还可能改变相对间距；没有原始 rollout group 就不能重建或"
        "保证实际 advantage 方差。",
        "",
        "## 受控强弱阶梯与随机基线",
        "",
        "| sampler | programs | mean negatives | gold mean/min | loose mean | mean gold-loose gap | gold>loose | trivial max |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in (
        "frozen_structured_3family",
        "current_structured_4family",
        "current_random",
    ):
        values = controlled["summary"][name]
        lines.append(
            f"| {name} | {values['programs']} | "
            f"{values['negative_traces_mean']:.1f} | "
            f"{values['gold_mean']:.3f}/{values['gold_min']:.3f} | "
            f"{values['loose_mean']:.3f} | "
            f"{values['gold_minus_loose_mean']:.3f} | "
            f"{values['gold_strictly_above_loose']}/{values['programs']} | "
            f"{values['trivial_max']:.3f} |"
        )
    lines += [
        "",
        "这 10 个手工指定的质量阶梯说明有负例时能区分 gold/loose/trivial；但样本很小，"
        "且 current structured 的平均 gold-loose gap 为 "
        f"{controlled['summary']['current_structured_4family']['gold_minus_loose_mean']:.3f}，"
        "random 为 "
        f"{controlled['summary']['current_random']['gold_minus_loose_mean']:.3f}。"
        f"逐程序比较 structured 仅胜 {paired_sampler['structured_wins']} / "
        f"{controlled['summary']['current_structured_4family']['programs']}，exact sign-flip "
        f"p={paired_sampler['exact_two_sided_sign_flip_p']:.3f}。这不是 structured 优于 "
        "random 的证据；两者虽共享上限，实际接纳的负例数也不同。它更不是训练后 "
        "pass rate 的因果消融。",
        "",
        "## 当前 frame 家族反例",
        "",
        f"在 {frame['program']} 上，当前样本含 "
        f"{frame['sampler_stats']['frame']} / {frame['sampler_stats']['n_neg']} 个 frame 负例。"
        "四个与目标无关、但确实归纳成立的参数 frame 子句单独取得 base="
        f"{frame['frame_only']['deployed_base']:.3f}、完整 reward="
        f"{frame['frame_only']['deployed_full_reward_singleton']:.3f}，却不能证明目标；"
        "gold 的 base/reward 只有 "
        f"{frame['gold']['deployed_base']:.3f}/"
        f"{frame['gold']['deployed_full_reward_singleton']:.3f}。把两者拼接后，"
        "目标证明能力不变，base/reward 却升至 "
        f"{frame['gold_plus_frame']['deployed_base']:.3f}/"
        f"{frame['gold_plus_frame']['deployed_full_reward_singleton']:.3f}。",
        "",
        "这说明把 frame 负例等权放入主分母，会奖励与验证目标无关的样板信息，"
        "并稀释真正证明所需的关系子句。frame 可保留为诊断项；若必须保持 target-hidden，"
        "主质量分可按 guard/transition relevance 限制变量，或至少按 family 分层归一化"
        "并降权。只有在允许 reward 读取目标时，才应使用 target backward slice。",
        "",
        "如果把 quality 定义为与目标无关的绝对状态空间强度，frame 子句确实让不变式"
        "更强，这个加分并非自相矛盾；但若验收目标是筛出能证明当前 assertion 的候选，"
        "它就是明确的目标错配。",
        "",
        "## 当前 832 例负例误标审计",
        "",
    ]
    if mislabels is None:
        lines += [
            "当前代码的扩大可达集碰撞审计尚无结果文件。",
            "",
        ]
    else:
        lines += [
            f"对 {mislabels['programs']} 个程序用 {mislabels['base_runs']} runs 生成当前"
            f"负例，并用 seed={mislabels['audit_seeds']}、每组 "
            f"{mislabels['audit_runs_per_seed']} runs 的"
            "扩大正例集检查完整 (vars, Pre, LoopEntry) 状态碰撞：共审计 "
            f"{mislabels['negative_states']} 个负状态，发现 "
            f"{mislabels['hard_mislabel_states']} 个 hard collision，涉及 "
            f"{mislabels['hard_mislabel_programs']} 个程序；执行错误 "
            f"{mislabels['errors']} 个。当前版本有负例/零负例程序数为 "
            f"{mislabels['with_negative_programs']}/"
            f"{mislabels['zero_negative_programs']}。",
            f"标准 seed=9 在 {mislabels['canonical_seed_errors']} 个程序上无法生成满足"
            f" requires 的输入；对这 {mislabels['retried_programs']} 项预先声明改用 seed="
            f"{mislabels['fallback_audit_seed']} 复核后，未解决错误为 "
            f"{mislabels['errors']}。",
            "",
            f"因此按当前 RewardCalculator，1 == 1 会在这 "
            f"{mislabels['zero_negative_programs']} 个零负例程序上触发二值回退并得 1；"
            f"其中只有 {mislabels['zero_negative_tautology_target_verified']} 个能通过目标，"
            f"{mislabels['zero_negative_tautology_target_failed']} 个不能。在其余有负例程序"
            "上的 base 为 0。",
            "",
            "这是有限动态审计，只能发现已观察到的误标，不能证明所有合成状态都不可达。"
            f"另有 {mislabels['vars_only_overlap_states_without_hard']} 个 vars-only overlap，"
            f"涉及 {mislabels['vars_only_overlap_programs_without_hard']} 个程序；它们不是 "
            "hard error，因为 Pre/LoopEntry 不同可能对应不同状态。",
            "真实 Houdini 是候选归纳可靠性的最后防线；即使存在负例误标，也不会让"
            "不归纳子句通过 Houdini，但会污染分母、压低诚实不变式的强度分。",
            "",
        ]
    lines += [
        "## 零负例回退",
        "",
        f"真实候选有 {fallback['candidate_rows']} 个零负例评分行；二值归纳性通过 "
        f"{fallback['binary_pass']} 个，其目标精度为 "
        f"{pct(fallback['target_precision_when_binary_passes'])}。"
        "tautology 总能通过归纳性检查，却不提供任何强度信息。",
        (
            f"当前四族的零负例区间有 {current_fallback['candidate_rows']} 个候选行；"
            f"二值归纳性通过 {current_fallback['binary_pass']} 个，其目标精度为 "
            f"{pct(current_fallback['target_precision_when_binary_passes'])}。"
            if current_fallback else
            ""
        ),
        "",
        f"精确 tautology 对照中，零负例任务只有 "
        f"{trivial['empirical_zero_negative_target_verified_tasks']} / "
        f"{trivial['zero_negative_tasks']} 个目标通过；另外有 "
        f"{trivial['empirical_target_verified_tasks'] - trivial['empirical_zero_negative_target_verified_tasks']}"
        " 个有负例任务虽覆盖分为 0，目标仍可通过。",
        "",
        "建议零负例任务不参与 GRPO（mask/skip），或先补足可审计的行为负例；"
        "不要把“非空且归纳成立”作为强度满分。仅做语法级 tautology 过滤仍可被"
        " x-x==0、x<=x 等等价形式绕过，并且仍没有连续梯度。",
        "",
        "已发布 RL 训练集实际做了这层保护："
        f"{training['negative_filter_policy']}，最终 {training['rows']} 行中"
        f"零负例行数为 {training['zero_negative_rows']}。所以该漏洞存在于通用"
        "RewardCalculator 回退和 832 诊断评分中，但不会被这份经过过滤的 RL"
        "训练集直接触发。",
        "",
        "## 版本边界",
        "",
        f"冻结样本预算为 {version['archived_negative_budget']}，frame 预算 "
        f"{version['archived_frame_budget']}；当前代码预算为 "
        f"{version['current_code_negative_budget']}，frame 预算 "
        f"{version['current_code_frame_budget']}。",
        (
            "当前四族的候选重评分复用了归档中完全匹配的 target-free Houdini survivors，"
            "只重新生成并替换负例；它是同一批候选的 sampler 敏感性比较，不是重新生成"
            "候选或重新训练模型。旧分复现抽查为 "
            f"{current_rescore['archived_score_reproduction']['exact_within_1e-15']} / "
            f"{current_rescore['archived_score_reproduction']['checked']}。"
            if current_candidates else
            "当前四族尚无全候选重评分，因此冻结指标不能冒充当前 sampler 结果。"
        ),
        "",
        "## 限制",
        "",
        "这是同一冻结候选池在三族/四族负例上的观察性重评分，不是 "
        "random-vs-structured 重新训练消融。"
        "候选来自多个方法，方法、suite 和任务难度会影响 pooled 指标，所以同时报告"
        "同任务配对指标和任务簇 bootstrap。最终标签来自共同 Frama-C/WP judge；"
        "负例分数本身不接触目标。仓库论文中的 sampler ablation 仍为 TBD，因而这些"
        "结果不能证明 structured sampler 相比 random sampler 会提升训练后的"
        " pass/compose 指标。冻结表中 AutoSpec 没有负例分数，Clause2Inv 也不支持"
        " Loopy，因此 5131 行并非九个方法的完整笛卡尔积。Loopy 与 core 还共享"
        "上游程序族，832 也不应解释成 832 个完全独立的语义任务。",
        "",
        "负例分按 Houdini survivors 计分，而共同目标 judge 对表中最终候选的全部子句"
        "负责；对未经 Houdini 的方法，这会把“可裁剪后成功”的响应记成失败，可能低估"
        "部署时先裁剪再验证的表现。不过上文已经单列预先裁剪的 CRAFT-H 假阳性，故"
        "不能消除“覆盖满分不蕴含目标充分性”的结论。",
        "",
        "这里的梯度统计来自跨方法候选池，不是训练时实际 GRPO rollout group；"
        "仓库没有对应的逐组原始 reward 方差或零方差比例，所以不能据此保证每个"
        "训练组都有有效 advantage。",
        "",
        "## 建议的验收口径",
        "",
        "1. 零负例任务返回 unscorable 并从相对策略优化中 mask；不要回退为满分二值奖励。",
        "2. 负例分只用于同任务排序/top-k，最终必须继续跑带目标的 Frama-C/WP。",
        "3. 主分数移除或显著降权 frame family，并按 family 分层归一化；target-hidden 场景只使用 guard/transition relevance。",
        "4. 保存训练时每个 rollout group 的原始 reward，报告零方差组比例；候选池有梯度不等于每个 GRPO 组有梯度。",
        "5. 补做 structured-vs-random、至少三种 seed 的完整重训练消融，再报告 pass@k/compose 和任务簇置信区间。",
        "",
    ]
    return "\n".join(lines)


def build_report(root: Path, repetitions: int, seed: int) -> dict:
    manifest = [
        json.loads(line)
        for line in (root / "samples_manifest.jsonl").read_text().splitlines()
        if line.strip()
    ]
    task_keys = {(row["suite"], str(row["case_id"])) for row in manifest}
    if len(manifest) != EXPECTED_TASKS or len(task_keys) != EXPECTED_TASKS:
        raise RuntimeError("sample manifest is not a unique full-832 manifest")
    graded, fallback = load_candidates(root / "final_results_9methods.csv")
    samples = sample_summary(manifest)
    current_manifest_path = (
        REPO / "results" / "negative_sampler_current832" /
        "samples_manifest.jsonl"
    )
    current_manifest = (
        [
            json.loads(line)
            for line in current_manifest_path.read_text().splitlines()
            if line.strip()
        ]
        if current_manifest_path.is_file() else []
    )
    if current_manifest and len(current_manifest) != EXPECTED_TASKS:
        raise RuntimeError("current sample manifest is not full-832")
    current_samples = (
        sample_summary(current_manifest) if current_manifest else None
    )
    if len({row["task"] for row in graded}) != samples["all"]["with_negatives"]:
        raise RuntimeError("graded candidate tasks do not match sampled tasks")
    training_audit_path = REPO / "paper" / "artifacts" / "rl_final_audit.json"
    training_audit = (
        json.loads(training_audit_path.read_text())
        if training_audit_path.is_file()
        else None
    )
    controlled_path = (
        REPO / "results" / "negative_sampler_discrimination_comparison.json"
    )
    frame_path = (
        REPO / "results" / "negative_sampler_frame_control_linear34.json"
    )
    mislabel_path = (
        REPO / "results" / "negative_sampler_mislabel_audit_832_current.json"
    )
    mislabel_retry_path = (
        REPO / "results" / "negative_sampler_mislabel_audit_832_retry.json"
    )
    controlled = json.loads(controlled_path.read_text())
    frame_control = frame_control_summary(frame_path)
    if frame_control is None:
        raise RuntimeError(f"missing frame control: {frame_path}")
    current_mislabels = mislabel_summary(
        mislabel_path, mislabel_retry_path
    )
    current_scores_path = (
        REPO / "results" / "negative_sampler_current832" /
        "candidate_scores.jsonl"
    )
    current_rescore_summary_path = (
        REPO / "results" / "negative_sampler_current832" /
        "rescore_summary.json"
    )
    current_rescore_summary = (
        json.loads(current_rescore_summary_path.read_text())
        if current_rescore_summary_path.is_file() else None
    )
    current_graded, current_fallback = load_current_candidates(
        current_scores_path
    )
    current_matched_graded, _ = load_current_candidates(
        current_scores_path, require_archived_score=True
    )
    trivial_path = root / "trivial_invariant_full832.jsonl"
    trivial_rows = (
        [
            json.loads(line)
            for line in trivial_path.read_text().splitlines()
            if line.strip()
        ]
        if trivial_path.is_file() else []
    )
    trivial_target_tasks = {
        (row["suite"], str(row["case_id"]))
        for row in trivial_rows
        if row.get("target_verified") is True
    }
    if current_mislabels is not None and trivial_rows:
        current_zero_programs = set(current_mislabels["zero_programs"])
        current_zero_trivial = [
            row
            for row in trivial_rows
            if f"{row['suite']}/{row['case_id']}.c" in current_zero_programs
        ]
        current_mislabels.update({
            "zero_negative_tautology_target_verified": sum(
                row.get("target_verified") is True
                for row in current_zero_trivial
            ),
            "zero_negative_tautology_target_failed": sum(
                row.get("target_verified") is False
                for row in current_zero_trivial
            ),
        })
    nontrivial_target_candidates = [
        row for row in graded if row["task"] not in trivial_target_tasks
    ]
    current_nontrivial_target_candidates = [
        row
        for row in current_graded
        if row["task"] not in trivial_target_tasks
    ]
    if current_graded and current_mislabels is not None and (
        len({row["task"] for row in current_graded})
        != current_mislabels["with_negative_programs"]
    ):
        raise RuntimeError(
            "current candidate tasks do not match current sampled tasks"
        )
    return {
        "protocol": "negative_sampler_quality_full832_v1",
        "results_root": str(root.resolve()),
        "sampler_version": sampler_version(root, manifest),
        "controlled_discrimination": controlled,
        "frame_control": frame_control,
        "current_mislabel_audit": current_mislabels,
        "samples": samples,
        "current_samples": current_samples,
        "trivial_control": {
            "invariant": "1 == 1",
            "with_negative_tasks": samples["all"]["with_negatives"],
            "coverage_score_with_negatives": 0.0,
            "zero_negative_tasks": samples["all"]["zero_negatives"],
            "binary_fallback_score": 1.0,
            "full_score_rate_over_832": (
                samples["all"]["zero_negatives"] / EXPECTED_TASKS
            ),
            "empirical_target_verified_tasks": len(trivial_target_tasks),
            "empirical_zero_negative_target_verified_tasks": sum(
                row.get("zero_negative") and row.get("target_verified") is True
                for row in trivial_rows
            ),
            "empirical_zero_negative_target_failed_tasks": sum(
                row.get("zero_negative") and row.get("target_verified") is False
                for row in trivial_rows
            ),
            "empirical_zero_negative_full_reward_tasks": sum(
                row.get("zero_negative") and row.get("fallback_reward") == 1.0
                for row in trivial_rows
            ),
            "empirical_artifact": (
                str(trivial_path.relative_to(REPO)) if trivial_rows else None
            ),
        },
        "graded_candidates": candidate_summary(graded, repetitions, seed),
        "current_four_family_candidates": (
            candidate_summary(current_graded, repetitions, seed)
            if current_graded else None
        ),
        "current_four_family_matched_frozen_candidates": (
            candidate_summary(
                current_matched_graded, repetitions, seed
            )
            if current_matched_graded else None
        ),
        "current_four_family_matched_score_shift": current_score_shift(
            current_scores_path
        ),
        "current_minus_frozen_paired_bootstrap": paired_sampler_bootstrap(
            graded, current_matched_graded, repetitions, seed
        ),
        "current_four_family_sensitivity_excluding_trivial_target_tasks": (
            candidate_summary(
                current_nontrivial_target_candidates, 0, seed
            )
            if current_graded and trivial_rows else None
        ),
        "current_four_family_zero_negative_fallback": (
            fallback_summary(current_fallback)
            if current_fallback else None
        ),
        "current_four_family_candidate_artifact": (
            str(current_scores_path.relative_to(REPO))
            if current_scores_path.is_file() else None
        ),
        "current_four_family_rescore_provenance": current_rescore_summary,
        "sensitivity_excluding_trivial_target_tasks": (
            candidate_summary(nontrivial_target_candidates, 0, seed)
            if trivial_rows else None
        ),
        "zero_negative_fallback": fallback_summary(fallback),
        "released_training_guard": (
            {
                "rows": training_audit["rows"],
                "negative_filter_policy": training_audit["negative_filter"]["policy"],
                "zero_negative_rows": training_audit["checks"]["zero_negative_rows"],
                "artifact": str(training_audit_path.relative_to(REPO)),
            }
            if training_audit else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--bootstrap", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260823)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    if args.bootstrap < 0:
        parser.error("--bootstrap must be non-negative")
    report = build_report(args.results_root, args.bootstrap, args.seed)
    json_path = args.json or args.results_root / "negative_sampler_quality_832.json"
    md_path = args.markdown or args.results_root / "negative_sampler_quality_832.md"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )
    md_path.write_text(markdown(report), encoding="utf-8")
    print(json_path)
    print(md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
