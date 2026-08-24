#!/usr/bin/env python3
"""Synthesize SFT targets by distilling the compose@k pipeline, target-independently.

Three resumable stages, each keyed by the sha256 of the visible program:

A. ``rollouts``  k responses per program from an OpenAI-compatible model
                 (``OPENAI_API_KEY`` / ``OPENAI_BASE_URL`` / ``CRAFT_MODEL``),
                 parsed with ``extract_invariants``.  -> ``<root>/rollouts.jsonl``
B. ``compose``   union -> interface gate -> Houdini (target-hidden, exactly as
                 inference) -> target-independent pruning to a *core*:
                   * frame equalities of unmodified variables dropped
                     (``loop assigns`` already states them);
                   * guarded copies / subsumed constant bounds dropped;
                   * every transition-law (relational) clause kept;
                   * remaining clauses kept only while they add negative-trace
                     coverage (greedy set cover to the union's coverage);
                   * Houdini re-run on the core (pruning can break support).
                 -> ``<root>/cores.jsonl``
C. ``write``     emit ``{system, human, gpt}`` rows for programs whose core is
                 non-empty, not bounds-only, and within the clause cap.
                 -> ``--output`` json + ``--report``

Nothing here reads a postcondition: training programs have none, and the
judge is the same Houdini filter the inference pipeline uses.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper.scripts.audit_sft_invariant_quality import _clause_features  # noqa: E402
from paper.scripts._curation_common import (  # noqa: E402
    digest_of,
    latest_rows as _latest_rows,
    limit_memory,
    quantile,
)
from paper.scripts.filter_training_by_negative_coverage import (  # noqa: E402
    _atomic_json,
    _display_path,
    _source_from_rl,
    _source_from_sft,
)
from paper.scripts.sanitize_training_prompts import (  # noqa: E402
    _LogicParseError,
    _LogicParser,
    _canonical_user,
    _rejection_reason,
    _remove_guarded_copies,
    _remove_subsumed_constant_bounds,
)
from rl_pipeline.common import prompts  # noqa: E402
from rl_pipeline.common.program import (  # noqa: E402
    integer_source_constants,
    parse_program,
    strip_postcondition,
)
from rl_pipeline.common.state import (  # noqa: E402
    MAX_INVARIANTS_PER_RESPONSE,
    dedup_normalized,
    extract_invariants,
)
from rl_pipeline.reward.filters import HoudiniFilter, frama_c_available  # noqa: E402
from rl_pipeline.reward.reward_calculator import RewardCalculator  # noqa: E402
from rl_pipeline.sampler.example_sampler import (  # noqa: E402
    DEFAULT_N_RUNS,
    DEFAULT_SEED,
    NEGATIVE_SCHEMA_VERSION,
    ExampleSampler,
)

SYNTH_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------
def latest_rows(path: Path, drop: Tuple[str, ...] = ()) -> Dict[str, dict]:
    """Stage ledgers are keyed by program digest; ``drop`` sheds bulky fields
    (e.g. raw responses) that the caller does not need in memory."""
    rows = _latest_rows(path, key="digest")
    for row in rows.values():
        for field in drop:
            row.pop(field, None)
    return rows


def append_row(path: Path, row: dict, lock: Optional[threading.Lock] = None) -> None:
    text = json.dumps(row, sort_keys=True) + "\n"
    if lock:
        with lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(text)
    else:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)


def load_programs(dataset: str, path: Path):
    """Return (unique (digest, source) pairs, digest -> existing answer clauses,
    raw records).  Answers/records are only populated for ``sft`` inputs."""
    answers: Dict[str, List[str]] = {}
    records: List[dict] = []
    if dataset == "sft":
        records = json.loads(path.read_text(encoding="utf-8"))
        sources = []
        for record in records:
            source = _source_from_sft(record)
            answer = next(t["value"] for t in record["conversations"] if t["from"] == "gpt")
            sources.append(source)
            answers.setdefault(
                digest_of(source), extract_invariants(answer, max_invariants=MAX_INVARIANTS_PER_RESPONSE)
            )
    elif dataset == "rl":
        import pyarrow.parquet as pq
        sources = [_source_from_rl(r) for r in pq.read_table(path, columns=["prompt"]).to_pylist()]
    else:  # a plain JSON list of C sources
        sources = json.loads(path.read_text(encoding="utf-8"))
    seen: Dict[str, str] = {}
    for source in sources:
        seen.setdefault(digest_of(source), source)
    answers = {d: a for d, a in answers.items() if a}
    return list(seen.items()), answers, records


# ---------------------------------------------------------------------------
# Stage A: rollouts
# ---------------------------------------------------------------------------
def _rollout_job(chat, source: str, k: int, retries: int = 2) -> dict:
    prompt = prompts.GENERATE_PROMPT.format(program=strip_postcondition(source))
    responses: List[str] = [chat.chat(prompt) for _ in range(k)]
    # Reasoning endpoints sometimes return an empty/unparsable choice when the
    # completion budget is spent on hidden reasoning; retry those individually.
    empty_retries = 0
    for index, text in enumerate(responses):
        attempt = 0
        while not extract_invariants(text) and attempt < retries:
            text = chat.chat(prompt)
            attempt += 1
            empty_retries += 1
        responses[index] = text
    return {
        "responses": responses,
        "empty_retries": empty_retries,
        "invariants": [
            extract_invariants(text, max_invariants=MAX_INVARIANTS_PER_RESPONSE)
            for text in responses
        ],
    }


def stage_rollouts(programs, root: Path, *, k: int, workers: int,
                   model: Optional[str], max_tokens: int) -> Counter:
    from experiments.gpt5nano_full832.api import RecordingChat
    path = root / "rollouts.jsonl"
    done = latest_rows(path)
    pending = [(d, s) for d, s in programs if d not in done or done[d].get("model") != (model or done[d].get("model"))]
    status = Counter(done=len(programs) - len(pending))
    if not pending:
        return status
    chat = RecordingChat(model=model, reasoning_effort="omit", max_completion_tokens=max_tokens)
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_rollout_job, chat, s, k): (d, s) for d, s in pending}
        for count, future in enumerate(as_completed(futures), 1):
            d, s = futures[future]
            try:
                result = future.result()
                row = {"digest": d, "model": chat.model, "k": k, "status": "ok", **result}
                status["ok"] += 1
            except Exception as error:
                row = {"digest": d, "model": chat.model, "k": k, "status": "error",
                       "error": f"{type(error).__name__}: {str(error)[:200]}"}
                status["error"] += 1
            append_row(path, row, lock)
            if count % 20 == 0 or count == len(futures):
                print(f"[rollouts] {count}/{len(futures)} usage={chat.usage()}", flush=True)
    return status


# ---------------------------------------------------------------------------
# Stage B: compose + prune
# ---------------------------------------------------------------------------


def _coverage(examples, clauses: Sequence[str], constants) -> Tuple[float, Set[int]]:
    groups = examples.groups(0)
    if not groups or not clauses:
        return 0.0, set()
    states = RewardCalculator._rejected_set(examples.neg(0), list(clauses), constants)
    rejected = RewardCalculator._to_groups(states, groups)
    return len(rejected) / len(groups), rejected


def _z3_formula(clause: str):
    """z3 formula for a clause, or None when it cannot be trusted (parse
    failure, or C division/modulo whose semantics differ from z3's)."""
    if "/" in clause or "%" in clause:
        return None
    try:
        return _LogicParser(clause).parse()
    except (_LogicParseError, Exception):
        return None


def _implied(premises, conclusion, timeout_ms: int = 200) -> bool:
    import z3
    if conclusion is None or not premises:
        return False
    solver = z3.Solver()
    solver.set(timeout=timeout_ms)
    solver.add(*premises)
    solver.add(z3.Not(conclusion))
    return solver.check() == z3.unsat


def _strength_rank(clause: str, feature: dict) -> int:
    """Lower = stronger/more specific; used to decide which of two mutually
    redundant clauses survives implication pruning."""
    logical = any(op in clause for op in ("==>", "<==>", "||"))
    if feature["transition_law"] and "==" in clause.replace("==>", "") and not logical:
        return 0          # relational equality
    if feature["transition_law"] and not logical:
        return 1          # relational inequality
    if feature["transition_law"]:
        return 2          # guarded / disjunctive relation
    if "&&" in clause:
        return 3          # conjunction of bounds
    if not feature["constant_bound"]:
        return 4          # other inequality
    return 5              # constant bound


def remove_implied(clauses: List[str], features: Dict[str, dict]) -> Tuple[List[str], List[str]]:
    """Drop clauses logically implied (z3, integers) by the rest of the set.

    Forward pass keeps a clause unless the already-kept set implies it;
    backward pass then removes any kept clause implied by the others, so a
    weaker clause admitted early does not shadow a stronger one.  Clauses
    without a trusted formula are always kept.
    """
    order = sorted(range(len(clauses)), key=lambda i: (_strength_rank(clauses[i], features[clauses[i]]), i))
    formulas = {c: _z3_formula(c) for c in clauses}
    kept: List[str] = []
    dropped: List[str] = []
    for i in order:
        clause = clauses[i]
        premises = [formulas[k] for k in kept if formulas[k] is not None]
        if _implied(premises, formulas[clause]):
            dropped.append(clause)
        else:
            kept.append(clause)
    for clause in list(kept):
        if formulas[clause] is None:
            continue
        premises = [formulas[k] for k in kept if k != clause and formulas[k] is not None]
        if _implied(premises, formulas[clause]):
            kept.remove(clause)
            dropped.append(clause)
    kept = [c for c in clauses if c in set(kept)]
    return kept, dropped


COVERAGE_MIN_TRACES = 8


def prune_to_core(program, examples, survivors: List[str]) -> dict:
    """Target-independent core of a Houdini-surviving clause set."""
    constants = integer_source_constants(program.source)
    modified = set(ExampleSampler._modified_vars(program))
    kept = list(survivors)
    dropped: Dict[str, List[str]] = {
        "frame": [], "guarded_copy": [], "subsumed_bound": [], "implied": [], "no_coverage_gain": [],
    }

    features = {c: _clause_features(c, program, modified) for c in kept}
    frame = [c for c in kept if features[c]["frame_only"]]
    kept = [c for c in kept if c not in set(frame)]
    dropped["frame"] = frame
    before = list(kept)
    kept, _ = _remove_guarded_copies(kept)
    dropped["guarded_copy"] = [c for c in before if c not in set(kept)]
    before = list(kept)
    kept, _ = _remove_subsumed_constant_bounds(kept)
    dropped["subsumed_bound"] = [c for c in before if c not in set(kept)]
    kept, dropped["implied"] = remove_implied(kept, features)

    union_cov, union_rej = _coverage(examples, kept, constants)
    per_clause = {c: _coverage(examples, [c], constants)[1] for c in kept}
    # Relational clauses (the ones negatives may fail to witness) are always
    # kept; everything else must earn its place through coverage -- unless
    # there are too few negatives to judge, in which case implication pruning
    # alone decides and every remaining clause is kept.
    if len(examples.groups(0)) < COVERAGE_MIN_TRACES:
        core = list(kept)
    else:
        core = [c for c in kept if _strength_rank(c, features[c]) <= 1]
    covered: Set[int] = set()
    for c in core:
        covered |= per_clause[c]
    candidates = [c for c in kept if c not in set(core)]
    # Greedy set cover: add the clause with the largest marginal coverage
    # until the pruned set rejects everything the full set rejects.
    while covered != union_rej and candidates:
        best = max(candidates, key=lambda c: (len(per_clause[c] - covered), -len(c)))
        gain = per_clause[best] - covered
        if not gain:
            break
        core.append(best)
        covered |= gain
        candidates.remove(best)
    dropped["no_coverage_gain"] = candidates
    core = [c for c in kept if c in set(core)]  # restore original order
    core_cov = len(covered) / len(examples.groups(0)) if examples.groups(0) else 0.0
    return {
        "core": core,
        "cleaned": kept,
        "core_coverage": round(core_cov, 4),
        "union_coverage": round(union_cov, 4),
        "dropped": dropped,
        "clause_features": {
            c: {k: bool(v) for k, v in features[c].items() if k in ("transition_law", "informative_progress", "frame_only", "constant_bound")}
            for c in survivors
        },
    }


def _compose_job(job: Tuple[str, str, List[List[str]], int, int, int]) -> dict:
    digest, source, rollouts, n_runs, seed, min_traces = job
    started = time.perf_counter()
    try:
        program = parse_program(strip_postcondition(source))
        union = dedup_normalized(c for r in rollouts for c in r)
        gated = [c for c in union if _rejection_reason(c, program) is None]
        examples = ExampleSampler(source, n_runs=n_runs, seed=seed).sample()
        if len(examples.groups(0)) < min_traces:  # min_traces=0 disables this gate
            return {"digest": digest, "status": "too_few_negatives", "n_union": len(union),
                    "n_gated": len(gated), "n_negative_traces": len(examples.groups(0)),
                    "seconds": round(time.perf_counter() - started, 2)}
        houdini = HoudiniFilter()
        survivors = dedup_normalized(houdini.filter(program, 0, gated, None))
        if not survivors:
            return {"digest": digest, "status": "empty_survivors", "n_union": len(union),
                    "n_gated": len(gated), "n_negative_traces": len(examples.groups(0)),
                    "seconds": round(time.perf_counter() - started, 2)}
        pruned = prune_to_core(program, examples, survivors)
        core = pruned["core"]
        final = dedup_normalized(houdini.filter(program, 0, core, None)) if core else []
        houdini_lost = [c for c in core if c not in set(final)]
        path = "core"
        if houdini_lost:
            # Support clauses were pruned away; fall back to the cleaned
            # survivor set (frame/guarded/subsumed removed) if that is intact.
            cleaned = pruned["cleaned"]
            recheck = dedup_normalized(houdini.filter(program, 0, cleaned, None))
            if set(recheck) == set(cleaned):
                final, path = cleaned, "cleaned_survivors"
            else:
                final, path = survivors, "survivors"
            constants = integer_source_constants(program.source)
        final_cov, _ = _coverage(examples, final, constants)
        rollout_cov = [
            _coverage(examples, [c for c in dedup_normalized(r) if c in set(survivors)], constants)[0]
            for r in rollouts
        ]
        modified = set(ExampleSampler._modified_vars(program))
        final_features = [_clause_features(c, program, modified) for c in final]
        return {
            "digest": digest,
            "status": "ok",
            "path": path,
            "n_union": len(union),
            "n_gated": len(gated),
            "n_survivors": len(survivors),
            "survivors": survivors,
            "final": final,
            "n_final": len(final),
            "final_coverage": round(final_cov, 4),
            "union_coverage": pruned["union_coverage"],
            "best_single_rollout_coverage": round(max(rollout_cov) if rollout_cov else 0.0, 4),
            "has_transition_law": any(f["transition_law"] for f in final_features),
            "bounds_only": bool(final) and all(f["constant_bound"] for f in final_features),
            "dropped": pruned["dropped"],
            "houdini_lost_from_core": houdini_lost,
            "n_negative_traces": len(examples.groups(0)),
            "negative_families": dict(Counter(examples.group_families(0))),
            "negative_schema_version": NEGATIVE_SCHEMA_VERSION,
            "seconds": round(time.perf_counter() - started, 2),
        }
    except Exception as error:
        return {"digest": digest, "status": "error",
                "error": f"{type(error).__name__}: {str(error)[:300]}",
                "seconds": round(time.perf_counter() - started, 2)}


def stage_compose(programs, answers, root: Path, *, workers: int, n_runs: int, seed: int,
                  memory_cap: int, wp_timeout: int, wp_par: int, min_traces: int,
                  include_existing: bool) -> Counter:
    rollouts = latest_rows(root / "rollouts.jsonl", drop=("responses",))
    path = root / "cores.jsonl"
    done = latest_rows(path)
    jobs = []
    status = Counter()
    for d, s in programs:
        row = rollouts.get(d)
        if row is None or row.get("status") != "ok":
            status["no_rollouts"] += 1
            continue
        if d in done and done[d].get("status") == "ok":
            status["done"] += 1
            continue
        invariants = list(row["invariants"])
        if include_existing and answers.get(d):
            # The archival answer joins the pool as one more rollout; it is
            # filtered and pruned exactly like the model's responses.
            invariants.append(answers[d])
        jobs.append((d, s, invariants, n_runs, seed, min_traces))
    if not jobs:
        return status
    os.environ["CRAFT_WP_TIMEOUT"] = str(wp_timeout)
    os.environ["CRAFT_WP_PAR"] = str(wp_par)
    with ProcessPoolExecutor(max_workers=workers, initializer=limit_memory, initargs=(memory_cap,)) as pool:
        futures = {pool.submit(_compose_job, job): job[0] for job in jobs}
        for count, future in enumerate(as_completed(futures), 1):
            digest = futures[future]
            try:
                row = future.result()
            except Exception as error:
                row = {"digest": digest, "status": "error", "error": f"{type(error).__name__}: {error}"}
            status[row["status"]] += 1
            append_row(path, row)
            if count % 10 == 0 or count == len(futures):
                print(f"[compose] {count}/{len(futures)} {dict(status)}", flush=True)
    return status


# ---------------------------------------------------------------------------
# Stage C: write SFT rows
# ---------------------------------------------------------------------------
def stage_write(programs, root: Path, *, output: Path, report_path: Path,
                min_coverage: float, allow_bounds_only: bool, max_clauses: int,
                model: Optional[str], passthrough: Optional[List[dict]] = None) -> dict:
    cores = latest_rows(root / "cores.jsonl")
    rollouts = latest_rows(root / "rollouts.jsonl", drop=("responses",))
    system = prompts.system_prompt()
    rows = []
    reasons = Counter()
    clause_counts = []
    gains = []
    for d, source in programs:
        core = cores.get(d)
        if core is None or core.get("status") != "ok":
            reasons[core["status"] if core else "no_core"] += 1
            continue
        final = core["final"]
        if not final:
            reasons["empty"] += 1
            continue
        if len(final) > max_clauses:
            reasons["too_many_clauses"] += 1
            continue
        if core["bounds_only"] and not allow_bounds_only:
            reasons["bounds_only"] += 1
            continue
        if core["final_coverage"] < min_coverage and not core["has_transition_law"]:
            reasons["weak"] += 1
            continue
        rows.append({
            "conversations": [
                {"from": "system", "value": system},
                {"from": "human", "value": _canonical_user(source)},
                {"from": "gpt", "value": "\n".join(f"loop invariant {c};" for c in final)},
            ],
            "synthesis": {
                "schema_version": SYNTH_SCHEMA_VERSION,
                "digest": d,
                "model": rollouts.get(d, {}).get("model", model),
                "k": rollouts.get(d, {}).get("k"),
                "path": core["path"],
                "n_union": core["n_union"],
                "n_survivors": core["n_survivors"],
                "n_final": core["n_final"],
                "final_coverage": core["final_coverage"],
                "union_coverage": core["union_coverage"],
                "best_single_rollout_coverage": core["best_single_rollout_coverage"],
                "has_transition_law": core["has_transition_law"],
                "n_negative_traces": core["n_negative_traces"],
            },
        })
        reasons["written"] += 1
        clause_counts.append(len(final))
        gains.append(core["final_coverage"] - core["best_single_rollout_coverage"])
    passthrough = passthrough or []
    _atomic_json(passthrough + rows, output)
    q = quantile
    report = {
        "schema_version": SYNTH_SCHEMA_VERSION,
        "negative_schema_version": NEGATIVE_SCHEMA_VERSION,
        "root": _display_path(root),
        "output": _display_path(output),
        "programs": len(programs),
        "rows_written": len(rows),
        "skipped": {k: v for k, v in reasons.items() if k != "written"},
        "clauses_per_answer": {"mean": round(sum(clause_counts) / len(clause_counts), 2) if clause_counts else None,
                               "median": q(clause_counts, 0.5), "max": q(clause_counts, 1.0)},
        "coverage_gain_over_best_rollout": {"median": q(gains, 0.5), "p25": q(gains, 0.25), "p75": q(gains, 0.75)},
        "with_transition_law": sum(1 for r in rows if r["synthesis"]["has_transition_law"]),
        "paths": dict(Counter(r["synthesis"]["path"] for r in rows)),
        "rows_passthrough_answered": len(passthrough),
        "rows_total": len(passthrough) + len(rows),
        "policy": (
            "target-independent: union of k rollouts -> interface gate -> Houdini "
            "(target-hidden) -> drop frame/guarded/subsumed clauses -> keep every "
            "transition-law clause -> greedy coverage core -> Houdini recheck; "
            "write only non-empty, non-bounds-only answers"
        ),
    }
    _atomic_json(report, report_path)
    return report


# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=("rollouts", "compose", "write", "all"))
    parser.add_argument("--dataset", choices=("sft", "rl", "sources"), default="sft")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True, help="working directory for the stage ledgers")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--limit", type=int, help="only the first N unique programs")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--model", default=None, help="default: CRAFT_MODEL env / protocol model")
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--api-workers", type=int, default=8)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--n-runs", type=int, default=DEFAULT_N_RUNS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--memory-cap", type=int, default=4_000_000_000)
    parser.add_argument("--min-traces", type=int, default=0,
                        help="skip programs with fewer negative traces (0 = never skip; "
                             "below %d traces the coverage step is bypassed)" % COVERAGE_MIN_TRACES)
    parser.add_argument("--no-existing-answer", action="store_true",
                        help="do not add the existing SFT answer to the union")
    parser.add_argument("--keep-answered", action="store_true",
                        help="rows with a non-empty answer are copied to the output unchanged and "
                             "not re-synthesized")
    parser.add_argument("--wp-timeout", type=int, default=5)
    parser.add_argument("--wp-par", type=int, default=2)
    parser.add_argument("--min-coverage", type=float, default=0.2)
    parser.add_argument("--allow-bounds-only", action="store_true")
    parser.add_argument("--max-clauses", type=int, default=MAX_INVARIANTS_PER_RESPONSE)
    args = parser.parse_args()

    args.root.mkdir(parents=True, exist_ok=True)
    programs, answers, records = load_programs(args.dataset, args.input)
    programs = programs[args.offset:]
    if args.limit is not None:
        programs = programs[: args.limit]
    answered = {d for d, s in programs if answers.get(d)}
    if args.keep_answered:
        # Rows that already carry an answer are passed through untouched; only
        # unanswered programs are rolled out, composed and written.
        programs = [(d, s) for d, s in programs if d not in answered]
    print(f"programs: {len(programs)} (answered rows kept as-is: {len(answered) if args.keep_answered else 0})", flush=True)

    if args.stage in ("rollouts", "all"):
        print("[rollouts]", dict(stage_rollouts(
            programs, args.root, k=args.k,
            workers=args.api_workers, model=args.model, max_tokens=args.max_tokens,
        )), flush=True)
    if args.stage in ("compose", "all"):
        if not frama_c_available():
            raise SystemExit("frama-c is not on PATH (eval \"$(opam env --switch=frama-c.27.1 --set-switch)\")")
        print("[compose]", dict(stage_compose(
            programs, answers, args.root, workers=args.workers, n_runs=args.n_runs, seed=args.seed,
            memory_cap=args.memory_cap, wp_timeout=args.wp_timeout, wp_par=args.wp_par,
            min_traces=args.min_traces, include_existing=not args.no_existing_answer,
        )), flush=True)
    if args.stage in ("write", "all"):
        if not args.output or not args.report:
            raise SystemExit("--output and --report are required for the write stage")
        passthrough = [
            r for r in records
            if args.keep_answered and digest_of(_source_from_sft(r)) in answered
        ] if args.keep_answered else []
        report = stage_write(
            programs, args.root, output=args.output, report_path=args.report,
            min_coverage=args.min_coverage, allow_bounds_only=args.allow_bounds_only,
            max_clauses=args.max_clauses, model=args.model, passthrough=passthrough,
        )
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
