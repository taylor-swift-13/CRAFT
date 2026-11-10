"""Audit frozen target-visible CRAFT prefixes using the original semantic engine.

No generation, filtering, or training is performed. Input C/verification files
are read-only. Analysis uses the original 15-second exactness search budget and
the original per-query timeouts. A separate process pool re-parses saved SMT-LIB
obligations and checks them with fresh Z3 instances (8 seconds per obligation).
Positive classifications whose required obligations do not replay are retained
as raw observations but are excluded from certified summary counts.
With --allow-partial, complete prefixes can be audited incrementally against the
frozen 832-key manifest. Each invocation preserves its own input inventory and
summary; no incomplete prefix is marked full_benchmark_complete.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import time
from collections import Counter
from pathlib import Path

import analyze_craft16_semantics as engine


DEFAULT_RUN = Path('/home/yangfp/craft_empirical_runs/craft16_target_visible_20261110')
DEFAULT_PREFIX_RUN = Path('/home/yangfp/craft_empirical_runs/craft16_target_visible_prefixes_20261110')
EXACT_BUDGET_SECONDS = 15
REPLAY_TIMEOUT_MS = 8000
VERSION = 2


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.tmp.{os.getpid()}')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    os.replace(temporary, path)


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def evidence_paths(out: Path, k: int, key: str) -> tuple[Path, Path, Path]:
    prefix = out / f'k{k}'
    return (prefix / 'cases' / f'{key}.json',
            prefix / 'proof_queries' / f'{key}.json',
            prefix / 'replay' / f'{key}.json')


def analysis_worker(job: dict) -> dict:
    started = time.monotonic()
    source_path = Path(job['source_path'])
    verification_path = Path(job['verification_path'])
    case_path, proof_path, _ = evidence_paths(Path(job['out']), job['k'], job['key'])
    # Detect input replacement between scheduling and execution.
    if digest(source_path) != job['source_sha256'] or digest(verification_path) != job['verification_sha256']:
        raise RuntimeError(f"Input changed before analysis: {job['key']} k={job['k']}")
    verification = load(verification_path)
    result = {name: job[name] for name in [
        'key', 'k', 'source_path', 'verification_path', 'source_sha256',
        'verification_sha256', 'analysis_engine_sha256', 'driver_sha256',
    ]}
    result.update(suite=verification['suite'], original_verified=verification['verified'],
                  survivor_count=len(verification['survivors']),
                  exact_budget_seconds=EXACT_BUDGET_SECONDS, schema_version=VERSION)
    analysis = None
    try:
        analysis = engine.Analysis(source_path.read_text(), verification)
        result['strength'] = analysis.strength()
        result['exact'] = analysis.exact(EXACT_BUDGET_SECONDS)
        result['modeled_variables'] = analysis.types
        result['frame_variables'] = sorted(set(analysis.types) - analysis.modified)
    except Exception as exc:
        # As in the original engine, unsupported syntax and solver exceptions
        # never create a positive result. Keep any completed property separately.
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    dump(proof_path, analysis.proof_queries if analysis is not None else {})
    result['proof_queries_sha256'] = digest(proof_path)
    result['seconds'] = time.monotonic() - started
    dump(case_path, result)
    return result


def required_queries(case: dict) -> dict[str, dict[str, str]]:
    required = {}
    strength = case.get('strength', {}).get('classification')
    exact = case.get('exact', {}).get('classification')
    if strength in ['strictly_stronger', 'target_equivalent']:
        required['strength'] = {
            'strength_nonvacuity': 'sat', 'strength_forward': 'unsat',
            'strength_reverse': 'sat' if strength == 'strictly_stronger' else 'unsat',
        }
    if exact in ['proven_exact', 'proven_not_exact']:
        required['exact'] = {'initialization': 'unsat', 'preservation': 'unsat'}
        if exact == 'proven_exact':
            required['exact']['exact_completeness'] = 'unsat'
        else:
            required['exact'].update(lemma_initialization='unsat',
                                     lemma_preservation='unsat',
                                     lemma_unreachable_state='sat')
    return required


def replay_worker(job: dict) -> dict:
    import z3

    started = time.monotonic()
    case_path, proof_path, replay_path = evidence_paths(Path(job['out']), job['k'], job['key'])
    case = load(case_path)
    if digest(proof_path) != case['proof_queries_sha256']:
        raise RuntimeError(f"Saved SMT obligations changed: {job['key']} k={job['k']}")
    queries = load(proof_path)
    required = required_queries(case)
    expected = {label: status for group in required.values() for label, status in group.items()}
    checks = {}
    for label, status in expected.items():
        item = {'expected': status}
        try:
            if label not in queries:
                raise KeyError('missing saved obligation ' + label)
            solver = z3.Solver()
            solver.set(timeout=REPLAY_TIMEOUT_MS)
            solver.add(z3.parse_smt2_string(queries[label]))
            actual = solver.check()
            item['actual'] = str(actual)
            if actual == z3.unknown:
                item['reason'] = solver.reason_unknown()
        except Exception as exc:
            item.update(actual='error', reason=type(exc).__name__ + ': ' + str(exc))
        item['pass'] = item['actual'] == status
        checks[label] = item
    properties = {
        field: all(checks[label]['pass'] for label in group)
        for field, group in required.items()
    }
    result = {
        'key': job['key'], 'k': job['k'], 'schema_version': VERSION,
        'case_sha256': digest(case_path), 'proof_queries_sha256': digest(proof_path),
        'driver_sha256': job['driver_sha256'], 'timeout_ms': REPLAY_TIMEOUT_MS,
        'solver': 'Z3', 'solver_version': z3.get_version_string(),
        'properties': properties, 'queries': checks,
        'pass': all(properties.values()), 'seconds': time.monotonic() - started,
        'scope': 'Fresh SMT-LIB parsing and solving in a separate replay process pool; '
                 'not an independently implemented C translator or proof-object checker.',
    }
    dump(replay_path, result)
    return result


def resumable_analysis(job: dict) -> bool:
    case_path, proof_path, _ = evidence_paths(Path(job['out']), job['k'], job['key'])
    if not case_path.exists() or not proof_path.exists():
        return False
    try:
        case = load(case_path)
        return (case.get('schema_version') == VERSION
                and case.get('exact_budget_seconds') == EXACT_BUDGET_SECONDS
                and all(case.get(name) == job[name] for name in [
                    'key', 'k', 'source_sha256', 'verification_sha256',
                    'analysis_engine_sha256', 'driver_sha256'])
                and case.get('proof_queries_sha256') == digest(proof_path))
    except (OSError, ValueError):
        return False


def resumable_replay(job: dict) -> bool:
    case_path, proof_path, replay_path = evidence_paths(Path(job['out']), job['k'], job['key'])
    if not replay_path.exists():
        return False
    try:
        replay = load(replay_path)
        return (replay.get('schema_version') == VERSION
                and replay.get('timeout_ms') == REPLAY_TIMEOUT_MS
                and replay.get('case_sha256') == digest(case_path)
                and replay.get('proof_queries_sha256') == digest(proof_path)
                and replay.get('driver_sha256') == job['driver_sha256'])
    except (OSError, ValueError):
        return False


def gated_classification(case: dict, replay: dict, field: str) -> str:
    raw = case.get(field, {}).get('classification', 'unsupported')
    if field in required_queries(case) and not replay.get('properties', {}).get(field, False):
        return 'unconfirmed_replay'
    return raw


def summarize_rows(rows: list[tuple[dict, dict]]) -> dict:
    result = {'programs': len(rows)}
    for field in ['strength', 'exact']:
        result[field] = dict(Counter(gated_classification(case, replay, field) for case, replay in rows))
        result['raw_' + field] = dict(Counter(case.get(field, {}).get('classification', 'unsupported') for case, _ in rows))
    result['certified_strict'] = sum(gated_classification(case, replay, 'strength') == 'strictly_stronger' for case, replay in rows)
    result['certified_exact'] = sum(gated_classification(case, replay, 'exact') == 'proven_exact' for case, replay in rows)
    result['certified_strict_and_exact'] = sum(
        gated_classification(case, replay, 'strength') == 'strictly_stronger'
        and gated_classification(case, replay, 'exact') == 'proven_exact' for case, replay in rows)
    for field in ['certified_strict', 'certified_exact']:
        result[field + '_percent'] = round(100 * result[field] / len(rows), 4) if rows else None
    result['errors'] = dict(Counter(case['error'] for case, _ in rows if 'error' in case))
    return result


def write_summary(out: Path, manifest: dict, jobs: list[dict], finished: bool) -> dict:
    by_prefix = {}
    changed = []
    for k in manifest['prefixes']:
        subset = [job for job in jobs if job['k'] == k]
        rows = []
        pending_replay = 0
        for job in subset:
            if not resumable_analysis(job):
                continue
            case_path, _, replay_path = evidence_paths(out, k, job['key'])
            case = load(case_path)
            if resumable_replay(job):
                replay = load(replay_path)
            else:
                replay = {}
                pending_replay += 1
            rows.append((case, replay))
            if finished:
                for name in ['source', 'verification']:
                    if digest(Path(job[name + '_path'])) != job[name + '_sha256']:
                        changed.append(job[name + '_path'])
        missing = manifest['missing_by_prefix'][str(k)]
        row = {
            'k': k, 'requested_programs': len(manifest['selected_keys']),
            'available_complete_inputs': len(subset),
            'missing_or_incomplete': missing,
            'analysis_completed': len(rows), 'replay_pending': pending_replay,
            'available_inputs_audited': finished and len(rows) == len(subset) and pending_replay == 0,
            'complete': finished and not missing and len(rows) == len(subset) and pending_replay == 0,
            'full_benchmark_complete': finished and not missing and len(rows) == 832
                                       and pending_replay == 0 and len(manifest['selected_keys']) == 832,
            'all': summarize_rows(rows),
            'original_verified': summarize_rows([pair for pair in rows if pair[0]['original_verified']]),
            'by_suite': {},
        }
        for suite in sorted({case['suite'] for case, _ in rows}):
            suite_rows = [pair for pair in rows if pair[0]['suite'] == suite]
            row['by_suite'][suite] = {
                'all': summarize_rows(suite_rows),
                'original_verified': summarize_rows([pair for pair in suite_rows if pair[0]['original_verified']]),
            }
        by_prefix[str(k)] = row
        dump(out / f'k{k}' / 'summary.json', row)
    report = {
        'schema_version': VERSION, 'invocation_id': manifest['invocation_id'],
        'source_fingerprint': manifest['source_fingerprint'],
        'expected_benchmark_programs': 832, 'selected_programs': len(manifest['selected_keys']),
        'complete': finished and all(x['complete'] for x in by_prefix.values()) and not changed,
        'full_benchmark_complete': finished and manifest['prefixes'] == [1, 4, 8, 16]
                                   and all(x['full_benchmark_complete'] for x in by_prefix.values()) and not changed,
        'by_prefix': by_prefix, 'input_integrity': {'changed_paths': sorted(set(changed)), 'checked_at_completion': finished},
        'definitions': {
            'certified': 'A positive property is counted only if all required saved obligations replay with the expected SAT/UNSAT result.',
            'original_verified': 'Programs marked verified in the corresponding k-prefix verification record; no verification outcome is changed.',
            'population': 'All counts use the selected program population; --limit/--keys produce a subset, not the full benchmark.',
            'comparison': 'Same semantic engine and budgets as the hidden audit. No extra predecessor search or longer replay timeout is used.',
        },
    }
    dump(out / 'summary.json', report)
    dump(out / 'invocations' / manifest['invocation_id'] / 'summary.json', report)
    if changed:
        raise RuntimeError('Input integrity check failed: ' + ', '.join(changed[:5]))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, default=DEFAULT_RUN)
    parser.add_argument('--prefix-run', type=Path, default=DEFAULT_PREFIX_RUN)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--prefixes', default='1,4,8,16')
    parser.add_argument('--jobs', type=int, default=8)
    parser.add_argument('--replay-jobs', type=int, default=12)
    parser.add_argument('--keys', help='Comma-separated program keys, for an explicitly scoped subset')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--rerun-replay', action='store_true', help='Recheck all saved obligations, even if a previous replay is valid')
    parser.add_argument('--allow-partial', action='store_true', help='Audit only status=complete inputs; retain missing keys and never mark a partial benchmark complete')
    parser.add_argument('--plan-only', action='store_true', help='Validate input manifests and print counts without analysis/replay or output writes')
    args = parser.parse_args()
    if min(args.jobs, args.replay_jobs) < 1:
        parser.error('worker counts must be positive')
    prefixes = sorted(set(int(k) for k in args.prefixes.split(',')))
    if not prefixes or any(k not in [1, 4, 8, 16] for k in prefixes):
        parser.error('prefixes must be drawn from 1,4,8,16')
    run, prefix_run, out = args.run.resolve(), args.prefix_run.resolve(), args.out.resolve()
    if any(out == base or out.is_relative_to(base) for base in [run, prefix_run]):
        parser.error('--out must be outside both read-only input runs')
    source_manifest_path = run / 'manifest.json'
    if not source_manifest_path.exists():
        parser.error(f'missing frozen run manifest: {source_manifest_path}')
    source_manifest = load(source_manifest_path)
    original_inputs = source_manifest.get('original_inputs', {})
    if not isinstance(original_inputs, dict) or len(original_inputs) != 832:
        parser.error('run manifest must contain the complete 832-entry original_inputs map')
    expected_inputs = {Path(path).parent.name + '_' + Path(path).stem: checksum
                       for path, checksum in original_inputs.items()}
    if len(expected_inputs) != 832 or not source_manifest.get('fingerprint'):
        parser.error('run manifest has duplicate program keys or no fingerprint')
    if (out / 'protocol.json').exists():
        previous_protocol = load(out / 'protocol.json')
        previous_fingerprint = previous_protocol.get('source_fingerprint')
        if previous_fingerprint is not None and previous_fingerprint != source_manifest['fingerprint']:
            parser.error('output belongs to a different run fingerprint; choose a separate --out')
    prefix_protocol_path = prefix_run / 'protocol.json'
    prefix_protocol = load(prefix_protocol_path) if prefix_protocol_path.exists() else None
    if prefix_protocol is not None and prefix_protocol.get('source_fingerprint') != source_manifest['fingerprint']:
        parser.error('prefix protocol belongs to a different source run fingerprint')
    keys = sorted(expected_inputs)
    if args.keys:
        requested = sorted(set(args.keys.split(',')))
        missing = sorted(set(requested) - set(keys))
        if missing:
            parser.error('keys absent from the frozen run manifest: ' + ', '.join(missing[:10]))
        keys = requested
    if args.limit is not None:
        if args.limit < 1:
            parser.error('--limit must be positive')
        keys = keys[:args.limit]
    if not keys:
        parser.error('no program keys selected')
    engine_hash = digest(Path(engine.__file__))
    driver_hash = digest(Path(__file__))
    jobs = []
    missing_by_prefix = {str(k): [] for k in prefixes}
    for key in keys:
        source = run / 'sources' / f'{key}.original.c'
        source_hash = digest(source) if source.exists() else None
        if source_hash is not None and source_hash != expected_inputs[key]:
            parser.error(f'frozen source hash disagrees with manifest: {source}')
        for k in prefixes:
            verification = run / 'verification' / f'{key}.json' if k == 16 else prefix_run / 'verification' / f'{key}_k{k}.json'
            record = None
            reason = None
            if source_hash is None:
                reason = 'source_not_frozen'
            elif k != 16 and prefix_protocol is None:
                reason = 'prefix_protocol_missing'
            elif not verification.exists():
                reason = 'verification_missing'
            else:
                try:
                    record = load(verification)
                except (OSError, ValueError):
                    reason = 'verification_unreadable'
                if not isinstance(record, dict):
                    reason = reason or 'verification_not_object'
                elif record.get('status') != 'complete':
                    reason = 'verification_status_' + str(record.get('status', 'missing'))
                elif (not isinstance(record.get('verified'), bool)
                      or not isinstance(record.get('survivors'), list)
                      or not isinstance(record.get('union'), list)
                      or not record.get('suite')):
                    reason = 'verification_schema_incomplete'
            if reason:
                missing_by_prefix[str(k)].append({'key': key, 'reason': reason,
                                                  'source_path': str(source),
                                                  'verification_path': str(verification)})
                if not args.allow_partial:
                    parser.error(f'{key} k={k}: {reason}; wait for completion or use --allow-partial')
                continue
            if 'k' in record and record['k'] != k:
                parser.error(f'prefix k mismatch in {verification}')
            jobs.append({
                'key': key, 'k': k, 'out': str(out), 'source_path': str(source),
                'verification_path': str(verification), 'source_sha256': source_hash,
                'verification_sha256': digest(verification),
                'analysis_engine_sha256': engine_hash, 'driver_sha256': driver_hash,
            })
    manifest = {
        'schema_version': VERSION, 'invocation_id': f'{time.time_ns()}-{os.getpid()}',
        'run': str(run), 'prefix_run': str(prefix_run),
        'source_manifest_path': str(source_manifest_path),
        'source_manifest_sha256': digest(source_manifest_path),
        'source_fingerprint': source_manifest['fingerprint'],
        'prefix_protocol_path': str(prefix_protocol_path),
        'prefix_protocol_sha256': digest(prefix_protocol_path) if prefix_protocol is not None else None,
        'expected_keys': sorted(expected_inputs), 'selected_keys': keys,
        'expected_programs': 832, 'programs_per_prefix': len(keys), 'prefixes': prefixes,
        'allow_partial': args.allow_partial, 'missing_by_prefix': missing_by_prefix,
        'available_complete_inputs': {str(k): sum(job['k'] == k for job in jobs) for k in prefixes},
        'jobs': args.jobs, 'replay_jobs': args.replay_jobs,
        'exact_budget_seconds': EXACT_BUDGET_SECONDS, 'replay_timeout_ms': REPLAY_TIMEOUT_MS,
        'analysis_engine': str(Path(engine.__file__).resolve()), 'analysis_engine_sha256': engine_hash,
        'driver_sha256': driver_hash, 'inputs': jobs,
    }
    if args.plan_only:
        print(json.dumps({name: value for name, value in manifest.items()
                          if name not in ['inputs', 'expected_keys', 'selected_keys', 'missing_by_prefix']}, indent=2))
        return
    out.mkdir(parents=True, exist_ok=True)
    # The top-level protocol is the latest snapshot; every invocation's complete
    # input/hash/missing inventory is retained separately, including zero-work runs.
    dump(out / 'invocations' / manifest['invocation_id'] / 'protocol.json', manifest)
    dump(out / 'protocol.json', manifest)
    todo = [job for job in jobs if not resumable_analysis(job)]
    print(json.dumps({'phase': 'analysis', 'total': len(jobs), 'pending': len(todo)}), flush=True)
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for count, _ in enumerate(pool.map(analysis_worker, todo), 1):
            if count % 100 == 0:
                print(json.dumps({'phase': 'analysis', 'newly_completed': count, 'pending_initially': len(todo)}), flush=True)
    write_summary(out, manifest, jobs, finished=False)
    todo = [job for job in jobs if args.rerun_replay or not resumable_replay(job)]
    print(json.dumps({'phase': 'fresh_replay', 'pending': len(todo)}), flush=True)
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.replay_jobs) as pool:
        for count, _ in enumerate(pool.map(replay_worker, todo), 1):
            if count % 100 == 0:
                print(json.dumps({'phase': 'fresh_replay', 'newly_completed': count, 'pending_initially': len(todo)}), flush=True)
    summary = write_summary(out, manifest, jobs, finished=True)
    print(json.dumps({'complete': summary['complete'],
                     'full_benchmark_complete': summary['full_benchmark_complete'], 'by_prefix': {
        k: {'all': row['all'], 'original_verified': row['original_verified']}
        for k, row in summary['by_prefix'].items()
    }}, indent=2), flush=True)


if __name__ == '__main__':
    main()
