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
    ("GPT-5.6-luna", "gpt56luna_full832_r8"),
    ("Claude Sonnet-4.6", "claude_sonnet_4_6_full832_r10_no_thinking"),
    ("DeepSeek V4-Flash", "deepseek_v4_flash_full832_r10_no_thinking_v2"),
]
SUITES = [("linear", 316), ("NLA_lipus", 50), ("Loopy", 466), ("all", 832)]
KS = ("1", "4", "8")


def cell(count, denom, decimals=2) -> str:
    return f"\\acc{{{round(count, 1)}/{denom}}}{{{100 * count / denom:.{decimals}f}}}"


def block(name: str, summary: dict) -> str:
    lines = [f"\\multicolumn{{9}}{{l}}{{\\textit{{{name}}}}} \\\\"]
    for k in KS:
        comp = summary["compose"][k]
        pas = summary["pass_estimate"][k]
        cells = []
        for suite, denom in SUITES:
            pass_count = pas.get(suite if suite != "all" else "all", 0.0)
            comp_count = comp.get(suite if suite != "all" else "all", 0)
            cells.append(f"{cell(pass_count, denom)} & {cell(comp_count, denom)}")
        lines.append(k + "\n  & " + "\n  & ".join(cells) + " \\\\")
    return "\n".join(lines)


def main() -> None:
    blocks = []
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
        blocks.append(block(name, summary))
    body = "\n\\cmidrule(l{3pt}r{3pt}){1-9}\n".join(blocks)

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
