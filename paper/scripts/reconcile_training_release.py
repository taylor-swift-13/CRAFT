#!/usr/bin/env python3
"""Append authoritative released-dataset facts to the sanitation artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def atomic_json(path: Path, value) -> None:
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
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "paper" / "artifacts" / "training_sanitation.json",
    )
    parser.add_argument(
        "--rl", type=Path, default=ROOT / "traindata" / "craft_rl_clean.parquet"
    )
    parser.add_argument(
        "--sft", type=Path, default=ROOT / "traindata" / "craft_sft_clean.json"
    )
    parser.add_argument(
        "--power-audit",
        type=Path,
        default=ROOT / "paper" / "artifacts" / "power_rewrite_audit.json",
    )
    parser.add_argument(
        "--verification-report",
        type=Path,
        help="report from the final clean-to-clean --verify-sft run",
    )
    parser.add_argument(
        "--guarded-copies-removed",
        type=int,
        help="count printed by the explicit power-review pruning run",
    )
    parser.add_argument(
        "--fixed-point-report",
        type=Path,
        help="report from a final clean-to-clean static check",
    )
    parser.add_argument(
        "--enhancement-report",
        type=Path,
        help="static clean-to-clean report that proposed SFT enhancements",
    )
    parser.add_argument(
        "--final-cleanup-report",
        type=Path,
        help="static report for any final removal-only cleanup after WP",
    )
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    records = json.loads(args.sft.read_text(encoding="utf-8"))
    power_audit = json.loads(args.power_audit.read_text(encoding="utf-8"))
    answers = [
        next(turn["value"] for turn in row["conversations"] if turn["from"] == "gpt")
        for row in records
    ]
    verification = None
    if args.verification_report is not None:
        verification = json.loads(args.verification_report.read_text(encoding="utf-8"))
    fixed_point = None
    if args.fixed_point_report is not None:
        fixed_point = json.loads(args.fixed_point_report.read_text(encoding="utf-8"))
    enhancement = None
    if args.enhancement_report is not None:
        enhancement = json.loads(args.enhancement_report.read_text(encoding="utf-8"))
    final_cleanup = None
    if args.final_cleanup_report is not None:
        final_cleanup = json.loads(args.final_cleanup_report.read_text(encoding="utf-8"))
    proposed_relations = (
        enhancement["sft"]["transformations"].get(
            "synchronous_relations_proposed", 0
        )
        if enhancement is not None
        else 0
    )
    verifier_rejections = (
        verification["sft"]["removed_clauses"].get("frama_c_rejected", 0)
        if verification is not None
        else 0
    )
    reducible_rejected = sum(
        relation.get("quality_reason") == "reducible_product_equality"
        for row in power_audit["rows"]
        for relation in row["derived_relations"]
    )
    loopentry_pattern = re.compile(
        r"\\at\(\s*([A-Za-z_]\w*)\s*,\s*LoopEntry\s*\)"
    )
    loopentry_answers = [answer for answer in answers if loopentry_pattern.search(answer)]
    loopentry_clauses = [
        line
        for answer in answers
        for line in answer.splitlines()
        if loopentry_pattern.search(line)
    ]

    report["final_release"] = {
        "authoritative": True,
        "rl": {
            "path": display_path(args.rl),
            "sha256": sha256(args.rl),
            "rows": pq.read_metadata(args.rl).num_rows,
        },
        "sft": {
            "path": display_path(args.sft),
            "sha256": sha256(args.sft),
            "rows": len(records),
            "clauses": sum(answer.count("loop invariant ") for answer in answers),
            "empty_answers": sum(not answer.strip() for answer in answers),
            "helper_call_answers": sum(
                "power(" in answer or "factorial(" in answer for answer in answers
            ),
            "max_clauses_per_answer": max(
                (answer.count("loop invariant ") for answer in answers), default=0
            ),
        },
        "quality_cleanup": {
            "guarded_copies_removed": args.guarded_copies_removed,
            "reducible_power_candidates_rejected": reducible_rejected,
            "post_cleanup_frama_c_rejected": (
                verification["sft"]["removed_clauses"].get("frama_c_rejected", 0)
                if verification is not None
                else None
            ),
            "frama_c_errors": (
                verification["sft"]["frama_c_errors"]
                if verification is not None
                else None
            ),
            "wp_timeout_per_obligation_seconds": 5,
            "loopentry_policy": (
                "only locals whose final pre-loop assignment is directly unknown*()"
            ),
            "loopentry_answers": len(loopentry_answers),
            "loopentry_clauses": len(loopentry_clauses),
            "loopentry_references": sum(
                len(loopentry_pattern.findall(clause)) for clause in loopentry_clauses
            ),
            "interface_violations": (
                verification["sft"]["output_answer_violations"]
                if verification is not None
                else None
            ),
        },
        "power_rewrite_summary": power_audit["summary"],
        "fixed_point": (
            {
                "rl_modified_prompts": fixed_point["rl"]["modified_prompts"],
                "sft_modified_prompts": fixed_point["sft"]["modified_prompts"],
                "sft_modified_answers": fixed_point["sft"]["modified_answers"],
                "sft_removed_clauses": fixed_point["sft"]["removed_clauses"],
            }
            if fixed_point is not None
            else None
        ),
        "sft_enhancement": (
            {
                "input_clauses": enhancement["sft"]["clauses_before"],
                "static_output_clauses": enhancement["sft"]["clauses_after"],
                "final_verified_clauses": sum(
                    answer.count("loop invariant ") for answer in answers
                ),
                "modified_answers": enhancement["sft"]["modified_answers"],
                "removed_clauses": enhancement["sft"]["removed_clauses"],
                "transformations": enhancement["sft"]["transformations"],
                "frama_c_rejected_candidates": (
                    verifier_rejections
                    if verification is not None
                    else None
                ),
                "synchronous_relations_retained": (
                    proposed_relations - verifier_rejections
                    if verification is not None
                    else None
                ),
                "post_wp_removal_only_cleanup": (
                    {
                        "modified_answers": final_cleanup["sft"]["modified_answers"],
                        "removed_clauses": final_cleanup["sft"]["removed_clauses"],
                    }
                    if final_cleanup is not None
                    else None
                ),
            }
            if enhancement is not None
            else None
        ),
        "note": (
            "The top-level sanitation statistics record the precursor archive-to-clean "
            "pass. This final_release object is authoritative for the tracked artifacts."
        ),
    }
    atomic_json(args.report, report)
    print(json.dumps(report["final_release"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
