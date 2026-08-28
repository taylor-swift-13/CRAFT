#!/usr/bin/env python3
"""Render the per-stratum base-probe rows from paper/artifacts/v4/probe_stratum_grid.json.

Fills, in place:
  * tab:probe-complete: one unified table with per-stratum and whole-workload
    pass/compose results at k=1,4,8,16,32, plus k_95;
  * the "bare (pass)" and "+pipeline (compose)" rows of tab:rcf-paired
    (experiments.tex) for every checkpoint the table lists.

It also checks that the 316/50/466-weighted whole-set values reproduce the
aggregates already printed in tab:probe-complete.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GRID = ROOT / "paper" / "artifacts" / "v4" / "probe_stratum_grid.json"
APPENDIX = ROOT / "paper" / "sections" / "appendix.tex"
EXPERIMENTS = ROOT / "paper" / "sections" / "experiments.tex"

STRATA = ("linear", "NLA", "Loopy")
REPORT_KS = (1, 4, 8, 16, 32)

# Canonical whole-workload values printed in the paper.  These come from the
# unrounded per-program aggregates; weighting the one-decimal stratum values
# in the JSON reproduces them to within 0.1 point.
OVERALL = {
    "Qwen3-1.7B": {
        "pass": {1: 7.8, 4: 15.0, 8: 18.8, 16: 22.4, 32: 26.0},
        "compose": {1: 22.7, 4: 33.1, 8: 36.8, 16: 39.3, 32: 41.4},
        "k95": 32,
    },
    "Qwen3-4B": {
        "pass": {1: 6.4, 4: 11.6, 8: 14.1, 16: 16.5, 32: 19.1},
        "compose": {1: 38.3, 4: 46.3, 8: 51.2, 16: 55.0, 32: 57.0},
        "k95": 16,
    },
    "Qwen3-8B": {
        "pass": {1: 8.3, 4: 16.5, 8: 20.5, 16: 24.4, 32: 28.1},
        "compose": {1: 43.8, 4: 55.2, 8: 59.0, 16: 61.2, 32: 62.5},
        "k95": 16,
    },
    "Qwen3-14B": {
        "pass": {1: 10.1, 4: 17.5, 8: 21.3, 16: 25.2, 32: 29.1},
        "compose": {1: 52.5, 4: 62.1, 8: 65.0, 16: 66.2, 32: 67.8},
        "k95": 8,
    },
    "Qwen3-30B-A3B": {
        "pass": {1: 9.7, 4: 16.9, 8: 20.1, 16: 23.6, 32: 27.2},
        "compose": {1: 48.1, 4: 58.8, 8: 61.3, 16: 62.4, 32: 62.6},
        "k95": 8,
    },
    "Llama 3.1 8B": {
        "pass": {1: 0.8, 4: 3.0, 8: 5.4, 16: 8.9, 32: 13.8},
        "compose": {1: 29.2, 4: 48.3, 8: 54.6, 16: 56.0, 32: 56.1},
        "k95": 8,
    },
}


def load():
    doc = json.loads(GRID.read_text())
    return doc["k_grid"], doc["strata"], doc["base_probes"]


def whole_set(values: dict, sizes: dict, idx: int) -> float:
    total = sum(sizes.values())
    return sum(sizes[s] * values[s][idx] for s in STRATA) / total


def cells(model: dict, metric: str, ks: list, k: int, all_value: float) -> list[float]:
    """[linear, NLA, Loopy, all] at budget k."""
    idx = ks.index(k)
    return [model[metric][s][idx] for s in STRATA] + [all_value]


def fmt(v: float, bold: bool = False) -> str:
    s = f"{v:.1f}"
    return f"\\textbf{{{s}}}" if bold else s


def replace_between(text: str, label: str, body: str) -> str:
    pattern = re.compile(
        r"(\\label\{" + re.escape(label) + r"\}.*?\\midrule\n).*?(\n\\bottomrule)",
        re.DOTALL,
    )
    match = pattern.search(text)
    assert match, f"{label} not found"
    return text[: match.end(1)] + body + text[match.start(2):]


def render_probe_table(ks, base, agg) -> str:
    # Column order per k: (pass, comp) x (linear, NLA, Loopy, all).
    per_model = {
        name: {k: [x for p, c in zip(cells(m, "pass", ks, k, agg[name]["pass"][k]),
                                     cells(m, "compose", ks, k, agg[name]["compose"][k]))
                   for x in (p, c)]
               for k in REPORT_KS}
        for name, m in base.items()
    }
    best = {k: [max(per_model[n][k][i] for n in per_model) for i in range(8)]
            for k in REPORT_KS}
    blocks = []
    for name, row in per_model.items():
        lines = []
        for row_idx, k in enumerate(REPORT_KS):
            vals = " & ".join(fmt(v, v == best[k][i]) for i, v in enumerate(row[k]))
            model = (
                f"\\multirow{{{len(REPORT_KS)}}}{{*}}{{{name}}}"
                if row_idx == 0 else ""
            )
            k95 = (
                f"\\multirow{{{len(REPORT_KS)}}}{{*}}{{{agg[name]['k95']}}}"
                if row_idx == 0 else ""
            )
            lines.append(f"{model} & {k} & {vals} & {k95} \\\\")
        blocks.append("\n".join(lines))
    return "\n\\cmidrule(l{3pt}r{3pt}){1-11}\n".join(blocks)


def fill_rcf_paired(text: str, ks, base, agg) -> str:
    start = text.index("\\label{tab:rcf-paired}")
    table_start = text.rfind("\\begin{table}", 0, start)
    table = text[table_start:start]
    for name, m in base.items():
        head = f"\\multicolumn{{9}}{{l}}{{\\textit{{{name}}}}} \\\\\n"
        if head not in table:
            continue
        pos = table.index(head)
        end = table.find("\\cmidrule", pos)
        end = len(table) if end == -1 else end
        block = table[pos:end]
        for row_label, metric in (("\\quad bare (pass)", "pass"),
                                  ("\\quad +pipeline (compose)", "compose")):
            if row_label + "\n" not in block:
                continue
            vals = [x for at_k1, at_k8 in zip(cells(m, metric, ks, 1, agg[name][metric][1]),
                                              cells(m, metric, ks, 8, agg[name][metric][8]))
                    for x in (at_k1, at_k8)]
            new_row = row_label + "\n  & " + " & ".join(fmt(v) for v in vals) + " \\\\"
            block = re.sub(re.escape(row_label) + r"\n  & [^\n]*\\\\", lambda _: new_row, block, count=1)
        table = table[:pos] + block + table[end:]
    return text[:table_start] + table + text[start:]


def check_aggregates(ks, sizes, base, tol: float = 0.15) -> dict:
    """One-decimal stratum means must reproduce canonical whole-set values."""
    for name, m in base.items():
        for metric in ("pass", "compose"):
            for k in REPORT_KS:
                want = OVERALL[name][metric][k]
                got = whole_set(m[metric], sizes, ks.index(k))
                if abs(got - want) > tol:
                    sys.exit(f"{name} {metric}@{k}: stratum mean {got:.2f} vs table {want}")
    print("whole-set aggregates reproduce tab:probe-complete")
    return OVERALL


def main() -> None:
    ks, sizes, base = load()
    appendix = APPENDIX.read_text()
    agg = check_aggregates(ks, sizes, base)
    appendix = replace_between(appendix, "tab:probe-complete", render_probe_table(ks, base, agg))
    APPENDIX.write_text(appendix)
    EXPERIMENTS.write_text(fill_rcf_paired(EXPERIMENTS.read_text(), ks, base, agg))
    print("rendered tab:probe-stratum and tab:rcf-paired bare/+pipeline rows")


if __name__ == "__main__":
    main()
