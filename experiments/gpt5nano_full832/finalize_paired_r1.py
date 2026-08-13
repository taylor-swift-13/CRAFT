"""Write compact CSV and Markdown tables for paired pass@1/combine@1."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, required=True)
    args = parser.parse_args()

    root = args.results_root
    summary = json.loads((root / "paired_pass1_combine1_summary.json").read_text())
    outcomes = summary["paired_outcomes"]
    rows = []
    for mode, count_key, time_key in (
        ("pass@1", "pass_at_1", "mean_pass_seconds"),
        ("combine@1", "combine_at_1", "mean_combine_seconds"),
    ):
        rows.append({
            "model": summary["model"],
            "mode": mode,
            "linear": summary["by_suite"]["linear"][count_key],
            "NLA_lipus": summary["by_suite"]["NLA_lipus"][count_key],
            "Loopy": summary["by_suite"]["Loopy"][count_key],
            "verified": summary[count_key],
            "total_tasks": summary["rows"],
            "verified_rate": summary[f"{count_key}_rate"],
            "mean_pipeline_seconds": summary[time_key],
            "mean_total_tokens": summary["mean_total_tokens_both"],
            "total_tokens": summary["total_tokens"],
            "token_accounting": "exact_api_usage",
            "filter_gain": outcomes["filter_gain"],
            "filter_regression": outcomes["filter_regression"],
        })

    csv_path = root / "paired_pass1_combine1_table.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        f"# {summary['model']} paired pass@1 / combine@1",
        "",
        "Both modes reuse the same single rollout, so their exact model-token "
        "cost is identical. Time is the complete per-task pipeline time.",
        "",
        "| Mode | Linear / 316 | NLA / 50 | Loopy / 466 | All / 832 | "
        "Mean time | Mean tokens |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['mode']} | {row['linear']} | {row['NLA_lipus']} | "
            f"{row['Loopy']} | {row['verified']} "
            f"({100 * row['verified_rate']:.2f}%) | "
            f"{row['mean_pipeline_seconds']:.2f} s | "
            f"{row['mean_total_tokens']:,.2f} |"
        )
    lines.extend([
        "",
        f"Paired outcomes: {outcomes['filter_gain']} combine gains, "
        f"{outcomes['both_verified']} verified by both, and "
        f"{outcomes['neither_verified']} verified by neither.",
        "",
    ])
    md_path = root / "paired_pass1_combine1_table.md"
    md_path.write_text("\n".join(lines))
    print(csv_path)
    print(md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
