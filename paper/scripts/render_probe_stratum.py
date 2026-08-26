#!/usr/bin/env python3
"""Render the per-stratum base-probe rows from paper/artifacts/v4/probe_stratum_grid.json.

Fills, in place:
  * the body of tab:probe-stratum (appendix.tex): k=1 and k=8 rows per base
    checkpoint, best value per column within each budget block in bold;
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
TABLE_KS = (1, 8)


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


def render_probe_stratum(ks, base, agg) -> str:
    # Column order per k: (pass, comp) x (linear, NLA, Loopy, all).
    per_model = {
        name: {k: [x for p, c in zip(cells(m, "pass", ks, k, agg[name]["pass"][k]),
                                     cells(m, "compose", ks, k, agg[name]["compose"][k]))
                   for x in (p, c)]
               for k in TABLE_KS}
        for name, m in base.items()
    }
    best = {k: [max(per_model[n][k][i] for n in per_model) for i in range(8)]
            for k in TABLE_KS}
    blocks = []
    for name, row in per_model.items():
        lines = [f"\\multicolumn{{9}}{{l}}{{\\textit{{{name}}}}} \\\\"]
        for k in TABLE_KS:
            vals = " & ".join(fmt(v, v == best[k][i]) for i, v in enumerate(row[k]))
            lines.append(f"{k}\n  & {vals} \\\\")
        blocks.append("\n".join(lines))
    return "\n\\cmidrule(l{3pt}r{3pt}){1-9}\n".join(blocks)


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


def check_aggregates(text: str, ks, sizes, base, tol: float = 0.15) -> dict:
    """Whole-set values must reproduce tab:probe-complete (already in the tex).

    Returns {model: {metric: {k: printed value}}}; the printed aggregates come
    from raw counts and are used for the All column instead of re-deriving
    them from one-decimal stratum values.
    """
    printed_all = {}
    for name, m in base.items():
        rows = re.findall(re.escape(name) + r" & ((?:[\d.]+\\% & )+[\d.]+\\%)", text)
        assert len(rows) >= 2, f"{name}: expected pass and compose rows in tab:probe-complete"
        for metric, row in zip(("pass", "compose"), rows[:2]):
            printed = [float(x) for x in re.findall(r"([\d.]+)\\%", row)]
            for k, want in zip((1, 4, 8, 16, 32), printed):
                got = whole_set(m[metric], sizes, ks.index(k))
                if abs(got - want) > tol:
                    sys.exit(f"{name} {metric}@{k}: stratum mean {got:.2f} vs table {want}")
            printed_all.setdefault(name, {})[metric] = dict(zip((1, 4, 8, 16, 32), printed))
    print("whole-set aggregates reproduce tab:probe-complete")
    return printed_all


def main() -> None:
    ks, sizes, base = load()
    appendix = APPENDIX.read_text()
    agg = check_aggregates(appendix, ks, sizes, base)
    appendix = replace_between(appendix, "tab:probe-stratum", render_probe_stratum(ks, base, agg))
    APPENDIX.write_text(appendix)
    EXPERIMENTS.write_text(fill_rcf_paired(EXPERIMENTS.read_text(), ks, base, agg))
    print("rendered tab:probe-stratum and tab:rcf-paired bare/+pipeline rows")


if __name__ == "__main__":
    main()
