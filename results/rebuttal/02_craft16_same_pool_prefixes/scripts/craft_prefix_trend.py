"""Same-pool CRAFT compose@1/4/8/16; local verification only, no API access."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
import csv
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
BASE = Path('/tmp/craft16_minimal_20261107_v2')
RUN = Path('/home/yangfp/craft_empirical_runs/craft16_prefix_trend_20261108')
EXPORT = Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20/outputs/craft16_prefix_trend')
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
    report = dict(state='complete' if all_done else 'running', updated_utc=utc(),
                  wall_seconds=time.monotonic()-started, summary=summary, paired=pairs,
                  protocol='Same fresh 16-answer pool, prefix order; no new API calls; actual effort minimal.')
    atomic(EXPORT/'result.json', report)
    out = io.StringIO()
    fields = ['key','suite','case_id','k','status','verified','n_union','n_survivors','seconds','error']
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(sorted(rows.values(), key=lambda r:(r['key'],r['k'])))
    (EXPORT/'per_program.csv').write_text(out.getvalue())
    lines = [f"state: {report['state']}", 'Same 832 programs and same fresh response pool; no new API calls.',
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
    args = parser.parse_args()
    initialize()
    for name in ['verification','logs','scratch']:
        (RUN/name).mkdir(parents=True, exist_ok=True)
    EXPORT.mkdir(parents=True, exist_ok=True)
    manifest = read(BASE/'manifest.json')
    changed = [p for p,h in {**manifest['sources'], **manifest['original_inputs']}.items() if sha(p)!=h]
    if changed:
        raise ValueError(f'Source drift: {changed}')
    tasks = discover_tasks()
    if len(tasks) != 832:
        raise ValueError('Population drift')
    hashes, jobs, rows = {}, [], {}
    for task in tasks:
        key = task.suite + '_' + task.case_id
        task = replace(task, source_path=BASE/'sources'/(key+'.original.c'))
        if task.hidden_source != (BASE/'sources'/(key+'.hidden.c')).read_text():
            raise ValueError('Hidden source drift')
        gp = BASE/'generations'/(key+'.json')
        vp = BASE/'verification'/(key+'.json')
        hashes[str(gp)], hashes[str(vp)] = sha(gp), sha(vp)
        old = read(vp)
        if old['status'] != 'complete' or old['fingerprint'] != manifest['fingerprint']:
            raise ValueError('Incomplete or inconsistent @16 result')
        rows[(key,16)] = dict(key=key,suite=task.suite,case_id=task.case_id,k=16,
            status='complete',verified=old['verified'],n_union=len(old['union']),
            n_survivors=len(old['survivors']),seconds=old['seconds'],reused_original=True)
        for k in [1,4,8]:
            jobs.append((key,k,task,hashes[str(gp)],hashes[str(vp)]))
    protocol = dict(created_utc=utc(), keys=sorted({j[0] for j in jobs}),
        source_run=str(BASE), source_fingerprint=manifest['fingerprint'],
        model='gpt-5-nano', reasoning_effort='minimal', checkpoints=[1,4,8,16],
        pool_selection='First k responses in original accepted call/choice order; no resampling or selection.',
        max_invariants_per_response=20, target_hidden=True, api_calls=0,
        wp=manifest['wp'], driver_sha256=sha(__file__), frozen_base_files=hashes,
        original_inputs=manifest['original_inputs'], verification_sources=manifest['sources'])
    saved = RUN/'protocol.json'
    if saved.exists():
        oldp=read(saved)
        for name in ['keys','source_fingerprint','driver_sha256','frozen_base_files']:
            if oldp[name] != protocol[name]:
                raise ValueError('Resume drift: '+name)
        protocol=oldp
    else:
        atomic(saved,protocol)
        shutil.copyfile(__file__,RUN/'driver.py')
    atomic(EXPORT/'protocol.json',protocol)
    pending=[]
    for job in jobs:
        path=RUN/'verification'/f'{job[0]}_k{job[1]}.json'
        if path.exists() and (value:=read(path)).get('status') == 'complete':
            rows[(job[0],job[1])]=value
        else:
            pending.append(job)
    # Finish @1 first, then @4 and @8, without choosing programs by outcomes.
    pending.sort(key=lambda j:(j[1],j[0]))
    started=time.monotonic()
    atomic(EXPORT/'runtime.json',dict(pid=os.getpid(),status='running',started_utc=utc(),workers=args.workers))
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
    atomic(EXPORT/'runtime.json',dict(pid=os.getpid(),status=report['state'],finished_utc=utc(),workers=args.workers))
    if changed:
        raise ValueError('Base inputs changed during replay')
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    main()
