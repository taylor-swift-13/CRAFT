"""Build the final Full-832 tables with R5-H replacing the legacy R4-H row."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from rl_pipeline.common import prompts

from .common import DEFAULT_RESULTS_ROOT, sha256_text


FINAL_METHODS = (
    "autospec",
    "clause2inv",
    "sespec",
    "naive",
    "loopgym_r1_no_houdini",
    "loopgym_r1_houdini",
    "loopgym_r5_houdini",
    "loopgym_r10_houdini",
    "daikon",
)

DISPLAY = {
    "autospec": "AutoSpec",
    "clause2inv": "Clause2Inv",
    "sespec": "SESpec",
    "naive": "Naive",
    "loopgym_r1_no_houdini": "LoopGym R1-NoH",
    "loopgym_r1_houdini": "LoopGym R1-H",
    "loopgym_r5_houdini": "LoopGym R5-H (no reroll)",
    "loopgym_r10_houdini": "LoopGym R10-H (no reroll)",
    "daikon": "Daikon",
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _normalise(raw: dict[str, str], fields: list[str]) -> dict[str, str]:
    row = {field: raw.get(field, "") for field in fields}
    row["artifact"] = raw.get("artifact") or raw.get("api_calls_artifact") or ""
    return row


def _pct(value: str) -> str:
    return f"{100 * float(value):.2f}%"


def _cost_text(row: dict[str, str]) -> tuple[str, str]:
    method = row["method"]
    if method == "clause2inv":
        time = f"{float(row['mean_generation_seconds']):.2f} s (366 supported rows)"
    else:
        time = f"{float(row['mean_generation_seconds']):.2f} s ({row['time_rows']} rows)"
    exact = row.get("mean_total_tokens")
    estimated = row.get("mean_estimated_tokens")
    if method == "daikon":
        tokens = "0 (not called, 832 rows)"
    elif exact and estimated:
        tokens = (
            f"{float(exact):,.2f} exact ({row['token_rows']}); "
            f"{float(estimated):,.2f} estimated ({row['estimated_token_rows']})"
        )
    elif exact:
        tokens = f"{float(exact):,.2f} exact ({row['token_rows']} rows)"
    else:
        tokens = (
            f"{float(estimated):,.2f} estimated "
            f"({row['estimated_token_rows']} rows)"
        )
    return time, tokens


def main() -> int:
    root = DEFAULT_RESULTS_ROOT
    base_path = root / "final_results.csv"
    generic_fields = list(csv.DictReader(base_path.open(newline="")).fieldnames or [])
    base = [
        row for row in _read_csv(base_path)
        if row["method"] != "loopgym_r4_houdini"
    ]
    extensions = []
    for name in ("r5_results.csv", "r10_results.csv", "daikon_results.csv"):
        extensions.extend(_normalise(row, generic_fields) for row in _read_csv(root / name))
    final_rows = base + extensions
    final_rows.sort(key=lambda row: (FINAL_METHODS.index(row["method"]), row["suite"], row["case_id"]))
    _write_csv(root / "final_results_9methods.csv", generic_fields, final_rows)

    old_comparison = {
        row["method"]: row for row in _read_csv(root / "comparison_table_9methods.csv")
    }
    r5_summary = json.loads((root / "r5_summary.json").read_text())
    old_comparison["loopgym_r5_houdini"] = {
        "method": "loopgym_r5_houdini",
        "verified": r5_summary["verified"],
        "total": 832,
        "accuracy": r5_summary["accuracy"],
        "mean_generation_seconds": r5_summary["mean_generation_seconds"],
        "time_rows": r5_summary["time_rows"],
        "mean_total_tokens": r5_summary["mean_total_tokens"],
        "token_accounting": "exact",
        "token_rows": r5_summary["token_rows"],
        "mean_estimated_tokens": "",
        "estimated_token_rows": "",
        "negative_micro": r5_summary["negative_micro_rejection"],
        "negative_macro": r5_summary["negative_macro_rejection"],
    }
    comparison = [old_comparison[method] for method in FINAL_METHODS]
    comparison_fields = list(comparison[0])
    _write_csv(root / "comparison_table_9methods.csv", comparison_fields, comparison)

    lines = [
        "# Full-832 final comparison (R5-H replaces R4-H)",
        "",
        "| Tool / configuration | Correct / 832 | Mean generation time | Mean total tokens | Negative micro | Negative macro |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in comparison:
        method = row["method"]
        correct = f"{row['verified']} ({_pct(row['accuracy'])})"
        if method == "clause2inv":
            correct += "; 64/366 supported (17.49%)"
        time, tokens = _cost_text(row)
        lines.append(
            f"| {DISPLAY[method]} | {correct} | {time} | {tokens} | "
            f"{_pct(row['negative_micro'])} | {_pct(row['negative_macro'])} |"
        )
    lines += [
        "",
        "Correctness is direct common Frama-C validation against each restored hidden target.",
        "R5-H and R10-H use exactly five and ten target-hidden rollouts, respectively,",
        "followed by union and Houdini, with reroll disabled. R5-H replays the exact first",
        "five R10-H responses and independently recomputes union, Houdini, and validation;",
        "its reported cost includes the original response latency and exact token usage.",
        "R4-H is intentionally excluded from this final comparison.",
        "",
        "Daikon receives no assertion, postcondition, or negative traces and uses no Houdini.",
    ]
    (root / "comparison_table_9methods.md").write_text("\n".join(lines) + "\n")

    r5_rows = _read_csv(root / "r5_results.csv")
    suite_counts = {
        suite: {
            "rows": len(rows := [row for row in r5_rows if row["suite"] == suite]),
            "verified": sum(row["verified"] == "True" for row in rows),
        }
        for suite in ("linear", "NLA_lipus", "Loopy")
    }
    no_negative = [row for row in r5_rows if int(row["negative_trace_count"]) == 0]
    artifacts_ok = 0
    prompt_hashes_ok = 0
    source_artifacts_ok = 0
    for row in r5_rows:
        records = json.loads(Path(row["api_calls_artifact"]).read_text())
        expected_prompt_hash = sha256_text(
            prompts.GENERATE_PROMPT.format(
                program=Path(row["hidden_source"]).read_text(errors="ignore")
            )
        )
        if (
            len(records) == 5
            and all(record.get("reused") is True for record in records)
            and all(record.get("reuse_method") == "loopgym_r10_houdini" for record in records)
            and [record.get("reuse_prefix_position") for record in records] == list(range(5))
        ):
            artifacts_ok += 1
        if all(record.get("prompt_sha256") == expected_prompt_hash for record in records):
            prompt_hashes_ok += 1
        source_records = json.loads(Path(row["reuse_source_artifact"]).read_text())
        if len(source_records) == 10 and records == [
            {
                **record,
                "reused": True,
                "reuse_source": row["reuse_source_artifact"],
                "reuse_method": "loopgym_r10_houdini",
                "reuse_prefix_position": index,
            }
            for index, record in enumerate(source_records[:5])
        ]:
            source_artifacts_ok += 1
    method_counts = {
        method: sum(row["method"] == method for row in final_rows)
        for method in FINAL_METHODS
    }
    audit = {
        "complete": len(final_rows) == 9 * 832 and all(value == 832 for value in method_counts.values()),
        "methods": 9,
        "expected_per_method": 832,
        "total_rows": len(final_rows),
        "method_rows": method_counts,
        "r4_in_final_table": any(row["method"] == "loopgym_r4_houdini" for row in final_rows),
        "r5_rows": len(r5_rows),
        "r5_generation_failures": sum(row["generation_status"] != "completed" for row in r5_rows),
        "r5_scoring_failures": sum(row["negative_score_status"] != "completed" for row in r5_rows),
        "r5_verified": sum(row["verified"] == "True" for row in r5_rows),
        "r5_suite_counts": suite_counts,
        "r5_no_negative_rows": len(no_negative),
        "r5_no_negative_frama_c_pass": sum(float(row["binary_frama_c_validation"]) == 1.0 for row in no_negative),
        "r5_exact_five_reused_call_artifacts": artifacts_ok,
        "r5_prompt_hash_consistent_artifacts": prompt_hashes_ok,
        "r5_complete_r10_prefix_source_artifacts": source_artifacts_ok,
        "r5_api_call_counts": sorted({int(row["api_call_count"]) for row in r5_rows}),
        "r5_rollout_counts": sorted({int(row["n_rollouts"]) for row in r5_rows}),
        "r5_reroll_counts": sorted({int(row["reroll_count"]) for row in r5_rows}),
        "r5_max_rerolls": sorted({int(row["max_rerolls"]) for row in r5_rows}),
        "r5_fresh_api_calls": sum(int(row["fresh_api_call_count"]) for row in r5_rows),
        "r5_reused_api_calls": sum(int(row["reused_api_call_count"]) for row in r5_rows),
        "comparison_table": str((root / "comparison_table_9methods.md").resolve()),
        "r5_summary": str((root / "r5_summary.json").resolve()),
    }
    (root / "completion_audit_9methods.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(audit, indent=2, sort_keys=True))
    return 0 if (
        audit["complete"]
        and artifacts_ok == prompt_hashes_ok == source_artifacts_ok == 832
        and not audit["r4_in_final_table"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
