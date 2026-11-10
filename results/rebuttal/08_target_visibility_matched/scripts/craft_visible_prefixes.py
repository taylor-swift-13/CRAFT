"""Target-visible generation pool prefixes; local verification only, no API access.

Run after the complete pool is ready, or use --allow-partial for currently ready cases.
Generation sees targets; clause filtering remains target-free, matching the hidden arm.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
import csv
import fcntl
import gzip
import hashlib
import io
import json
import logging
import multiprocessing
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
REPO = Path('/home/yangfp/CRAFT')
BASE = Path('/home/yangfp/craft_empirical_runs/craft16_target_visible_20261110')
RUN = Path('/home/yangfp/craft_empirical_runs/craft16_target_visible_prefixes_20261110')
EXPORT = Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20/outputs/craft16_target_visible_prefixes_20261110')
sys.path.insert(0, str(REPO))
from experiments.gpt5nano_full832.common import discover_tasks, ensure_frama_c_available, judge_invariants
from rl_pipeline.common.program import parse_program
from rl_pipeline.common.state import extract_invariants, dedup_normalized
from rl_pipeline.reward.filters import auto_filter


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def atomic(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def initialize():
    logging.basicConfig(level=logging.CRITICAL)
    os.environ['CRAFT_WP_TIMEOUT'] = '5'
    os.environ['CRAFT_WP_PAR'] = '2'
    os.environ['CRAFT_WP_PROVERS'] = 'alt-ergo,z3'
    ensure_frama_c_available()


def validate_manifest(manifest):
    expected = dict(expected=832, model='gpt-5-nano', reasoning_effort='minimal',
                    n_rollouts=16, request_schedule=[4, 4, 4, 4],
                    max_completion_tokens=8192, max_invariants_per_response=20,
                    target_hidden=False, repair=False)
    for field, value in expected.items():
        if manifest.get(field) != value:
            raise ValueError(f'Unexpected source protocol {field}: {manifest.get(field)!r}')
    if manifest.get('wp') != dict(timeout=5, parallel=2, provers='alt-ergo,z3'):
        raise ValueError('Source verifier settings differ from the hidden arm')
    if sorted(manifest.get('unsupported_sampling_parameters_omitted', [])) != ['temperature', 'top_p']:
        raise ValueError('Unexpected generation sampling parameters')
    if len(manifest.get('original_inputs', {})) != 832:
        raise ValueError('Source input population is not 832')


def export_generation_costs(protocol, ready_keys):
    """Recorded successful n=4 batch usage; @1 is the historical estimator."""
    generations = [(key, read(BASE/'generations'/(key+'.json'))) for key in ready_keys]
    by_k = {}
    per_program = []
    for k in [1, 4, 8, 16]:
        calls = [call for key, g in generations for call in g['calls'][:max(1, k//4)]]
        missing = sum(call.get('usage') is None for call in calls)
        usage = [call['usage'] for call in calls if call.get('usage') is not None]
        inputs = sum(u.get('prompt_tokens', 0) for u in usage)
        outputs = sum(u.get('completion_tokens', 0) for u in usage)
        total = sum(u.get('total_tokens', 0) for u in usage)
        cached = sum((u.get('prompt_tokens_details') or {}).get('cached_tokens', 0) or 0 for u in usage)
        reasoning = sum((u.get('completion_tokens_details') or {}).get('reasoning_tokens', 0) or 0 for u in usage)
        if k == 1:
            # The API reports usage for four choices together; do not divide input by four.
            outputs /= 4
            reasoning /= 4
            total = inputs + outputs
        by_k[str(k)] = dict(programs=len(generations), expected_programs=832,
            full_population=len(generations)==832, api_calls=len(calls), physical_batches_used=len(calls),
            exact_recorded_usage=(k != 1), missing_usage_batches=missing,
            input_tokens=inputs, output_tokens=outputs, total_tokens=total,
            cached_input_tokens=cached, reasoning_tokens=reasoning,
            mean_tokens_per_program=None if missing or not generations else total/len(generations))
        for key, generation in generations:
            selected = generation['calls'][:max(1, k//4)]
            us = [call['usage'] for call in selected if call.get('usage') is not None]
            input_tokens = sum(u.get('prompt_tokens', 0) for u in us)
            output_tokens = sum(u.get('completion_tokens', 0) for u in us)
            total_tokens = sum(u.get('total_tokens', 0) for u in us)
            if k == 1:
                output_tokens /= 4
                total_tokens = input_tokens + output_tokens
            per_program.append(dict(key=key, k=k, physical_batches_used=len(selected),
                exact_recorded_usage=(k!=1), missing_usage_batches=len(selected)-len(us),
                input_tokens=input_tokens, output_tokens=output_tokens,
                known_total_tokens=total_tokens,
                total_tokens=total_tokens if len(us)==len(selected) else None))
    atomic(EXPORT/'generation_prefix_token_costs.json', dict(
        scope='Usage only for the currently ready generation+@16 pool; partial population when fewer than 832 programs. Cached input is included; failed-attempt billing may be unavailable. Prefix costs overlap and must not be summed.',
        same_pool=True, programs=len(generations), expected_programs=832, ready_keys=ready_keys,
        source_fingerprint=protocol['source_fingerprint'], by_k=by_k,
        extra_tokens_for_local_prefix_verification=0,
        compose1_note='Estimate: full first-batch input plus one quarter of its output. No exact one-choice usage is available from n=4 generation. Reasoning tokens are already included in output.'))
    text=io.StringIO()
    fields=['key','k','physical_batches_used','exact_recorded_usage','missing_usage_batches',
            'input_tokens','output_tokens','known_total_tokens','total_tokens']
    writer=csv.DictWriter(text,fieldnames=fields);writer.writeheader();writer.writerows(per_program)
    (EXPORT/'per_program_generation_costs.csv').write_text(text.getvalue())


def verify(job):
    key, k, task, generation_sha, base_sha = job
    name = f'{key}_k{k}'
    path = RUN / 'verification' / (name + '.json')
    state = dict(key=key, suite=task.suite, case_id=task.case_id, k=k,
                 generation_sha256=generation_sha, base_verification_sha256=base_sha,
                 status='running', started_utc=utc())
    atomic(path, state)
    scratch = RUN / 'scratch' / name
    scratch.mkdir(exist_ok=True)
    os.chdir(scratch)
    tempfile.tempdir = str(scratch)
    os.environ['TMPDIR'] = str(scratch)
    log = RUN / 'logs' / (name + '.jsonl.gz')
    original_run = subprocess.run
    fatal = []
    start = time.monotonic()
    try:
        gpath = BASE / 'generations' / (key + '.json')
        bpath = BASE / 'verification' / (key + '.json')
        if sha(gpath) != generation_sha or sha(bpath) != base_sha:
            raise ValueError('Cached input changed')
        generation, baseline = read(gpath), read(bpath)
        if generation['status'] != 'complete' or len(generation['responses']) != 16:
            raise ValueError('Not a complete 16-response pool')
        if baseline.get('generation_sha256') != generation_sha:
            raise ValueError('@16 judgment refers to a different generation record')
        rollouts = [extract_invariants(text, max_invariants=20) for text in generation['responses']]
        if rollouts != baseline['rollouts']:
            raise ValueError('Parser drift')
        union = dedup_normalized(c for rollout in rollouts[:k] for c in rollout)

        def captured(*args, **kwargs):
            tick = time.monotonic()
            command = args[0] if args else kwargs.get('args')
            inputs = {}
            if isinstance(command, (list, tuple)):
                for value in command:
                    if isinstance(value, (str, os.PathLike)) and str(value).endswith('.c'):
                        p = Path(value)
                        if p.is_file():
                            inputs[str(p)] = p.read_text(errors='replace')
            try:
                result = original_run(*args, **kwargs)
                entry = dict(args=command, returncode=result.returncode,
                             stdout=result.stdout, stderr=result.stderr,
                             seconds=time.monotonic()-tick, c_inputs=inputs)
                if result.returncode < 0 or any(x in str(result.stderr).lower()
                                               for x in ['out of memory', 'segmentation fault']):
                    fatal.append(entry)
            except Exception as error:
                entry = dict(args=command, error=str(error), seconds=time.monotonic()-tick,
                             c_inputs=inputs)
                if isinstance(error, FileNotFoundError):
                    fatal.append(entry)
                with gzip.open(log, 'at') as handle:
                    handle.write(json.dumps(entry, default=str) + '\n')
                raise
            with gzip.open(log, 'at') as handle:
                handle.write(json.dumps(entry, default=str) + '\n')
            return result

        subprocess.run = captured
        filt = auto_filter()
        if filt.name != 'cascade(pre-frama->houdini)':
            raise RuntimeError('Unexpected filter chain')
        survivors = filt.filter(parse_program(task.hidden_source), 0, union, positives=None)
        judgment = judge_invariants(task, survivors)
        if fatal or judgment['verified'] is None:
            raise RuntimeError('Verification infrastructure error')
        state.update(status='complete', verified=judgment['verified'], union=union,
                     survivors=survivors, judgment=judgment,
                     n_union=len(union), n_survivors=len(survivors))
    except Exception as error:
        state.update(status='error', error=f'{type(error).__name__}: {error}')
    finally:
        subprocess.run = original_run
    state.update(seconds=time.monotonic()-start, updated_utc=utc())
    atomic(path, state)
    return state


def export(protocol, rows, started):
    summary = {}
    for k in [1, 4, 8, 16]:
        summary[str(k)] = {}
        for suite in ['linear', 'NLA_lipus', 'Loopy', 'all']:
            rs = [r for r in rows.values() if r['k'] == k and (suite == 'all' or r['suite'] == suite)]
            done = [r for r in rs if r['status'] == 'complete']
            expected = 832 if suite == 'all' else {'linear':316, 'NLA_lipus':50, 'Loopy':466}[suite]
            good = sum(r['verified'] is True for r in done)
            summary[str(k)][suite] = dict(expected=expected, complete=len(done),
                errors=sum(r['status'] == 'error' for r in rs), success=good,
                rate=good/expected if len(done) == expected else None)
    pairs = {}
    for low, high in [(1,4), (4,8), (8,16), (1,16)]:
        comparisons = [(rows[(key,low)], rows[(key,high)]) for key in protocol['keys']
                       if (key,low) in rows and (key,high) in rows
                       and rows[(key,low)]['status'] == rows[(key,high)]['status'] == 'complete']
        pairs[f'{low}->{high}'] = dict(compared=len(comparisons),
            gains=sum(not a['verified'] and b['verified'] for a,b in comparisons),
            losses=sum(a['verified'] and not b['verified'] for a,b in comparisons))
    all_done = all(summary[str(k)]['all']['complete'] == 832 for k in [1,4,8,16])
    report = dict(state='complete' if all_done else 'partial', updated_utc=utc(),
                  wall_seconds=time.monotonic()-started, summary=summary, paired=pairs,
                  protocol='Same target-visible 16-answer pool, prefix order; target-free filtering; no new API calls; actual effort minimal.')
    atomic(EXPORT/'result.json', report)
    out = io.StringIO()
    fields = ['key','suite','case_id','k','status','verified','n_union','n_survivors','seconds','error']
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(sorted(rows.values(), key=lambda r:(r['key'],r['k'])))
    (EXPORT/'per_program.csv').write_text(out.getvalue())
    lines = [f"state: {report['state']}", 'Same 832 programs and same target-visible response pool; no new API calls.',
             'k | complete | success | rate (full benchmark only)']
    for k in [1,4,8,16]:
        group = summary[str(k)]['all']
        rate = f"{group['rate']:.2%}" if group['rate'] is not None else 'pending'
        lines.append(f"{k} | {group['complete']} | {group['success']} | {rate}")
    lines += ['', json.dumps(pairs), f"wall seconds: {report['wall_seconds']:.1f}"]
    (EXPORT/'report.txt').write_text('\n'.join(lines)+'\n')
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, default=16)
    parser.add_argument('--allow-partial', action='store_true',
                        help='Process the ready generation+@16 pool now; rerun later to add cases.')
    args = parser.parse_args()
    initialize()
    for name in ['verification','logs','scratch']:
        (RUN/name).mkdir(parents=True, exist_ok=True)
    EXPORT.mkdir(parents=True, exist_ok=True)
    manifest = read(BASE/'manifest.json')
    validate_manifest(manifest)
    changed = [p for p,h in {**manifest['sources'], **manifest['original_inputs']}.items() if sha(p)!=h]
    if changed:
        raise ValueError(f'Source drift: {changed}')
    tasks = discover_tasks()
    if len(tasks) != 832:
        raise ValueError('Population drift')
    expected_keys=sorted(task.suite+'_'+task.case_id for task in tasks)
    protocol = dict(created_utc=utc(), keys=expected_keys, expected_keys=expected_keys,
        expected_programs=832, source_run=str(BASE), source_fingerprint=manifest['fingerprint'],
        source_manifest_sha256=sha(BASE/'manifest.json'),
        model='gpt-5-nano', reasoning_effort='minimal', checkpoints=[1,4,8,16],
        pool_selection='First k responses in original accepted call/choice order; no resampling or selection.',
        max_invariants_per_response=20, target_hidden=False, generation_target_visible=True,
        target_free_filtering=True, api_calls=0, wp=manifest['wp'], driver_sha256=sha(__file__),
        original_inputs=manifest['original_inputs'], verification_sources=manifest['sources'])
    saved = RUN/'protocol.json'
    if saved.exists():
        oldp=read(saved)
        for name,value in protocol.items():
            if name!='created_utc' and oldp.get(name)!=value:
                raise ValueError('Resume drift: '+name)
        protocol=oldp
    else:
        atomic(saved,protocol)
        shutil.copyfile(__file__,RUN/'driver.py')
    atomic(EXPORT/'protocol.json',protocol)
    ledger_path=RUN/'frozen_completed_inputs.json'
    ledger=read(ledger_path) if ledger_path.exists() else dict(source_fingerprint=manifest['fingerprint'],cases={})
    if ledger.get('source_fingerprint')!=manifest['fingerprint']:
        raise ValueError('Completed-input ledger protocol drift')
    for key,entry in ledger['cases'].items():
        if key not in expected_keys:
            raise ValueError('Unexpected case in completed-input ledger: '+key)
        for path,value in entry['files'].items():
            if not Path(path).is_file() or sha(path)!=value:
                raise ValueError('Previously frozen completed input changed: '+path)
    hashes, jobs, rows, missing = {}, [], {}, []
    for task in tasks:
        key = task.suite + '_' + task.case_id
        original_sha = sha(task.source_path)
        task = replace(task, source_path=BASE/'sources'/(key+'.original.c'))
        if sha(task.source_path) != original_sha:
            raise ValueError('Original source snapshot drift: '+key)
        if task.hidden_source != (BASE/'sources'/(key+'.hidden.c')).read_text():
            raise ValueError('Hidden source drift')
        gp = BASE/'generations'/(key+'.json')
        vp = BASE/'verification'/(key+'.json')
        generation=read(gp) if gp.exists() else {}
        old=read(vp) if vp.exists() else {}
        if generation.get('status')!='complete' or old.get('status')!='complete':
            missing.append(dict(key=key,suite=task.suite,case_id=task.case_id,
                generation_status=generation.get('status','missing'),
                verification16_status=old.get('status','missing')))
            continue
        hashes[str(gp)], hashes[str(vp)] = sha(gp), sha(vp)
        if old.get('fingerprint') != manifest['fingerprint'] or not isinstance(old.get('verified'),bool):
            raise ValueError('Incomplete or inconsistent @16 result')
        if generation.get('fingerprint') != manifest['fingerprint']:
            raise ValueError('Incomplete or inconsistent generation: '+key)
        original_source = task.source_path.read_text(errors='ignore')
        if not any(message.get('role') == 'user' and original_source in message.get('content', '')
                   for message in generation.get('messages', [])):
            raise ValueError('Model-facing prompt does not contain the original target-visible source: '+key)
        if len(generation.get('calls', [])) != 4 or any(
                call.get('requested_n') != 4 or len(call.get('raw_responses', [])) != 4
                for call in generation['calls']):
            raise ValueError('Unexpected accepted batch schedule: '+key)
        flat = [answer for call in generation['calls'] for answer in call['raw_responses']]
        if generation.get('responses') != flat or old.get('generation_sha256') != hashes[str(gp)]:
            raise ValueError('Response order or @16 generation hash drift: '+key)
        frozen=dict(files={str(gp):hashes[str(gp)],str(vp):hashes[str(vp)]})
        if key in ledger['cases'] and ledger['cases'][key]!=frozen:
            raise ValueError('Completed case inputs changed: '+key)
        ledger['cases'][key]=frozen
        rows[(key,16)] = dict(key=key,suite=task.suite,case_id=task.case_id,k=16,
            status='complete',verified=old['verified'],n_union=len(old['union']),
            n_survivors=len(old['survivors']),seconds=old['seconds'],reused_original=True,
            generation_sha256=hashes[str(gp)],base_verification_sha256=hashes[str(vp)])
        for k in [1,4,8]:
            jobs.append((key,k,task,hashes[str(gp)],hashes[str(vp)]))
    ready_keys=sorted(key for key,k in rows if k==16)
    atomic(EXPORT/'pending_programs.json',dict(utc=utc(),expected_programs=832,
        ready_programs=len(ready_keys),missing_programs=len(missing),ready_keys=ready_keys,
        records=missing,note='Not-ready programs are pending, not verification failures. This invocation snapshots readiness; rerun to include later completions.'))
    if missing and not args.allow_partial:
        raise ValueError(f'{len(missing)} programs are not ready; use --allow-partial to process {len(ready_keys)} ready cases.')
    atomic(ledger_path,ledger)
    atomic(EXPORT/'frozen_completed_inputs.json',ledger)
    export_generation_costs(protocol,ready_keys)
    pending=[]
    for job in jobs:
        path=RUN/'verification'/f'{job[0]}_k{job[1]}.json'
        if path.exists() and (value:=read(path)).get('status') == 'complete':
            if (value.get('generation_sha256') != job[3]
                    or value.get('base_verification_sha256') != job[4]
                    or value.get('key') != job[0] or value.get('k') != job[1]):
                raise ValueError('Cached prefix result provenance drift: '+str(path))
            rows[(job[0],job[1])]=value
        else:
            pending.append(job)
    # Finish @1 first, then @4 and @8, without choosing programs by outcomes.
    pending.sort(key=lambda j:(j[1],j[0]))
    started=time.monotonic()
    atomic(EXPORT/'runtime.json',dict(pid=os.getpid(),status='running',started_utc=utc(),workers=args.workers,
        allow_partial=args.allow_partial,ready_programs=len(ready_keys),pending_programs=len(missing)))
    export(protocol,rows,started)
    last=time.monotonic()
    with ProcessPoolExecutor(max_workers=args.workers,mp_context=multiprocessing.get_context('spawn'),
                             initializer=initialize) as pool:
        futures=[pool.submit(verify,j) for j in pending]
        for future in as_completed(futures):
            value=future.result()
            rows[(value['key'],value['k'])]=value
            if time.monotonic()-last >= 20 or value['status']=='error':
                report=export(protocol,rows,started)
                print(json.dumps(dict(utc=utc(),completed=len(rows),status=value['status'],
                    progress={k:report['summary'][str(k)]['all'] for k in [1,4,8,16]})),flush=True)
                last=time.monotonic()
    changed=[p for p,h in {**hashes,**manifest['sources'],**manifest['original_inputs']}.items() if sha(p)!=h]
    atomic(EXPORT/'input_integrity.json',dict(checked=len(hashes)+len(manifest['sources'])+832,changed=changed,utc=utc()))
    report=export(protocol,rows,started)
    atomic(EXPORT/'runtime.json',dict(pid=os.getpid(),status=report['state'],finished_utc=utc(),workers=args.workers,
        invocation_finished=True,allow_partial=args.allow_partial,ready_programs=len(ready_keys),pending_programs=len(missing)))
    if changed:
        raise ValueError('Base inputs changed during replay')
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    RUN.mkdir(parents=True, exist_ok=True)
    # Allow an incremental run now and the supervised final run later without
    # duplicate verification or concurrent writes to the same evidence files.
    with (RUN/'evaluation.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        invocation_dir=RUN/'invocations'
        invocation_dir.mkdir(exist_ok=True)
        invocation_path=invocation_dir/(str(time.time_ns())+'.json')
        invocation=dict(pid=os.getpid(),argv=sys.argv,started_utc=utc(),driver_sha256=sha(__file__))
        atomic(invocation_path,invocation)
        try:
            main()
            invocation['status']='complete'
        except BaseException as error:
            invocation.update(status='error',error=type(error).__name__+': '+str(error))
            raise
        finally:
            invocation['finished_utc']=utc()
            atomic(invocation_path,invocation)
