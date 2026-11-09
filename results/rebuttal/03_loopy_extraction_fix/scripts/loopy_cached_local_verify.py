"""Local-only cached trajectory replay of frozen Loopy initial/repair outputs.

Both arms consume the SAME historical responses. Cached repairs were generated
on the historical native trajectory: this is NOT a fresh adaptive corrected
Loopy run. No model client, generation, prompt construction, or credential read
is present. The repository and archived experiment files are read-only inputs.
"""
import argparse
import ast
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
import traceback

sys.dont_write_bytecode = True
REPO = Path('/home/yangfp/CRAFT')
RUN = REPO / 'results/02_rq2_tool_comparison/gpt5nano_tools_no_reasoning_cap8192'
DEFAULT_OUT = Path('/tmp/craft_loopy_cached_local_verify_20261107')
WINDOWS_OUTPUTS = Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20/work/outputs')
OLD_INITIAL = Path('/tmp/craft_loopy_extraction_replay_20261107')
NATIVE_PROTOCOL = 'loopy_n15_as_3x5_k8_shuffle10_repair7_target_hidden_none_cap8192_top_p1_v5'
SCHEMA = 1
SCOPE = 'cached trajectory replay; same frozen initial and historical repair outputs; no new adaptive repair or model calls'
CRITICAL = [
    'experiments/gpt5nano_full832/loopy_adapter.py',
    'experiments/gpt5nano_full832/common.py',
    'rl_pipeline/reward/filters.py', 'rl_pipeline/common/state.py',
    'src/output_verify.py', 'src/houdini_pruner.py',
    'rl_pipeline/inference/inference.py', 'rl_pipeline/reward/annotate.py',
    'rl_pipeline/common/program.py', 'rl_pipeline/common/acsl_parser.py',
    'src/syntax_checker.py', 'src/config.py',
]
sys.path.insert(0, str(REPO))
from experiments.gpt5nano_full832.common import (
    Task, discover_tasks, ensure_frama_c_available, judge_invariants, sha256_text,
)
from rl_pipeline.common.program import parse_program
from rl_pipeline.common.state import dedup_normalized, extract_invariants
from rl_pipeline.reward.filters import auto_filter


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def object_digest(value):
    return sha256_text(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')))


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def durable_gzip_log(path):
    """Close one gzip member per subprocess so earlier logs survive interruption."""
    class Log:
        def write(self, text):
            with gzip.open(path, 'at', encoding='utf-8') as stream:
                stream.write(text)

        def flush(self):
            pass  # write() already closes the gzip member and its backing file.

    yield Log()


def archive_path(value):
    prefix = 'results/gpt5nano_tools_no_reasoning_cap8192/'
    if not str(value).startswith(prefix):
        raise ValueError('Unexpected archived path: ' + str(value))
    path = (RUN / str(value)[len(prefix):]).resolve()
    if not path.is_relative_to(RUN.resolve()) or not path.is_file():
        raise ValueError('Invalid archived artifact path: ' + str(path))
    return path


def local_native_functions():
    """Compile exactly three local pure/verification functions, without API imports."""
    path = REPO / 'experiments/gpt5nano_full832/loopy_adapter.py'
    tree = ast.parse(path.read_text(), filename=str(path))
    names = {'_official_invariants', '_candidate_rollouts', '_run_houdini_candidate'}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    if {n.name for n in nodes} != names:
        raise ValueError('Native local function definitions missing')
    namespace = dict(Task=Task, random=random, time=time, SHUFFLE_TIMES=10,
                     COMBINE_K=8, extract_invariants=extract_invariants,
                     dedup_normalized=dedup_normalized, judge_invariants=judge_invariants)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return tuple(namespace[name] for name in (
        '_official_invariants', '_candidate_rollouts', '_run_houdini_candidate'))


official_extract, candidate_rollouts, run_candidate = local_native_functions()


def fixed_extract(text):
    original = official_extract(text)
    return original if original else extract_invariants(text, max_invariants=1000)


def effective_wp():
    result = {}
    for label, setting, default in [('timeout', 'TIMEOUT', '5'), ('parallel', 'PAR', '8')]:
        value = os.environ.get('CRAFT_WP_' + setting, os.environ.get('LOOPGYM_WP_' + setting, default))
        result[label] = value if value.isdigit() and int(value) >= 1 else default
    result['provers'] = os.environ.get('CRAFT_WP_PROVERS', os.environ.get('LOOPGYM_WP_PROVERS', 'alt-ergo,z3')).strip() or 'alt-ergo,z3'
    result.update(model='Typed', properties='-@terminates,-missing_return')
    return result


def source_hashes():
    paths = {REPO / p for p in CRITICAL}
    paths.add(REPO / 'experiments/gpt5nano_full832/protocol.json')
    for directory in ['rl_pipeline', 'src']:
        paths.update((REPO / directory).rglob('*.py'))
    return {str(p.relative_to(REPO)): digest(p) for p in sorted(paths)}


def old_cache_protocol(source_hash, wp):
    """Fail closed for reuse; incompatibility means recompute locally, never mix."""
    manifest_path = OLD_INITIAL / 'manifest.json'
    result = dict(directory=str(OLD_INITIAL), compatible=False)
    if not manifest_path.is_file():
        return dict(result, reason='no prior initial manifest')
    try:
        manifest = json.loads(manifest_path.read_text())
        result.update(manifest_sha256=digest(manifest_path))
        if manifest['source_run'] != str(RUN):
            raise ValueError('source run differs')
        original_driver = Path(__file__).with_name('loopy_extraction_replay.py')
        if not original_driver.is_file() or digest(original_driver) != manifest['script_sha256']:
            raise ValueError('prior driver differs from manifest')
        for key, value in manifest['wp_environment'].items():
            if os.environ.get(key) != value:
                raise ValueError('prior WP environment differs: ' + key)
        for relative in CRITICAL:
            expected = manifest['source_hashes'].get(relative)
            if expected is None:
                historical = subprocess.check_output([
                    'git', '-C', str(REPO), 'show', manifest['source_git_head'] + ':' + relative])
                expected = hashlib.sha256(historical).hexdigest()
            if source_hash[relative] != expected:
                raise ValueError('prior dependency differs: ' + relative)
        result.update(compatible=True, effective_wp=wp,
                      toolchain_limit='Prior manifest has no executable hashes; per-task logs must match the current WP settings. Current toolchain hashes are recorded for this replay.')
    except Exception as exc:
        result['reason'] = str(exc)
    return result


def prepare(out):
    events_path = RUN / 'events/loopy.jsonl'
    rows = [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]
    events = {}
    for row in rows:
        key = (row['suite'], str(row['case_id']))
        if key in events:
            raise ValueError('Ambiguous multiple archived events: ' + str(key))
        if (row['generation_status'] != 'completed' or row['loopy_protocol'] != NATIVE_PROTOCOL
                or row['model'] != 'gpt-5-nano' or row['reasoning_effort'] != 'none'):
            raise ValueError('Incompatible archived event')
        events[key] = row
    tasks = discover_tasks()
    if len(events) != 832 or len(tasks) != 832:
        raise ValueError('Expected exactly 832 complete archived events/tasks')
    inputs = {str(events_path): digest(events_path)}
    jobs, extracts = [], Counter()
    for task in tasks:
        row = events[(task.suite, str(task.case_id))]
        if task.source_sha256 != row['source_sha256']:
            raise ValueError('Original source drift: ' + str(task.source_path))
        hidden_path = archive_path(row['hidden_source'])
        responses_path = archive_path(row['raw_response_artifact'])
        orchestration_path = archive_path(row['orchestration_artifact'])
        hidden = hidden_path.read_text()
        if sha256_text(hidden) != row['hidden_source_sha256']:
            raise ValueError('Cached hidden source drift')
        task = replace(task, hidden_source=hidden, hidden_source_sha256=row['hidden_source_sha256'])
        responses = json.loads(responses_path.read_text())
        orch = json.loads(orchestration_path.read_text())
        initial, repairs = responses['initial'], responses['repairs']
        if len(initial) != 15 or not 0 <= len(repairs) <= 7:
            raise ValueError('Unexpected initial or repair response count')
        if any(not isinstance(text, str) for text in initial + repairs):
            raise ValueError('Non-string response')
        if len(repairs) != row['repair_count'] or len(repairs) != len(orch['repair_summaries']):
            raise ValueError('Archived repair count mismatch')
        if [r['repair_index'] for r in orch['repair_summaries']] != list(range(1, len(repairs) + 1)):
            raise ValueError('Archived repair sequence mismatch')
        arms = {name: dict(initial=[extract(text) for text in initial],
                           repairs=[extract(text) for text in repairs])
                for name, extract in [('native', official_extract), ('fixed', fixed_extract)]}
        seed = int(task.hidden_source_sha256[:16], 16)
        subsets = [indices for indices, _ in candidate_rollouts(arms['native']['initial'], seed)]
        archived_candidates = orch['candidate_summaries']
        if (len(subsets) != 10 or seed != orch['shuffle_seed']
                or subsets != [item['rollout_indices'] for item in archived_candidates]):
            raise ValueError('Archived subset schedule mismatch')
        for index, (indices, archived) in enumerate(zip(subsets, archived_candidates)):
            union = dedup_normalized(c for i in indices for c in arms['native']['initial'][i])
            if archived['candidate_index'] != index or len(union) != archived['union_invariant_count']:
                raise ValueError('Native candidate differs from archived union count')
        # Only check archived repair cardinality, without reusing its verdicts.
        for clauses, archived in zip(arms['native']['repairs'], orch['repair_summaries']):
            if len(dedup_normalized(clauses)) != archived['candidate_invariant_count']:
                raise ValueError('Native repair extraction differs from archived count')
        files = {str(p): digest(p) for p in [task.source_path, hidden_path, responses_path, orchestration_path]}
        inputs.update(files)
        counts = dict(initial_responses=15, cached_repairs=len(repairs),
                      native_initial_nonempty=sum(map(bool, arms['native']['initial'])),
                      fixed_initial_nonempty=sum(map(bool, arms['fixed']['initial'])),
                      native_repair_nonempty=sum(map(bool, arms['native']['repairs'])),
                      fixed_repair_nonempty=sum(map(bool, arms['fixed']['repairs'])))
        extracts.update(counts)
        job = dict(task=task, key=task.suite + '_' + str(task.case_id), out=str(out),
                   arms=arms, subsets=subsets, seed=seed, input_files=files,
                   responses_sha256=files[str(responses_path)], extraction=counts,
                   source_sha256=task.source_sha256, hidden_source_sha256=task.hidden_source_sha256,
                   archived_initial=any(c['verified'] is True for c in archived_candidates),
                   archived_final=row['verified'] is True,
                   archived_cached_repair_success=any(c['verified'] is True for c in orch['repair_summaries']))
        job['input_fingerprint'] = object_digest({k: v for k, v in job.items() if k not in ['task', 'out']})
        jobs.append(job)
    dependencies = source_hashes()
    wp = effective_wp()
    executable = ensure_frama_c_available()  # Resolves PATH only; does not execute a prover.
    executables = {str(executable.resolve()): digest(executable)}
    for name in ['alt-ergo', 'z3']:
        located = shutil.which(name)
        if located:
            executables[str(Path(located).resolve())] = digest(located)
    cache_protocol = old_cache_protocol(dependencies, wp)
    manifest = dict(schema_version=SCHEMA, scope=SCOPE, tasks=832, source_run=str(RUN),
                    native_protocol=NATIVE_PROTOCOL, script_sha256=digest(__file__),
                    source_hashes=dependencies, executable_hashes=executables,
                    effective_wp=wp, input_files=inputs, old_initial_cache=cache_protocol,
                    extraction_counts=dict(extracts),
                    intervention='Keep the original fenced extractor; only when empty, parse the full response with max_invariants=1000.',
                    candidate_schedule='Ten original seeded k=8 initial subsets, then each cached repair alone as a replacement candidate; stop an arm at first true proof.',
                    memoization='Identical ordered normalized unions within the same task and proof settings; proof timing is not a native inference cost.',
                    empty_union='Unchanged native behavior, including its empty annotation handling.',
                    unknown_policy='No true proof and any None judgment is an error, never a failed program.',
                    input_fingerprints={j['key']: j['input_fingerprint'] for j in jobs})
    protocol_fingerprint = object_digest(manifest)
    manifest['protocol_fingerprint'] = protocol_fingerprint
    for job in jobs:
        job.update(protocol_fingerprint=protocol_fingerprint, old_cache_protocol=cache_protocol)
    return jobs, manifest


def validate_result(result, union):
    if result.get('union') != union:
        raise ValueError('Candidate cache union mismatch')
    survivors = result.get('survivors')
    judgment = result.get('judgment', {})
    if not isinstance(survivors, list) or dedup_normalized(survivors) != survivors:
        raise ValueError('Invalid cached survivors')
    if judgment.get('invariants') != survivors or judgment.get('invariant_count') != len(survivors):
        raise ValueError('Cached judge/survivor mismatch')
    if judgment.get('verified') is not None and type(judgment['verified']) is not bool:
        raise ValueError('Invalid cached verdict')


def import_initial_cache(job):
    if not job['old_cache_protocol'].get('compatible'):
        return {}, dict(imported=0, reason='prior cache protocol incompatible or absent')
    path = OLD_INITIAL / 'tasks' / (job['key'] + '.json')
    log_path = OLD_INITIAL / 'logs' / (job['key'] + '.jsonl.gz')
    if not path.is_file() or not log_path.is_file():
        return {}, dict(imported=0, reason='no complete prior task/log')
    try:
        old = json.loads(path.read_text())
        if old.get('status') != 'complete':
            raise ValueError('prior task not complete')
        for field in ['source_sha256', 'hidden_source_sha256', 'responses_sha256']:
            if old[field] != job[field]:
                raise ValueError('prior task hash mismatch: ' + field)
        if old['suite'] != job['task'].suite or str(old['case_id']) != str(job['task'].case_id):
            raise ValueError('prior task identity mismatch')
        wp = job['old_cache_protocol']['effective_wp']
        phases = set()
        with gzip.open(log_path, 'rt') as log:
            for line in log:
                entry = json.loads(line)
                args = entry.get('args')
                if not isinstance(args, list):
                    continue
                phase = entry.get('phase', {})
                phases.add((phase.get('variant'), phase.get('candidate')))
                if '-wp' in args:
                    for flag, setting in [('-wp-timeout', 'timeout'), ('-wp-par', 'parallel'),
                                          ('-wp-prover', 'provers'), ('-wp-model', 'model')]:
                        if flag not in args or args[args.index(flag) + 1] != wp[setting]:
                            raise ValueError('prior proof log settings mismatch')
                    if '-wp-prop=' + wp['properties'] not in args:
                        raise ValueError('prior proof properties mismatch')
        cache = {}
        for arm in ['native', 'fixed']:
            candidates = old['variants'][arm]['candidates']
            if not 1 <= len(candidates) <= 10:
                raise ValueError('invalid prior candidate count')
            for expected_index, candidate in enumerate(candidates):
                index = candidate['index']
                if index != expected_index or candidate['indices'] != job['subsets'][index]:
                    raise ValueError('prior subset mismatch')
                union = dedup_normalized(c for i in candidate['indices'] for c in job['arms'][arm]['initial'][i])
                validate_result(candidate, union)
                key = object_digest(union)
                if candidate['judgment']['verified'] is None:
                    continue
                if (arm, index) not in phases and key not in cache:
                    raise ValueError('prior candidate lacks proof log or earlier identical union')
                result = {field: candidate[field] for field in ['union', 'survivors', 'filter_seconds', 'judgment']}
                if key in cache and cache[key]['judgment']['verified'] != result['judgment']['verified']:
                    raise ValueError('conflicting cached verdicts for identical union')
                result['origin'] = dict(kind='previous_initial_replay', task_path=str(path), task_sha256=digest(path),
                                        log_path=str(log_path), log_sha256=digest(log_path), arm=arm, candidate=index)
                cache[key] = result
        return cache, dict(imported=len(cache), task_path=str(path), task_sha256=digest(path),
                           log_path=str(log_path), log_sha256=digest(log_path))
    except Exception as exc:
        return {}, dict(imported=0, reason='rejected prior cache: ' + str(exc))


def verify_inputs(job):
    for path, expected in job['input_files'].items():
        if digest(path) != expected:
            raise ValueError('Input modified since preparation: ' + path)


def load_checkpoint(job, path):
    if not path.exists():
        return None
    state = json.loads(path.read_text())
    for field in ['protocol_fingerprint', 'input_fingerprint']:
        if state.get(field) != job[field]:
            raise ValueError('Refusing mixed checkpoint: ' + field)
    for key, result in state['candidate_cache'].items():
        if object_digest(result['union']) != key:
            raise ValueError('Corrupted union cache key')
        validate_result(result, result['union'])
    return state


def worker(job, retry_errors=False):
    out = Path(job['out'])
    destination = out / 'tasks' / (job['key'] + '.json')
    started = time.time()
    real_run = subprocess.run
    task = job['task']
    # Each process has its own tempfile root; repository inputs remain read-only.
    scratch = out / 'scratch' / job['key']
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ['TMPDIR'] = str(scratch)
    import tempfile
    tempfile.tempdir = str(scratch)
    os.chdir(scratch)
    state = None
    try:
        verify_inputs(job)
        state = load_checkpoint(job, destination)
        if state and state['status'] == 'complete':
            return state
        if state and state['status'] == 'error' and not retry_errors:
            return state
        if state is None:
            imported, import_audit = import_initial_cache(job)
            state = dict(schema_version=SCHEMA, scope=SCOPE, suite=task.suite, case_id=task.case_id,
                         status='running', input_fingerprint=job['input_fingerprint'],
                         protocol_fingerprint=job['protocol_fingerprint'],
                         source_sha256=task.source_sha256, hidden_source_sha256=task.hidden_source_sha256,
                         responses_sha256=job['responses_sha256'], archived_initial=job['archived_initial'],
                         archived_final=job['archived_final'], extraction=job['extraction'],
                         import_audit=import_audit, candidate_cache=imported, variants={}, created_utc=utc())
        state.update(status='running', variants={}, last_started_utc=utc())
        state.pop('error', None)
        if retry_errors:
            state['candidate_cache'] = {k: v for k, v in state['candidate_cache'].items()
                                        if v['judgment']['verified'] is not None}
        atomic_json(destination, state)
        ensure_frama_c_available()
        filt = auto_filter()
        if getattr(filt, 'name', '') != 'cascade(pre-frama->houdini)':
            raise RuntimeError('Refusing non-authoritative filter')
        program = parse_program(task.hidden_source)
        phase = {}
        log_path = out / 'logs' / (job['key'] + '.jsonl.gz')
        with durable_gzip_log(log_path) as log:
            def captured_run(*args, **kwargs):
                tick = time.time()
                try:
                    result = real_run(*args, **kwargs)
                    entry = dict(phase=phase.copy(), args=args[0] if args else kwargs.get('args'),
                                 seconds=time.time() - tick, returncode=result.returncode,
                                 stdout=result.stdout, stderr=result.stderr)
                    log.write(json.dumps(entry, default=str) + '\n')
                    log.flush()
                    return result
                except Exception as exc:
                    log.write(json.dumps(dict(phase=phase.copy(), args=str(args),
                                              seconds=time.time() - tick, error=str(exc))) + '\n')
                    log.flush()
                    raise
            subprocess.run = captured_run
            for arm in ['native', 'fixed']:
                trace, success, initial_success, unknown, initial_unknown = [], False, False, False, False
                schedule = [('initial', index, indices, [job['arms'][arm]['initial'][i] for i in indices])
                            for index, indices in enumerate(job['subsets'])]
                schedule += [('repair', index, None, [clauses])
                             for index, clauses in enumerate(job['arms'][arm]['repairs'])]
                for stage, index, indices, selected in schedule:
                    union = dedup_normalized(c for clauses in selected for c in clauses)
                    cache_key = object_digest(union)
                    phase.update(arm=arm, stage=stage, candidate=index, union_sha256=cache_key)
                    reused = cache_key in state['candidate_cache']
                    if reused:
                        result = state['candidate_cache'][cache_key]
                        validate_result(result, union)
                    else:
                        actual_union, survivors, seconds, judgment = run_candidate(task, program, filt, selected)
                        result = dict(union=actual_union, survivors=survivors, filter_seconds=seconds,
                                      judgment=judgment, origin=dict(kind='local_verification', log_path=str(log_path),
                                                                     phase=phase.copy(), verified_utc=utc()))
                        validate_result(result, union)
                        state['candidate_cache'][cache_key] = result
                        # Save every completed unique candidate before attempting another one.
                        state['current_candidate'] = phase.copy()
                        state['updated_utc'] = utc()
                        atomic_json(destination, state)
                    verdict = result['judgment']['verified']
                    unknown |= verdict is None
                    initial_unknown |= stage == 'initial' and verdict is None
                    trace.append(dict(stage=stage, index=index, indices=indices, union_sha256=cache_key,
                                      reused=reused, verified=verdict))
                    if verdict is True:
                        success = True
                        initial_success = stage == 'initial'
                        break
                verdict = True if success else (None if unknown else False)
                initial_verdict = True if initial_success else (None if initial_unknown else False)
                state['variants'][arm] = dict(verified=verdict, initial_verified=initial_verdict,
                                              success_phase=trace[-1]['stage'] if success else None,
                                              status='error' if verdict is None else 'complete',
                                              evaluated_candidates=len(trace), candidates=trace)
                atomic_json(destination, state)
        verify_inputs(job)
        state['status'] = 'error' if any(v['verified'] is None for v in state['variants'].values()) else 'complete'
        if state['status'] == 'error':
            state['error'] = 'No true proof and at least one unknown judgment; retry required.'
        state['native_archive_agreement'] = state['variants']['native']['verified'] == job['archived_final']
    except Exception:
        if state is None:
            # A separate diagnostic must not masquerade as a resumable valid checkpoint.
            error = dict(suite=task.suite, case_id=task.case_id, status='error',
                         error=traceback.format_exc(), preflight_error=True)
            atomic_json(out / 'errors' / (job['key'] + '.json'), error)
            return error
        state.update(status='error', error=traceback.format_exc())
    finally:
        subprocess.run = real_run
    state['last_run_seconds'] = time.time() - started
    state['updated_utc'] = utc()
    atomic_json(destination, state)
    return state


def summarize(out, manifest):
    records = []
    for key, fingerprint in manifest['input_fingerprints'].items():
        path = out / 'tasks' / (key + '.json')
        if path.exists():
            record = json.loads(path.read_text())
            if record.get('input_fingerprint') != fingerprint or record.get('protocol_fingerprint') != manifest['protocol_fingerprint']:
                raise ValueError('Mixed checkpoint in summary: ' + key)
            records.append(record)
        elif (out / 'errors' / (key + '.json')).exists():
            records.append(json.loads((out / 'errors' / (key + '.json')).read_text()))
    by_suite = {}
    for suite in ['linear', 'NLA_lipus', 'Loopy', 'all']:
        group = [r for r in records if suite == 'all' or r['suite'] == suite]
        paired = [r for r in group if r['status'] == 'complete']
        counts = dict(completed=len(paired), errors=sum(r['status'] == 'error' for r in group),
                      running=sum(r['status'] == 'running' for r in group))
        for arm in ['native', 'fixed']:
            counts[arm] = sum(r['variants'][arm]['verified'] is True for r in paired)
            counts[arm + '_initial'] = sum(r['variants'][arm]['initial_verified'] is True for r in paired)
            counts[arm + '_initial_unknown'] = sum(r['variants'][arm]['initial_verified'] is None for r in paired)
            counts[arm + '_repair_gain'] = sum(r['variants'][arm]['verified'] is True
                                              and r['variants'][arm]['initial_verified'] is False for r in paired)
            counts[arm + '_repair_success_after_initial_unknown'] = sum(
                r['variants'][arm]['verified'] is True and r['variants'][arm]['initial_verified'] is None for r in paired)
        counts.update(gains=sum(not r['variants']['native']['verified'] and r['variants']['fixed']['verified'] for r in paired),
                      losses=sum(r['variants']['native']['verified'] and not r['variants']['fixed']['verified'] for r in paired),
                      archived_final=sum(r['archived_final'] for r in paired),
                      native_archive_disagreements=sum(not r['native_archive_agreement'] for r in paired))
        by_suite[suite] = counts
    result = dict(scope=SCOPE, expected=832, observed=len(records), pending=832 - len(records),
                  by_suite=by_suite, extraction_counts=manifest['extraction_counts'], updated_utc=utc(),
                  rates_policy='Counts above use only paired complete tasks; no headline full-dataset rate until 832 complete and zero errors.')
    atomic_json(out / 'summary.json', result)
    return result


def final_input_audit(out, manifest):
    changed = [path for path, expected in manifest['input_files'].items() if digest(path) != expected]
    result = dict(checked=len(manifest['input_files']), changed=changed, unchanged=not changed, checked_utc=utc())
    atomic_json(out / 'input_integrity.json', result)
    if changed:
        raise RuntimeError('Original input files changed during replay; see input_integrity.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--workers', type=int, default=12)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--audit-only', action='store_true')
    parser.add_argument('--retry-errors', action='store_true')
    parser.add_argument('--resume', action='store_true', help='Checkpoints always resume safely; this flag documents intent.')
    args = parser.parse_args()
    out = args.out.resolve()
    if out.is_relative_to(REPO.resolve()) or out.is_relative_to(OLD_INITIAL.resolve()):
        raise ValueError('Repository and previous experiment directories are read-only.')
    if out != DEFAULT_OUT.resolve() and not out.is_relative_to(WINDOWS_OUTPUTS.resolve()):
        raise ValueError('Output must be the dedicated new /tmp directory or Windows work/outputs; archived results are read-only.')
    if args.workers < 1 or (args.limit is not None and args.limit < 1):
        raise ValueError('workers and limit must be positive')
    logging.basicConfig(level=logging.CRITICAL)
    out.mkdir(parents=True, exist_ok=True)
    for sub in ['tasks', 'logs', 'scratch', 'errors']:
        (out / sub).mkdir(exist_ok=True)
    jobs, manifest = prepare(out)
    manifest_path = out / 'manifest.json'
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        if old.get('protocol_fingerprint') != manifest['protocol_fingerprint']:
            raise ValueError('Refusing mixed-protocol resume')
    else:
        atomic_json(manifest_path, manifest)
        (out / 'driver.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps(dict(scope=SCOPE, tasks=832, protocol=manifest['protocol_fingerprint'],
                          extraction=manifest['extraction_counts'], old_cache=manifest['old_initial_cache'])), flush=True)
    if args.audit_only:
        final_input_audit(out, manifest)
        return
    # Interleave suites deterministically; --limit selects this prefix, independent of outcomes.
    groups = {s: [j for j in jobs if j['task'].suite == s] for s in ['linear', 'NLA_lipus', 'Loopy']}
    ordered = [groups[s][i] for i in range(max(map(len, groups.values()))) for s in groups if i < len(groups[s])]
    selected = ordered[:args.limit] if args.limit else ordered
    todo = []
    for job in selected:
        verify_inputs(job)
        checkpoint = load_checkpoint(job, out / 'tasks' / (job['key'] + '.json'))
        if checkpoint and checkpoint['status'] == 'complete':
            continue
        if checkpoint and checkpoint['status'] == 'error' and not args.retry_errors:
            continue
        todo.append(job)
    print(json.dumps(dict(selected=len(selected), pending=len(todo), workers=args.workers)), flush=True)
    try:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(worker, job, args.retry_errors): job['key'] for job in todo}
            for number, future in enumerate(as_completed(futures), 1):
                record = future.result()
                print(json.dumps(dict(finished=number, key=futures[future], status=record['status'],
                                      native=record.get('variants', {}).get('native', {}).get('verified'),
                                      fixed=record.get('variants', {}).get('fixed', {}).get('verified'))), flush=True)
                summarize(out, manifest)
    finally:
        final_input_audit(out, manifest)
        print(json.dumps(summarize(out, manifest), indent=2), flush=True)


if __name__ == '__main__':
    main()
