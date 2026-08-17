#!/usr/bin/env python3
"""Append verified, non-duplicate invariants to the curated SFT labels.

The expansion is monotone: every original clause must survive Frama-C/WP,
and accepted rows only append clauses.  Candidate clauses come from the
existing independently generated Stage 12--21 artifacts and from the small,
hand-auditable static templates used by ``strengthen_bounds_only_sft.py``.
The input file is never overwritten.
"""

from __future__ import annotations

import argparse
import copy
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from paper.scripts._curation_common import record_source_and_answer  # noqa: E402
from paper.scripts.consolidate_sft_stage2 import (  # noqa: E402
    _artifact_items,
    _proposal,
)
from paper.scripts.sanitize_training_prompts import (  # noqa: E402
    _loop_entry_resolver,
    _rejection_reason,
)
from paper.scripts.strengthen_bounds_only_sft import candidates_for  # noqa: E402
from rl_pipeline.common.program import parse_program  # noqa: E402
from rl_pipeline.common.state import (  # noqa: E402
    dedup_normalized,
    extract_invariants,
    invariant_dedup_key,
)
from rl_pipeline.reward.filters import HoudiniFilter  # noqa: E402
from rl_pipeline.sampler.example_sampler import ExampleSampler  # noqa: E402


DEFAULT_SFT = ROOT / "traindata" / "craft_sft_curated_stage11.json"
DEFAULT_OUTPUT = ROOT / "traindata" / "craft_sft_expanded_stage12.json"
DEFAULT_AUDIT = ROOT / "paper" / "artifacts" / "sft_expansion_stage12.json"
DEFAULT_CHECKPOINT = (
    ROOT / "paper" / "artifacts" / "sft_expansion_stage12_checkpoint.jsonl"
)
DEFAULT_ARTIFACT_GLOBS = (
    "paper/artifacts/sft_stage1[2-9]_*.json",
    "paper/artifacts/sft_stage2[0-1]_*.json",
)
MAX_CLAUSES = 20


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _default_artifacts() -> list[Path]:
    paths = {
        Path(value)
        for pattern in DEFAULT_ARTIFACT_GLOBS
        for value in glob.glob(str(ROOT / pattern))
        if not value.endswith("_targets.json")
    }
    return sorted(paths)


def _artifact_candidates(paths: list[Path]) -> dict[int, list[tuple[str, str]]]:
    by_row: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for path in paths:
        for item in _artifact_items(path):
            proposed = _proposal(item)
            if not proposed:
                continue
            for clause in proposed:
                by_row[int(item["row"])].append((clause, f"artifact:{path.name}"))
    return by_row


def _unique_additions(original: list[str], candidates: list[tuple[str, str]], program):
    seen = {invariant_dedup_key(clause) for clause in original}
    additions: list[tuple[str, str]] = []
    for clause, rationale in candidates:
        key = invariant_dedup_key(clause)
        if key in seen:
            continue
        if _rejection_reason(clause, program) is not None:
            continue
        seen.add(key)
        additions.append((clause, rationale))
    return additions


def _stable_frame_candidates(program) -> list[tuple[str, str]]:
    """Frame facts for every in-scope variable that the loop never writes."""
    assigned = ExampleSampler._assigned_names(program.loop.body)
    resolve = _loop_entry_resolver(program)
    candidates = []
    for variable in program.pre_vars:
        if variable in assigned:
            continue
        entry, _needs_loop_entry = resolve(variable)
        if entry is None or entry.strip() == variable:
            continue
        candidates.append(
            (f"{variable} == {entry}", "loop-stable frame relation")
        )
    return candidates


def _job_key(job, frama_version: str, wp_timeout: int) -> str:
    payload = json.dumps(
        {
            "job": job,
            "frama_c": frama_version,
            "wp_timeout": wp_timeout,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _latest_checkpoint(path: Path) -> dict[str, dict]:
    latest = {}
    if path.is_file():
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    item = json.loads(line)
                    latest[item["job_key"]] = item
    return latest


def _verify(job: tuple[int, str, list[str], list[tuple[str, str]]]) -> dict:
    row, source, original, candidates = job
    program = parse_program(source)
    pool = dedup_normalized(original + [clause for clause, _ in candidates])
    survivors = dedup_normalized(HoudiniFilter().filter(program, 0, pool, None))
    survivor_keys = {invariant_dedup_key(clause) for clause in survivors}
    original_survives = all(
        invariant_dedup_key(clause) in survivor_keys for clause in original
    )
    additions = [
        (clause, rationale)
        for clause, rationale in candidates
        if invariant_dedup_key(clause) in survivor_keys
    ]
    return {
        "row": row,
        "original": original,
        "candidates": [clause for clause, _ in candidates],
        "candidate_rationales": {
            clause: rationale for clause, rationale in candidates
        },
        "wp_survivors": survivors,
        "original_survives": original_survives,
        "additions": [clause for clause, _ in additions] if original_survives else [],
        "addition_rationales": {
            clause: rationale for clause, rationale in additions
        } if original_survives else {},
        "error": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sft", type=Path, default=DEFAULT_SFT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--artifact", action="append", type=Path, dest="artifacts")
    parser.add_argument("--jobs", type=int, default=min(12, os.cpu_count() or 1))
    parser.add_argument("--wp-timeout", type=int, default=5)
    parser.add_argument(
        "--target-min-clauses",
        type=int,
        default=8,
        help="only expand answers below this many distinct clauses (default: 8)",
    )
    args = parser.parse_args()

    if args.sft.resolve() == args.output.resolve():
        raise ValueError("refusing to overwrite the input SFT file")
    if not 1 <= args.target_min_clauses <= MAX_CLAUSES:
        raise ValueError("--target-min-clauses must be between 1 and 20")
    frama_c = shutil.which("frama-c")
    if frama_c is None:
        raise RuntimeError("frama-c is unavailable; initialize the opam switch")
    frama_version = subprocess.run(
        [frama_c, "-version"], capture_output=True, text=True, check=True, timeout=10
    ).stdout.strip()

    artifacts = args.artifacts or _default_artifacts()
    records = json.loads(args.sft.read_text(encoding="utf-8"))
    artifact_by_row = _artifact_candidates(artifacts)
    jobs = []
    items: dict[int, dict] = {}
    for row, record in enumerate(records):
        source, answer = record_source_and_answer(record)
        program = parse_program(source)
        original = dedup_normalized(extract_invariants(answer))
        room = max(0, args.target_min_clauses - len(original))

        static = candidates_for(program)
        strong_static = [item for item in static if item[1] != "loop-relevant frame relation"]
        frames = [item for item in static if item[1] == "loop-relevant frame relation"]
        # Prefer semantic update/progress laws, then independently generated
        # candidates, and use frame facts only for remaining capacity.
        proposed = _unique_additions(
            original,
            strong_static
            + artifact_by_row.get(row, [])
            + frames
            + _stable_frame_candidates(program),
            program,
        )[:room]
        items[row] = {
            "row": row,
            "original_count": len(original),
            "candidate_count": len(proposed),
            "status": "at_target" if room == 0 else "no_candidate" if not proposed else "pending",
        }
        if proposed:
            jobs.append((row, source, original, proposed))

    os.environ["CRAFT_WP_TIMEOUT"] = str(args.wp_timeout)
    os.environ.setdefault("CRAFT_WP_PAR", "2")
    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    cached = _latest_checkpoint(args.checkpoint)
    results = []
    pending = []
    for job in jobs:
        key = _job_key(job, frama_version, args.wp_timeout)
        if key in cached:
            results.append(cached[key])
        else:
            pending.append((key, job))
    with args.checkpoint.open("a", encoding="utf-8") as checkpoint:
        with ProcessPoolExecutor(max_workers=args.jobs) as executor:
            futures = {
                executor.submit(_verify, job): (key, job) for key, job in pending
            }
            for completed, future in enumerate(as_completed(futures), 1):
                key, job = futures[future]
                try:
                    result = future.result()
                except Exception as error:
                    result = {
                        "row": job[0],
                        "original": job[2],
                        "candidates": [clause for clause, _ in job[3]],
                        "candidate_rationales": dict(job[3]),
                        "wp_survivors": [],
                        "original_survives": False,
                        "additions": [],
                        "addition_rationales": {},
                        "error": f"{type(error).__name__}: {error}",
                    }
                result["job_key"] = key
                checkpoint.write(json.dumps(result, sort_keys=True) + "\n")
                checkpoint.flush()
                results.append(result)
                if completed % 50 == 0 or completed == len(futures):
                    print(
                        f"WP checked {completed}/{len(futures)} pending candidate rows "
                        f"({len(results)}/{len(jobs)} including cache)",
                        flush=True,
                    )

    expanded = copy.deepcopy(records)
    reasons: Counter[str] = Counter()
    for result in results:
        row = int(result["row"])
        additions = result["additions"]
        if result["error"] is not None:
            status = "error"
        elif not result["original_survives"]:
            status = "original_not_preserved"
        elif not additions:
            status = "all_candidates_rejected"
        else:
            status = "expanded"
            answer = "\n".join(
                f"loop invariant {clause};"
                for clause in result["original"] + additions
            )
            turn = next(
                turn for turn in expanded[row]["conversations"]
                if turn["from"] == "gpt"
            )
            turn["value"] = answer
            reasons.update(result["addition_rationales"].values())
        items[row].update(result)
        items[row]["status"] = status
        items[row]["final_count"] = len(result["original"]) + (
            len(additions) if status == "expanded" else 0
        )

    status_counts = Counter(item["status"] for item in items.values())
    additions_total = sum(
        len(item.get("additions", []))
        for item in items.values()
        if item["status"] == "expanded"
    )
    report = {
        "schema_version": 1,
        "source_sft": str(args.sft),
        "source_sha256": _sha256(args.sft),
        "expanded_sft": str(args.output),
        "frama_c_version": frama_version,
        "wp_timeout_per_obligation_seconds": args.wp_timeout,
        "max_clauses_per_answer": MAX_CLAUSES,
        "target_min_clauses": args.target_min_clauses,
        "source_artifacts": [str(path) for path in artifacts],
        "decision_rule": (
            "retain every original clause; append only interface-valid, non-duplicate "
            "clauses surviving Frama-C/WP with the original set; cap at 20"
        ),
        "rows": len(records),
        "rows_expanded": status_counts["expanded"],
        "clauses_added": additions_total,
        "status_counts": dict(sorted(status_counts.items())),
        "addition_rationales": dict(sorted(reasons.items())),
        "items": [items[row] for row in sorted(items)],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(expanded, indent=2) + "\n", encoding="utf-8")
    report["expanded_sha256"] = _sha256(args.output)
    args.audit.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "rows": report["rows"],
        "rows_expanded": report["rows_expanded"],
        "clauses_added": report["clauses_added"],
        "status_counts": report["status_counts"],
    }, indent=2))


if __name__ == "__main__":
    main()
