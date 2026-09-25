"""Audit CRAFT's archived Loopy/202 clauses against several hidden goals."""

import csv
import json
import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
RESULTS = ROOT / "results/02_rq2_tool_comparison/gpt5nano_full832"
SOURCE = ROOT / "src/input/Loopy/202.c"
BASELINES = ("autospec", "sespec", "naive", "daikon", "clause2inv")
GOALS = {
    "original_equal": "i == j",
    "sum_conservation": "i + j == 2 * \\at(j,Pre) + n",
    "i_half_progress": "i == \\at(j,Pre) + n / 2",
    "j_half_progress": "j == \\at(j,Pre) + n / 2",
}


def archived_verdicts():
    with (RESULTS / "final_results_9methods.csv").open() as file:
        return {
            row["method"]: row["verified"] == "True"
            for row in csv.DictReader(file)
            if row["suite"] == "Loopy" and row["case_id"] == "202"
        }


def craft_clauses():
    with (RESULTS / "r10_results.csv").open() as file:
        row = next(
            row for row in csv.DictReader(file)
            if row["suite"] == "Loopy" and row["case_id"] == "202"
        )
    assert row["verified"] == "True" and row["target_hidden"] == "True"
    prefix = json.loads(row["rollouts"])[:8]
    assert len(prefix) == 8
    eight_result = None
    with (RESULTS / "grid_recompute.jsonl").open() as file:
        for line in file:
            item = json.loads(line)
            if (item.get("suite"), item.get("case_id"), item.get("k")) == (
                "Loopy", "202", 8
            ):
                eight_result = item
    assert eight_result is not None and eight_result["verified"] is True
    # The four clauses below occur in the verified eight-response prefix.
    selected = [
        "0 <= n <= 2*k",
        "(i + j) == (n + 2 * \\at(j,Pre))",
        "((n % 2) == 0) ==> (i == j)",
        "(n % 2 == 1) ==> (((b == 0) && (i - j == 1)) || ((b == 1) && (i - j == -1)))",
    ]
    assert all(any(clause in rollout for rollout in prefix) for clause in selected)
    return selected


def baseline_clauses(method):
    matches = []
    path = RESULTS / "latest.jsonl"
    if method == "daikon":
        path = RESULTS / "events/daikon.jsonl"
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if (row.get("method"), row.get("suite"), row.get("case_id")) == (
            method, "Loopy", "202"
        ):
            matches.append(row)
    assert matches, method
    return matches[-1].get("invariants") or []


def annotate(source, clauses, goals):
    annotation = "/*@\n" + "\n".join(
        f"    loop invariant {clause};" for clause in clauses
    ) + "\n  */\n  "
    annotated = source.replace("while (n < 2*k)", annotation + "while (n < 2*k)", 1)
    assertions = "\n".join(
        f"//@ assert {name}: {goal};" for name, goal in goals.items()
    )
    return annotated.replace("//@ assert(i == j);", assertions, 1)


def run_wp(stem, clauses, goals, frama_c):
    file = HERE / f"{stem}.c"
    file.write_text(annotate(SOURCE.read_text(), clauses, goals))
    command = [
        frama_c, "-wp", "-wp-print", "-wp-timeout", "5",
        "-wp-prover", "alt-ergo,z3", "-wp-model", "Typed",
        "-wp-prop=-@terminates,-missing_return", str(file),
    ]
    check = subprocess.run(command, capture_output=True, text=True, timeout=90)
    output = check.stdout + check.stderr
    (HERE / f"{stem}.log").write_text(output)
    summary = re.findall(r"Proved goals:\s*(\d+)\s*/\s*(\d+)", output)
    proved, total = map(int, summary[-1]) if summary else (0, 0)
    assertion_only_results = {}
    for name in goals:
        block = re.search(
            rf"Goal Assertion '{name}'[^\n]*\n(.*?)(?=\n-{{40,}}|\Z)",
            output, re.S,
        )
        assertion_only_results[name] = bool(
            block and re.search(r"returns Valid\b", block.group(1))
        )
    return {
        # Later assertions may assume earlier assertions. Only the separate
        # per-goal runs below provide independent full-program verdicts.
        "assertion_only_results": assertion_only_results,
        "proved_goals": proved,
        "total_goals": total,
        "all_proved": check.returncode == 0 and proved == total and all(assertion_only_results.values()),
        "returncode": check.returncode,
        "command": command,
    }


def run_method(method, clauses, frama_c):
    combined = run_wp(method, clauses, GOALS, frama_c)
    independent = {
        name: run_wp(f"{method}_{name}", clauses, {name: goal}, frama_c)
        for name, goal in GOALS.items()
    }
    return {
        "archived_clauses": clauses,
        "combined": combined,
        "independent_goal_verdicts": {
            name: result["all_proved"] for name, result in independent.items()
        },
    }


def main():
    source = SOURCE.read_text()
    assert source.count("//@ assert(i == j);") == 1
    verdicts = archived_verdicts()
    assert verdicts["loopgym_r5_houdini"] and not any(
        verdicts[method] for method in BASELINES
    )
    frama_c = os.environ.get("FRAMA_C", "frama-c")
    methods = {"craft_r8": run_method("craft_r8", craft_clauses(), frama_c)}
    for method in ("autospec", "naive", "daikon"):
        methods[method] = run_method(method, baseline_clauses(method), frama_c)
    assert methods["craft_r8"]["combined"]["all_proved"]
    assert all(methods["craft_r8"]["independent_goal_verdicts"].values())
    assert not any(methods[method]["combined"]["all_proved"] for method in ("autospec", "naive", "daikon"))
    report = {
        "source": str(SOURCE.relative_to(ROOT)),
        "generation_target_hidden": True,
        "original_goal": "i == j",
        "craft_prefix_size": 8,
        "audit_goals": GOALS,
        "archived_original_verdicts": verdicts,
        "methods": methods,
    }
    (HERE / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({name: {"all_proved": row["combined"]["all_proved"],
                             "independent_goal_verdicts": row["independent_goal_verdicts"],
                             "wp": f'{row["combined"]["proved_goals"]}/{row["combined"]["total_goals"]}'}
                      for name, row in methods.items()}, indent=2))


if __name__ == "__main__":
    main()
