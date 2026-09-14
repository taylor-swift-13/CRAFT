#!/usr/bin/env python3
"""Export the current appendix values for the main table and experiment plots.

Run: python3 paper/scripts/prepare_experiment_results.py paper
The original evaluation report remains immutable; raw-results appendix incorporates
the author's subsequent reward-name and aggregate-score corrections.
"""
import argparse
import json
from pathlib import Path
import re

MODELS = ["Qwen3-4B", "Qwen3-8B", "Qwen3-14B", "Llama 3.1-8B"]
PROBE_MODELS = ["Qwen3-1.7B", *MODELS[:3], "Qwen3-30B-A3B", MODELS[3]]
K = [1, 4, 8, 16, 32]


def table_at(source, label):
    start = source.index(r"\label{" + label + "}")
    return source[start:source.index(r"\end{table}", start)]


def plain(text):
    text = re.sub(r"\\acc\{[^{}]*\}\{([^{}]*)\}", r"\1", text)
    return re.sub(r"\\textbf\{([^{}]*)\}", r"\1", text)


def stage_records(table, models=MODELS):
    records = {}
    model = None
    for line in table.splitlines():
        if r"\multirow{5}" in line:
            model = next((m for m in models if m in line), None)
            if model:
                records[model] = {"k": [], "pass": [], "compose": [],
                                  "source_label": line.strip()}
        elif model and re.match(r"\s*&\s*\d+\s*&", line):
            cells = plain(line).split("&")
            assert len(cells) == 10
            records[model]["k"].append(int(cells[1]))
            for metric, cell in zip(("pass", "compose"), cells[-2:]):
                records[model][metric].append(float(re.search(r"\d+\.\d+", cell)[0]))
    assert set(records) == set(models)
    assert all(r["k"] == K for r in records.values())
    return records


def api_records(table):
    records = {}
    blocks = re.split(r"\\multirow\{3\}\{\*\}\{([^{}]+)\}", table)
    for i in range(1, len(blocks), 2):
        name, block = blocks[i:i + 2]
        records[name] = {"k": [], "pass": [], "compose": []}
        for row in block.split(r"\\")[:3]:
            cells = plain(row).split("&")
            assert len(cells) == 10, (name, cells)
            records[name]["k"].append(int(cells[1]))
            for metric, cell in zip(("pass", "compose"), cells[-2:]):
                records[name][metric].append(float(cell.strip()))
    assert len(records) == 4
    assert all(r["k"] == [1, 4, 8] for r in records.values())
    return records


def reward_records(table):
    result = {"Bare": {}, "SFT": {}}
    initialization = None
    for line in table.splitlines():
        if r"\multicolumn{11}" in line:
            initialization = ("Bare" if "Qwen3-8B, RL directly from Bare" in line else
                              "SFT" if "Qwen3-8B, RL after SFT" in line else None)
        elif initialization and re.match(r"^(Binary|Whole-rollout|Clause-decomposed|Full)\b", line):
            cells = plain(line).split("&")
            name = cells[0].strip().replace(" (default)", "")
            values = [float(re.search(r"\d+\.\d+", cell)[0]) for cell in cells[1:]]
            assert len(values) == 10
            result[initialization][name] = {"pass": values[:5], "compose": values[5:]}
    return result


def render_main_table(data):
    rows = []
    for model in MODELS:
        for stage in ("Bare", "SFT", "RL", "SFT+RL"):
            r = data["stages"][stage][model]
            rows.append((model, stage, [r[m][i] for i in (0, 2) for m in ("pass", "compose")]))
    for model, r in data["api"].items():
        rows.append((model, "--", [r[m][i] for i in (0, 2) for m in ("pass", "compose")]))
    best = [max(row[2][i] for row in rows) for i in range(4)]
    output = [r"% Generated from the current raw-results appendix and cross-model table.",
              r"\begin{table}[!t]", r"\centering", r"\small",
              r"\begin{tabular}{llrrrr}", r"\toprule", r"\rowcolor{algobg}",
              r"& & \multicolumn{2}{c}{\(k=1\)} & \multicolumn{2}{c}{\(k=8\)} \\",
              r"\headercmidrules{\cmidrule(lr){3-4}\cmidrule(lr){5-6}}",
              r"\rowcolor{algobg}",
              r"\multirow{-2}{*}{Backbone} & \multirow{-2}{*}{Training} & pass & compose & pass & compose \\",
              r"\midrule"]
    for index, (model, stage, values) in enumerate(rows):
        if index < 16:
            if index % 4 == 0:
                if index:
                    output.append(r"\cmidrule(lr){1-6}")
                output.append(r"\multirow{4}{*}{" + model + "}")
            prefix = " & " + stage
            if stage == "SFT+RL":
                prefix += r" (\sam{})"
        else:
            if index == 16:
                output += [r"\midrule", r"\multicolumn{6}{l}{\textit{API models}} \\"]
            prefix = model + " & --"
        cells = [r"\textbf{" + f"{v:.2f}" + "}" if v == best[i] else f"{v:.2f}"
                 for i, v in enumerate(values)]
        output.append(prefix + " & " + " & ".join(cells) + r" \\")
    output += [r"\bottomrule", r"\end{tabular}",
               r"\caption{Main results on all 832 programs (\%).  Pass evaluates complete",
               r"responses; compose verifies the filtered union of candidate clauses.",
               r"Pass values are nearest-integer projections of reported rates; Appendix~\ref{app:experimental-protocol} details the aggregation.",
               r"Bold marks the highest value in each column across all rows, including",
               r"API baselines.  RL denotes training directly from Bare; SFT+RL starts",
               r"from the SFT model.  API models receive no additional training."]
    output += [r"Complete local-model curves are in Appendix~\ref{app:complete-results};",
               r"API breakdowns are in Table~\ref{tab:cross-model-main}.}",
               r"\label{tab:rcf-paired}", r"\end{table}"]
    return "\n".join(output) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paper", type=Path)
    root = parser.parse_args().paper.resolve()
    source = (root / "sections/appendix.tex").read_text()
    labels = {"Bare": "tab:probe-complete", "SFT": "tab:sft-probe-complete",
              "RL": "tab:rlzero-additional", "SFT+RL": "tab:rl-complete"}
    data = {"version": root.name, "default_reward": "Full", "k": K, "population": 832,
            "source": "Current raw-results appendix (including author corrections) and independent-experiments appendix cross-model results",
            "stage_source_tables": labels,
            "stages": {stage: stage_records(table_at(source, label), PROBE_MODELS if stage == "Bare" else MODELS) for stage, label in labels.items()},
            "api": api_records(table_at(source, "tab:cross-model-main")),
            "reward_source_table": "tab:reward-ablation",
            "rewards": reward_records(table_at(source, "tab:reward-ablation"))}
    data["pass_display"] = "Nearest integer-count projection of reported percentages; not recomputed observed counts. See pass_count_projection.json."
    assert data["rewards"]["Bare"]["Binary"]["compose"] == [4.55] * 5
    expected = {"Binary", "Whole-rollout", "Clause-decomposed"}
    if data["default_reward"] == "Full":
        expected.add("Full")
    assert set(data["rewards"]["Bare"]) == expected
    data["reward_comparison_current"] = {
        "Bare": {r: data["rewards"]["Bare"][r] for r in ("Clause-decomposed", "Full")},
        "SFT": {
            "Clause-decomposed": data["rewards"]["SFT"]["Clause-decomposed"],
            "Full": data["rewards"]["SFT"]["Full"],
        },
    }
    assert data["stages"]["SFT+RL"]["Qwen3-8B"]["compose"][0] == 69.23
    assert data["rewards"]["SFT"]["Clause-decomposed"]["compose"][0] == 67.91
    assert data["reward_comparison_current"]["SFT"]["Full"]["compose"][0] == 69.23
    (root / "artifacts/experiment_results_current.json").write_text(json.dumps(data, indent=2) + "\n")
    (root / "sections/main_results_table.tex").write_text(render_main_table(data))
    print(root.name, "exported 16 local model rows, 4 API rows, and current reward curves")


if __name__ == "__main__":
    main()
