#!/usr/bin/env python3
"""Revalidate and consolidate Stage-2 SFT candidates without batch failure.

Every artifact contributes one candidate alternative per row.  When a row is
present in multiple artifacts, their union is considered as an additional
alternative.  Alternatives are validated independently and sequentially by
default, so one compiler or verifier failure cannot discard completed rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from paper.scripts._curation_common import record_source_and_answer  # noqa: E402
from paper.scripts.consolidate_sft_curation import _validate  # noqa: E402
from rl_pipeline.common.state import dedup_normalized, extract_invariants  # noqa: E402


DEFAULT_SFT = ROOT / "traindata" / "craft_sft_curated_stage1.json"
DEFAULT_OUTPUT = ROOT / "traindata" / "craft_sft_curated_stage2.json"
DEFAULT_AUDIT = ROOT / "paper" / "artifacts" / "sft_curation_stage2.json"
DEFAULT_CHECKPOINT = (
    ROOT / "paper" / "artifacts" / "sft_curation_stage2_checkpoint.jsonl"
)


def _artifact_items(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, list):
        return value
    for key in ("items", "rows"):
        if isinstance(value.get(key), list):
            return value[key]
    raise ValueError(f"{path} has no items/rows list")


def _proposal(item: dict) -> list[str] | None:
    for key in (
        "wp_survivors",
        "rechecked_wp_survivors",
        "accepted_answer",
        "merged",
    ):
        value = item.get(key)
        if isinstance(value, list) and all(isinstance(clause, str) for clause in value):
            return dedup_normalized(value)
    return None


def _key(
    row: int,
    origin: str,
    source: str,
    original: list[str],
    proposed: list[str],
    runs: int,
    seed: int,
    frama_version: str,
    required_families: list[str],
) -> str:
    payload = json.dumps(
        {
            "row": row,
            "origin": origin,
            "source": source,
            "original": original,
            "proposed": proposed,
            "runs": runs,
            "seed": seed,
            "frama_c": frama_version,
            "required_families": required_families,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _latest_checkpoint(path: Path) -> dict[str, dict]:
    latest = {}
    if not path.is_file():
        return latest
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                item = json.loads(line)
                latest[item["alternative_key"]] = item
    return latest


def _rank(result: dict) -> tuple[int, int, int, int]:
    return (
        int(result["candidate"]["rejected"]),
        len(result["added_relation_rejections"]),
        len(result["added_rejections"]),
        -len(result["accepted_answer"]),
    )


def _apply_family_decision(result: dict, required_families: list[str]) -> None:
    family_additions = {}
    for family in ("relation", "post_exit", "range", "frame"):
        before = set(result["baseline"]["families"][family]["indices"])
        after = set(result["candidate"]["families"][family]["indices"])
        family_additions[family] = sorted(after - before)
    result["added_family_rejections"] = family_additions
    required_added = any(family_additions[family] for family in required_families)
    accepted = (
        result["original_survives"]
        and not result["removed_rejections"]
        and required_added
        and len(result["wp_survivors"]) <= 20
        and not result["interface_failures"]
    )
    result["decision"] = "accept" if accepted else "reject"
    result["accepted_answer"] = (
        result["wp_survivors"] if accepted else result["original"]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batches", nargs="+", type=Path)
    parser.add_argument("--sft", type=Path, default=DEFAULT_SFT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--runs", type=int, default=12)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--required-family",
        action="append",
        choices=("relation", "post_exit", "range", "frame"),
        dest="required_families",
        help="accept when at least one listed family gains a rejection; default: relation",
    )
    args = parser.parse_args()
    required_families = args.required_families or ["relation"]

    frama_c = shutil.which("frama-c")
    if frama_c is None:
        raise RuntimeError(
            "frama-c is unavailable; activate the frama-c.31 opam switch"
        )
    version = subprocess.run(
        [frama_c, "-version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    if not version.startswith("31"):
        raise RuntimeError(f"Frama-C 31 is required, found {version!r}")

    records = json.loads(args.sft.read_text(encoding="utf-8"))
    alternatives: dict[int, list[tuple[str, list[str]]]] = defaultdict(list)
    for path in args.batches:
        if not path.is_file():
            raise FileNotFoundError(path)
        for item in _artifact_items(path):
            proposed = _proposal(item)
            if not proposed:
                continue
            alternatives[int(item["row"])].append((path.name, proposed))

    # Retried artifacts may contribute a distinct candidate for the same row.
    # Validate their union as one more combine candidate.
    for row, values in list(alternatives.items()):
        unique = {tuple(proposed) for _, proposed in values}
        if len(unique) > 1:
            union = dedup_normalized(
                [clause for _, proposed in values for clause in proposed]
            )
            values.append(("union", union))

    os.environ.setdefault("CRAFT_WP_TIMEOUT", "5")
    os.environ.setdefault("CRAFT_WP_PAR", "2")
    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    latest = _latest_checkpoint(args.checkpoint)
    results = []
    jobs = []
    for row, values in sorted(alternatives.items()):
        record = records[row]
        source, answer = record_source_and_answer(record)
        original = extract_invariants(answer)
        for origin, proposed in values:
            alternative_key = _key(
                row,
                origin,
                source,
                original,
                proposed,
                args.runs,
                args.seed,
                version,
                required_families,
            )
            if alternative_key in latest:
                results.append(latest[alternative_key])
                continue
            jobs.append((alternative_key, origin, row, source, original, proposed))

    with args.checkpoint.open("a", encoding="utf-8") as handle:
        for completed, job in enumerate(jobs, 1):
            alternative_key, origin, row, source, original, proposed = job
            try:
                result = _validate(
                    (row, source, original, proposed, args.runs, args.seed)
                )
                _apply_family_decision(result, required_families)
                result["error"] = None
            except Exception as error:
                result = {
                    "row": row,
                    "decision": "error",
                    "original": original,
                    "proposed": proposed,
                    "accepted_answer": original,
                    "error": f"{type(error).__name__}: {error}",
                }
            result["alternative_key"] = alternative_key
            result["candidate_origin"] = origin
            handle.write(json.dumps(result, sort_keys=True) + "\n")
            handle.flush()
            results.append(result)
            if completed % 10 == 0 or completed == len(jobs):
                print(f"revalidated {completed}/{len(jobs)} new alternatives", flush=True)

    by_row: dict[int, list[dict]] = defaultdict(list)
    for result in results:
        by_row[int(result["row"])].append(result)
    selected = {}
    for row, row_results in by_row.items():
        accepted = [result for result in row_results if result["decision"] == "accept"]
        if accepted:
            selected[row] = max(accepted, key=_rank)

    staged = json.loads(json.dumps(records))
    for row, result in selected.items():
        answer = "\n".join(
            f"loop invariant {clause};" for clause in result["accepted_answer"]
        )
        turn = next(
            turn
            for turn in staged[row]["conversations"]
            if turn["from"] == "gpt"
        )
        turn["value"] = answer

    report = {
        "schema_version": 1,
        "source_sft": str(args.sft),
        "staged_sft": str(args.output),
        "source_artifacts": [str(path) for path in args.batches],
        "decision_rule": (
            "all original clauses survive WP; no baseline rejection is lost; "
            f"at least one rejection is added in {required_families}; <=20 clauses; "
            "no interface violation; choose maximum rejected-negative count"
        ),
        "required_families": required_families,
        "rows_considered": len(by_row),
        "alternatives_considered": len(results),
        "rows_accepted": len(selected),
        "rows_rejected": len(by_row) - len(selected),
        "rows_error": sum(
            all(result["decision"] == "error" for result in row_results)
            for row_results in by_row.values()
        ),
        "selected": {str(row): value for row, value in sorted(selected.items())},
        "alternatives": sorted(
            results, key=lambda item: (int(item["row"]), item["candidate_origin"])
        ),
    }
    args.output.write_text(json.dumps(staged, indent=2) + "\n", encoding="utf-8")
    args.audit.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "rows_considered", "alternatives_considered", "rows_accepted",
        "rows_rejected", "rows_error"
    )}, indent=2))


if __name__ == "__main__":
    main()
