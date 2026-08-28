#!/usr/bin/env python3
"""Regenerate the body of tab:cross-model-main from results/*/grid_summary.json.

All rows (k = 1, 4, 8) come from one archived rollout pool per model:
compose@k = prefix-subset composition counts, pass@k = the unbiased estimator.
The script replaces the block between the table's \\midrule and \\bottomrule
in paper/sections/appendix.tex in place.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

MODELS = [
    ("GPT-5-nano", "gpt5nano_tools_no_reasoning_cap8192"),
    ("GPT-5", "gpt5_full832_r10_no_reasoning"),
    # GPT-5-mini has no true reasoning-off setting (archives ran at
    # reasoning_effort=minimal, ~26k completion tokens/task) and is excluded
    # from the reasoning-off table.
    ("Claude Sonnet-4.6", "claude_sonnet_4_6_full832_r10_no_thinking"),
    ("DeepSeek V4-Flash", "deepseek_v4_flash_full832_r10_no_thinking_v2"),
]
SUITES = [("linear", 316), ("NLA_lipus", 50), ("Loopy", 466), ("all", 832)]
KS = ("1", "4", "8")


def cell(count, denom, best, decimals=2) -> str:
    value = 100 * count / denom
    rendered = f"\\acc{{{round(count, 1)}/{denom}}}{{{value:.{decimals}f}}}"
    return f"\\textbf{{{rendered}}}" if abs(value - best) < 1e-9 else rendered


def row_values(summary: dict, k: str) -> list[float]:
    comp = summary["compose"][k]
    pas = summary["pass_estimate"][k]
    values = []
    for suite, denom in SUITES:
        key = suite if suite != "all" else "all"
        values.extend((100 * pas.get(key, 0.0) / denom,
                       100 * comp.get(key, 0) / denom))
    return values


def block(name: str, summary: dict, best: dict) -> str:
    lines = []
    for row_idx, k in enumerate(KS):
        comp = summary["compose"][k]
        pas = summary["pass_estimate"][k]
        cells = []
        for suite_idx, (suite, denom) in enumerate(SUITES):
            key = suite if suite != "all" else "all"
            pass_count = pas.get(key, 0.0)
            comp_count = comp.get(key, 0)
            cells.append(
                f"{cell(pass_count, denom, best[k][2 * suite_idx])} & "
                f"{cell(comp_count, denom, best[k][2 * suite_idx + 1])}"
            )
        model = f"\\multirow{{{len(KS)}}}{{*}}{{{name}}}" if row_idx == 0 else ""
        lines.append(model + " & " + k + "\n  & " + "\n  & ".join(cells) + " \\\\")
    return "\n".join(lines)


def main() -> None:
    loaded = []
    for name, run in MODELS:
        path = ROOT / "results" / run / "grid_summary.json"
        if not path.is_file():
            path = ROOT / "paper" / "artifacts" / "v4" / "grid_summaries" / f"{run}.json"
        if not path.is_file():
            print(f"skip {name}: {path} missing", file=sys.stderr)
            continue
        summary = json.loads(path.read_text())
        if not summary.get("pass_rows"):
            print(f"note {name}: no per-rollout verdicts; pass rows are zero", file=sys.stderr)
        loaded.append((name, summary))
    best = {
        k: [max(row_values(summary, k)[i] for _, summary in loaded)
            for i in range(8)]
        for k in KS
    }
    blocks = [block(name, summary, best) for name, summary in loaded]
    body = "\n\\cmidrule(l{3pt}r{3pt}){1-10}\n".join(blocks)

    tex = ROOT / "paper" / "sections" / "appendix.tex"
    s = tex.read_text()
    pattern = re.compile(
        r"(\\label\{tab:cross-model-main\}.*?\\midrule\n).*?(\n\\bottomrule)", re.DOTALL
    )
    match = pattern.search(s)
    assert match, "cross-model table not found"
    s = s[: match.end(1)] + body + s[match.start(2):]
    tex.write_text(s)
    print(f"wrote {len(blocks)} model blocks")


if __name__ == "__main__":
    main()
