#!/usr/bin/env python3
"""Reconcile per-program power decisions with the released clean SFT file."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from paper.scripts.sanitize_training_prompts import (
    _equation_context,
    _has_nontrivial_product_factors,
    _primitive_polynomial,
    _remove_guarded_copies,
    _sympy_expression,
)


DEFAULT_AUDIT = ROOT / "paper" / "artifacts" / "power_rewrite_audit.json"
DEFAULT_SFT = ROOT / "traindata" / "craft_sft_clean.json"


def invariant_set(answer: str) -> set[str]:
    prefix = "loop invariant "
    return {
        line.strip()[len(prefix):-1].strip()
        for line in answer.splitlines()
        if line.strip().startswith(prefix) and line.strip().endswith(";")
    }


def invariant_list(answer: str) -> list[str]:
    prefix = "loop invariant "
    return [
        line.strip()[len(prefix):-1].strip()
        for line in answer.splitlines()
        if line.strip().startswith(prefix) and line.strip().endswith(";")
    ]


def relation_is_reducible(relation: dict) -> bool:
    """Reconstruct the eliminated polynomial and recognize weak products."""
    import sympy

    equations = []
    for source in relation["sources"]:
        context = _equation_context(source)
        if context is None:
            return False
        _antecedent, text, power_map = context
        parsed = _sympy_expression(text, power_map)
        if parsed is None:
            return False
        expression, powers, _at_symbols = parsed
        if len(powers) != 1:
            return False
        equations.append((expression, next(iter(powers))))
    if len(equations) != 2 or equations[0][1] != equations[1][1]:
        return False
    power = equations[0][1]
    left, right = equations[0][0], equations[1][0]
    left_coefficient = sympy.expand(left).coeff(power)
    right_coefficient = sympy.expand(right).coeff(power)
    eliminated = _primitive_polynomial(
        sympy.simplify(sympy.expand(right_coefficient * left - left_coefficient * right))
    )
    return _has_nontrivial_product_factors(eliminated)


def atomic_json_write(path: Path, value) -> None:
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(handle)
    temporary_path = Path(temporary)
    try:
        temporary_path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--sft", type=Path, default=DEFAULT_SFT)
    parser.add_argument(
        "--prune-sft",
        action="store_true",
        help="remove weak product relations and syntactically dominated guards",
    )
    args = parser.parse_args()

    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    audit["policy"] = (
        "expand fixed exponents; derive power-free polynomial relations by "
        "eliminating shared symbolic powers; reject reducible product equalities "
        "and guarded copies of unconditional conclusions; retain only Frama-C/WP "
        "Houdini survivors"
    )
    records = json.loads(args.sft.read_text(encoding="utf-8"))
    final_by_program: dict[str, set[str]] = {}
    record_by_program: dict[str, dict] = {}
    for record in records:
        human = next(
            turn["value"]
            for turn in record["conversations"]
            if turn["from"] == "human"
        )
        answer = next(
            turn["value"]
            for turn in record["conversations"]
            if turn["from"] == "gpt"
        )
        program = human.split("Program:\n", 1)[1].strip()
        final_by_program[program] = invariant_set(answer)
        record_by_program[program] = record

    reducible_by_program: dict[str, set[str]] = {}
    for row in audit["rows"]:
        for relation in row["derived_relations"]:
            reducible = relation_is_reducible(relation)
            relation["quality_decision"] = "remove" if reducible else "candidate"
            relation["quality_reason"] = (
                "reducible_product_equality" if reducible else None
            )
            if reducible:
                reducible_by_program.setdefault(row["program"].strip(), set()).add(
                    relation["clause"]
                )

    total_pruned = 0
    guarded_pruned = 0
    if args.prune_sft:
        for program, record in record_by_program.items():
            answer_turn = next(
                turn for turn in record["conversations"] if turn["from"] == "gpt"
            )
            clauses = invariant_list(answer_turn["value"])
            rejected = reducible_by_program.get(program, set())
            clauses = [clause for clause in clauses if clause not in rejected]
            clauses, removed = _remove_guarded_copies(clauses)
            guarded_pruned += removed
            total_pruned += len(invariant_list(answer_turn["value"])) - len(clauses)
            answer_turn["value"] = "\n".join(
                f"loop invariant {clause};" for clause in clauses
            )
        atomic_json_write(args.sft, records)
        final_by_program = {
            program: invariant_set(
                next(
                    turn["value"]
                    for turn in record["conversations"]
                    if turn["from"] == "gpt"
                )
            )
            for program, record in record_by_program.items()
        }

    matched = 0
    for row in audit["rows"]:
        final = final_by_program.get(row["program"].strip())
        row["final_sft_present"] = final is not None
        matched += int(final is not None)
        final = final or set()
        for decision in row["power_clauses"]:
            decision["final_retained"] = decision["rewritten"] in final
            if decision["final_retained"]:
                decision["frama_c_survived"] = True
        for relation in row["derived_relations"]:
            relation["final_retained"] = relation["clause"] in final
            if relation["final_retained"]:
                relation["frama_c_survived"] = True

    fixed = [
        decision
        for row in audit["rows"]
        for decision in row["power_clauses"]
        if decision["fixed_calls_expanded"]
    ]
    derived = [
        relation
        for row in audit["rows"]
        for relation in row["derived_relations"]
    ]
    audit["summary"] = {
        "audited_rows": len(audit["rows"]),
        "rows_present_in_final_sft": matched,
        "fixed_power_calls_expanded": sum(
            decision["fixed_calls_expanded"] for decision in fixed
        ),
        "fixed_expansion_clauses_final": sum(
            decision["final_retained"] for decision in fixed
        ),
        "rows_with_fixed_expansion_final": sum(
            any(
                decision["fixed_calls_expanded"] and decision["final_retained"]
                for decision in row["power_clauses"]
            )
            for row in audit["rows"]
        ),
        "derived_power_free_candidates": len(derived),
        "derived_power_free_relations_final": sum(
            relation["final_retained"] for relation in derived
        ),
        "rows_with_derived_relation_final": sum(
            any(relation["final_retained"] for relation in row["derived_relations"])
            for row in audit["rows"]
        ),
        "remaining_symbolic_power_clauses_removed": sum(
            row["remaining_power_clauses_removed"] for row in audit["rows"]
        ),
        "reducible_product_candidates": sum(
            relation.get("quality_reason") == "reducible_product_equality"
            for relation in derived
        ),
        "reducible_product_relations_final": sum(
            relation.get("quality_reason") == "reducible_product_equality"
            and relation["final_retained"]
            for relation in derived
        ),
    }
    atomic_json_write(args.audit, audit)
    result = dict(audit["summary"])
    if args.prune_sft:
        result["sft_clauses_pruned_in_this_run"] = total_pruned
        result["guarded_copies_pruned_in_this_run"] = guarded_pruned
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
