"""Same-response-pool BestCoverage/OracleBest/Compose; offline verification only."""
import os
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
from datetime import datetime, timezone
import csv
import gzip
import hashlib
import io
import json
import logging
import multiprocessing
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
REPO = Path('/home/yangfp/CRAFT')
BASE = Path('/tmp/craft16_minimal_20261107_v2')
PREFIX = Path('/home/yangfp/craft_empirical_runs/craft16_prefix_trend_20261108')
RUN = Path('/home/yangfp/craft_empirical_runs/bestof_compose_loopy466_20261109')
EXPORT = Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20/outputs/bestof_compose_loopy466_20261109')
KS = (1, 4, 8, 16)
sys.path.insert(0, str(REPO))
from experiments.gpt5nano_full832.common import discover_tasks, ensure_frama_c_available, judge_invariants
from rl_pipeline.common.program import parse_program, state_external_integer_constants
from rl_pipeline.common.state import State, dedup_normalized, extract_invariants, normalize_invariant
from rl_pipeline.reward.filters import auto_filter
from rl_pipeline.reward.reward_calculator import RewardCalculator
from rl_pipeline.sampler import ExampleSampler


def utc():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def atomic(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def clause_key(clauses):
    return digest([normalize_invariant(c) for c in clauses])


def initialize():
    logging.basicConfig(level=logging.CRITICAL)
    os.environ['CRAFT_WP_TIMEOUT'] = '5'
    os.environ['CRAFT_WP_PAR'] = '2'
    os.environ['CRAFT_WP_PROVERS'] = 'alt-ergo,z3'
    ensure_frama_c_available()


def proof_path(key, ck):
    return RUN / 'single_proofs' / f'{key}_{ck}.json'


def prepare():
    initialize()
    for name in ['single_proofs', 'samples', 'logs', 'scratch', 'per_program']:
        (RUN / name).mkdir(parents=True, exist_ok=True)
    EXPORT.mkdir(parents=True, exist_ok=True)
    manifest = read(BASE / 'manifest.json')
    changes = [p for p, h in manifest['sources'].items() if sha(p) != h]
    if changes:
        raise ValueError('Verifier/parser/sampler source drift: ' + repr(changes))
    tasks = [t for t in discover_tasks() if t.suite == 'Loopy']
    assert len(tasks) == 466
    frozen, cases, jobs = {}, {}, []
    for task in tasks:
        key = 'Loopy_' + task.case_id
        gp, vp = BASE/'generations'/(key+'.json'), BASE/'verification'/(key+'.json')
        original, hidden = BASE/'sources'/(key+'.original.c'), BASE/'sources'/(key+'.hidden.c')
        for path in [gp, vp, original, hidden, task.source_path]:
            frozen[str(path)] = sha(path)
        assert task.source_sha256 == sha(original) == sha(task.source_path)
        assert task.hidden_source == hidden.read_text()
        task = replace(task, source_path=original)
        gen, full = read(gp), read(vp)
        assert gen['status'] == full['status'] == 'complete'
        assert gen['fingerprint'] == full['fingerprint'] == manifest['fingerprint']
        assert len(gen['responses']) == 16
        raw = [dedup_normalized(extract_invariants(t, max_invariants=20)) for t in gen['responses']]
        assert raw == [dedup_normalized(r) for r in full['rollouts']]
        known, compose = {}, {}
        for k in KS:
            path = vp if k == 16 else PREFIX/'verification'/f'{key}_k{k}.json'
            frozen[str(path)] = sha(path)
            record = read(path)
            assert record['status'] == 'complete' and isinstance(record['verified'], bool)
            assert record['generation_sha256'] == frozen[str(gp)]
            union = dedup_normalized(c for r in raw[:k] for c in r)
            assert union == record['union']
            compose[str(k)] = dict(path=str(path), sha256=sha(path))
            known[clause_key(union)] = (path, record)
        keys = [clause_key(r) for r in raw]
        cases[key] = dict(case_id=task.case_id, hidden_path=str(hidden), original_path=str(original),
                          generation_path=str(gp), generation_sha256=sha(gp),
                          candidate_keys=keys, compose=compose)
        for ck, clauses in {clause_key(r):r for r in raw}.items():
            out = proof_path(key, ck)
            if out.exists() and read(out).get('status') == 'complete':
                previous = read(out)
                assert previous['clauses'] == clauses
                assert previous['generation_sha256'] == sha(gp)
            elif ck in known:
                path, record = known[ck]
                atomic(out, dict(kind='single', key=key, set_key=ck, clauses=clauses,
                    status='complete', survivors=record['survivors'], verified=record['verified'],
                    judgment=record['judgment'], seconds=0.0, reused=True,
                    recorded_original_seconds=record['seconds'], reused_from=str(path),
                    reused_sha256=sha(path), generation_sha256=sha(gp)))
            else:
                jobs.append(dict(kind='single', key=key, set_key=ck, task=task, clauses=clauses,
                                 generation_sha256=sha(gp), output=str(out)))
        sample_path = RUN/'samples'/(key+'.json')
        if not sample_path.exists() or read(sample_path).get('status') != 'complete':
            jobs.append(dict(kind='sample', key=key, hidden_source=task.hidden_source,
                             hidden_sha256=sha(hidden), output=str(sample_path)))
    protocol = dict(schema=1, created_utc=utc(), model='gpt-5-nano', reasoning_effort='minimal',
        population='Loopy 466', expected=466, responses_per_program=16, checkpoints=list(KS),
        source_pool_fingerprint=manifest['fingerprint'], new_model_calls=0, training_runs=0,
        inference_filter='PreFrama + Houdini independently for each response, or jointly for the prefix union; no sampled-positive prefilter',
        wp=dict(timeout=5, parallel=2, provers='alt-ergo,z3'),
        sampler=dict(n_runs=12, seed=0, negative_sampler='structured'),
        coverage='Fraction of common negative trace groups rejected by any retained clause; unknown evaluations do not reject',
        selection='Highest negative-trace coverage among first N independently filtered responses; tie -> earliest response; no negative groups -> earliest response',
        oracle='Evaluation-only upper bound: any independently filtered response among first N proves original target',
        selection_sees_targets=False, target_use='Restored only for final proof; oracle metric is not a deployable selector',
        comparison_scope='Inference and label-quality mechanism; does not measure retrained SFT outcomes',
        costs='Generation is shared and already incurred; new work is local filtering, coverage scoring, and proof only. Reused proof costs are not zero-cost inference claims.',
        cases=cases, frozen_inputs=frozen, frozen_sources=manifest['sources'], driver_sha256=sha(__file__))
    old_path = RUN/'protocol.json'
    if old_path.exists():
        old = read(old_path)
        for field in ['cases', 'frozen_inputs', 'frozen_sources', 'driver_sha256', 'selection', 'sampler', 'wp']:
            if old[field] != protocol[field]:
                raise ValueError('Resume protocol drift: ' + field)
        protocol = old
    else:
        atomic(old_path, protocol)
        (RUN/'driver.py').write_bytes(Path(__file__).read_bytes())
    atomic(EXPORT/'protocol.json', protocol)
    jobs.sort(key=lambda j:(0 if j['kind']=='sample' else 1, int(j['key'].split('_')[1]), j.get('set_key','')))
    return protocol, jobs


def worker(job):
    key, kind = job['key'], job['kind']
    label = key + ('_sample' if kind=='sample' else '_'+job['set_key'])
    scratch = RUN/'scratch'/label
    scratch.mkdir(exist_ok=True)
    os.chdir(scratch)
    tempfile.tempdir = str(scratch)
    os.environ['TMPDIR'] = str(scratch)
    start, usage = time.monotonic(), resource.getrusage(resource.RUSAGE_CHILDREN)
    original_run, fatal = subprocess.run, []
    log_path = RUN/'logs'/(label+'.jsonl.gz')
    state = dict(kind=kind, key=key, status='running', started_utc=utc(),
                 driver_sha256=sha(__file__))
    if kind=='single':
        state.update(set_key=job['set_key'], clauses=job['clauses'], reused=False,
                     generation_sha256=job['generation_sha256'])
    atomic(job['output'], state)

    def captured(*args, **kwargs):
        tick = time.monotonic()
        command = args[0] if args else kwargs.get('args')
        inputs = {}
        if kind=='single' and isinstance(command, (list, tuple)):
            for value in command:
                if isinstance(value, (str, os.PathLike)) and str(value).endswith('.c'):
                    p = Path(value)
                    if p.is_file(): inputs[str(p)] = p.read_text(errors='replace')
        try:
            result = original_run(*args, **kwargs)
            entry = dict(args=command, returncode=result.returncode, seconds=time.monotonic()-tick)
            if kind=='single':
                entry.update(stdout=result.stdout, stderr=result.stderr, c_inputs=inputs)
            else:
                entry.update(stderr=str(result.stderr or '')[:4000])
            if result.returncode < 0 or any(t in str(result.stderr).lower() for t in ['out of memory', 'segmentation fault']):
                fatal.append(entry)
        except Exception as error:
            entry = dict(args=command, error=str(error), seconds=time.monotonic()-tick, c_inputs=inputs)
            if isinstance(error, FileNotFoundError): fatal.append(entry)
            with gzip.open(log_path, 'at') as h: h.write(json.dumps(entry, default=str)+'\n')
            raise
        with gzip.open(log_path, 'at') as h: h.write(json.dumps(entry, default=str)+'\n')
        return result

    subprocess.run = captured
    try:
        if kind=='sample':
            examples = ExampleSampler(job['hidden_source'], n_runs=12, seed=0, negative_sampler='structured').sample()
            state.update(status='complete', hidden_sha256=job['hidden_sha256'],
                negative_states=[asdict(s) for s in examples.neg(0)], negative_groups=examples.groups(0),
                group_families=examples.group_families(0), stats=examples.stats.get(0, {}),
                positive_state_count=len(examples.pos(0)), scorable=len(examples.groups(0))>0)
        else:
            filt = auto_filter()
            assert filt.name == 'cascade(pre-frama->houdini)'
            survivors = filt.filter(parse_program(job['task'].hidden_source), 0, job['clauses'], positives=None)
            judgment = judge_invariants(job['task'], survivors)
            if fatal or judgment['verified'] is None:
                raise RuntimeError('Proof infrastructure failure: '+repr(judgment))
            state.update(status='complete', survivors=survivors, judgment=judgment, verified=judgment['verified'])
        if fatal: raise RuntimeError('Subprocess infrastructure failure')
    except Exception as error:
        state.update(status='error', error=f'{type(error).__name__}: {error}')
    finally:
        subprocess.run = original_run
    now_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    state.update(seconds=time.monotonic()-start, updated_utc=utc(),
                 child_cpu_seconds=now_usage.ru_utime+now_usage.ru_stime-usage.ru_utime-usage.ru_stime,
                 process_max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    atomic(job['output'], state)
    return {k:state.get(k) for k in ['kind','key','set_key','status','seconds','error','scorable']}


def selected_index(rejection_counts, n):
    # The selector's inputs contain no target verdicts or target-bearing source.
    return max(range(n), key=lambda i:(rejection_counts[i], -i))


def score_case(key, case):
    sp = RUN/'samples'/(key+'.json')
    if not sp.exists() or read(sp).get('status') != 'complete': return None
    sample = read(sp)
    candidates = []
    for ck in case['candidate_keys']:
        p = proof_path(key, ck)
        if not p.exists() or read(p).get('status') != 'complete': return None
        candidates.append(read(p))
    states = [State(**s) for s in sample['negative_states']]
    groups = sample['negative_groups']
    constants = state_external_integer_constants(parse_program(Path(case['hidden_path']).read_text()))

    def rejected(invariants):
        state_ids = RewardCalculator._rejected_set(states, invariants, constants)
        return RewardCalculator._to_groups(state_ids, groups)

    coverage_sets = [rejected(c['survivors']) for c in candidates]
    counts = [len(s) for s in coverage_sets]
    choices = {str(n):selected_index(counts, n) for n in KS}
    # Freeze all target-blind selections before consuming any verification verdict.
    selection = dict(key=key, indices_zero_based=choices, rejection_counts=counts,
        negative_groups=len(groups), tie_break='earliest', target_information_used=False)
    result = dict(key=key, case_id=case['case_id'], scorable=bool(groups), selection=selection,
                  positive_states=sample['positive_state_count'], sampler_seconds=sample['seconds'], rows=[])
    for n in KS:
        compose = read(case['compose'][str(n)]['path'])
        selected = candidates[choices[str(n)]]
        compose_rejected = rejected(compose['survivors'])
        best_count = counts[choices[str(n)]]
        result['rows'].append(dict(key=key, n=n, scorable=bool(groups), negative_groups=len(groups),
            selected_response=choices[str(n)]+1,
            best_coverage_verified=selected['verified'],
            oracle_best_verified=any(c['verified'] for c in candidates[:n]),
            compose_verified=compose['verified'],
            best_rejected=best_count, compose_rejected=len(compose_rejected),
            best_coverage=best_count/len(groups) if groups else None,
            compose_coverage=len(compose_rejected)/len(groups) if groups else None,
            best_survivor_clauses=len(selected['survivors']),
            compose_survivor_clauses=len(compose['survivors']),
            best_meets_half_coverage=bool(groups) and best_count/len(groups)>=0.5,
            compose_meets_half_coverage=bool(groups) and len(compose_rejected)/len(groups)>=0.5,
            compose_below_best_coverage=len(compose_rejected)<best_count))
    first = result['rows'][0]
    assert first['best_coverage_verified'] == first['oracle_best_verified'] == first['compose_verified']
    assert first['best_rejected'] == first['compose_rejected']
    result['candidates'] = [dict(index=i+1, set_key=case['candidate_keys'][i],
        n_clauses=len(c['clauses']), n_survivors=len(c['survivors']), verified=c['verified'],
        rejected=counts[i], coverage=counts[i]/len(groups) if groups else None, reused=c.get('reused',False),
        seconds=c['seconds'], child_cpu_seconds=c.get('child_cpu_seconds',0)) for i,c in enumerate(candidates)]
    atomic(RUN/'per_program'/(key+'.json'), result)
    return result


def export(protocol, started, selected_keys=None):
    results = []
    for key, case in protocol['cases'].items():
        if selected_keys is not None and key not in selected_keys: continue
        cached = RUN/'per_program'/(key+'.json')
        result = read(cached) if cached.exists() else score_case(key, case)
        if result is not None: results.append(result)
    rows = [r for p in results for r in p['rows']]
    total = 466 if selected_keys is None else len(selected_keys)
    summaries = {}
    for n in KS:
        rs = [r for r in rows if r['n']==n]
        scored = [r for r in rs if r['scorable']]
        summary = dict(expected=total, complete=len(rs), scorable=len(scored))
        for metric in ['best_coverage','oracle_best','compose']:
            summary[metric] = dict(success=sum(r[metric+'_verified'] for r in rs),
                rate=sum(r[metric+'_verified'] for r in rs)/total if len(rs)==total else None)
        summary['compose_vs_oracle'] = dict(
            compose_only=sum(r['compose_verified'] and not r['oracle_best_verified'] for r in rs),
            oracle_only=sum(r['oracle_best_verified'] and not r['compose_verified'] for r in rs))
        summary['compose_vs_best_coverage'] = dict(
            compose_only=sum(r['compose_verified'] and not r['best_coverage_verified'] for r in rs),
            best_only=sum(r['best_coverage_verified'] and not r['compose_verified'] for r in rs))
        if scored:
            summary['coverage'] = dict(
                mean_best=sum(r['best_coverage'] for r in scored)/len(scored),
                mean_compose=sum(r['compose_coverage'] for r in scored)/len(scored),
                compose_strictly_greater=sum(r['compose_rejected']>r['best_rejected'] for r in scored),
                compose_lower=sum(r['compose_below_best_coverage'] for r in scored),
                best_accept_half=sum(r['best_meets_half_coverage'] for r in scored),
                compose_accept_half=sum(r['compose_meets_half_coverage'] for r in scored))
        summaries[str(n)] = summary
    errors=[]
    for folder in ['single_proofs','samples']:
        for path in (RUN/folder).glob('*.json'):
            value=read(path)
            if value.get('status')=='error':errors.append(dict(path=str(path),error=value.get('error')))
    report=dict(state='complete' if len(results)==total else 'running', updated_utc=utc(),
        mode='full' if selected_keys is None else 'pilot', expected=total, completed_programs=len(results),
        new_model_calls=0, training_runs=0, current_invocation_wall_seconds=time.monotonic()-started,
        summary=summaries, errors=errors)
    filename='result.json' if selected_keys is None else 'pilot_result.json'
    atomic(EXPORT/filename,report)
    if rows:
        out=io.StringIO();writer=csv.DictWriter(out,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        (EXPORT/('per_program.csv' if selected_keys is None else 'pilot_per_program.csv')).write_text(out.getvalue())
    lines=[f"state: {report['state']}; mode: {report['mode']}; programs: {len(results)}/{total}",
        'Model generation: shared historical GPT-5-nano/minimal 16-response pool; no new model calls.',
        'N | BestCoverage success | OracleBest success | Compose success | Scorable programs']
    for n in KS:
        s=summaries[str(n)]
        lines.append(f"{n} | {s['best_coverage']['success']} | {s['oracle_best']['success']} | {s['compose']['success']} | {s['scorable']}")
    lines.append(f'Errors: {len(errors)}')
    (EXPORT/('report.txt' if selected_keys is None else 'pilot_report.txt')).write_text('\n'.join(lines)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--workers',type=int,default=16)
    parser.add_argument('--pilot',type=int,default=0)
    parser.add_argument('--status',action='store_true')
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args()
    started=time.monotonic()
    protocol,jobs=prepare()
    selected_keys=None
    if args.pilot:
        keys=sorted(protocol['cases'],key=lambda k:int(k.split('_')[1]))
        indices=sorted({round(i*(len(keys)-1)/max(1,args.pilot-1)) for i in range(args.pilot)})
        selected_keys={keys[i] for i in indices}
        jobs=[j for j in jobs if j['key'] in selected_keys]
    print(json.dumps(dict(event='prepared',programs=466,selected=sorted(selected_keys) if selected_keys else 'all',
        pending=len(jobs),sampler_jobs=sum(j['kind']=='sample' for j in jobs),
        proof_jobs=sum(j['kind']=='single' for j in jobs),new_model_calls=0)),flush=True)
    if args.prepare_only:return
    if args.status:
        print(json.dumps(export(protocol,started,selected_keys),ensure_ascii=False),flush=True);return
    initial_pending=len(jobs)
    with ProcessPoolExecutor(max_workers=args.workers,mp_context=multiprocessing.get_context('spawn'),initializer=initialize) as pool:
        futures={pool.submit(worker,j):j for j in jobs}
        for completed,future in enumerate(as_completed(futures),1):
            try:result=future.result()
            except Exception as e:
                j=futures[future];result=dict(key=j['key'],kind=j['kind'],status='worker_error',error=str(e))
            if completed%10==0 or result['status']!='complete' or completed==initial_pending:
                print(json.dumps(dict(event='progress',completed=completed,total=initial_pending,last=result),ensure_ascii=False),flush=True)
            if completed%100==0 or completed==initial_pending:
                report=export(protocol,started,selected_keys)
                atomic(EXPORT/'runtime.json',dict(updated_utc=utc(),jobs_complete=completed,jobs_total=initial_pending,
                    completed_programs=report['completed_programs'],errors=report['errors'],workers=args.workers))
    report=export(protocol,started,selected_keys)
    print(json.dumps(dict(event='finished',state=report['state'],completed_programs=report['completed_programs'],
        expected=report['expected'],errors=len(report['errors']),wall_seconds=time.monotonic()-started)),flush=True)
    if report['state']!='complete':sys.exit(2)
    changed=[p for p,h in {**protocol['frozen_inputs'],**protocol['frozen_sources']}.items() if sha(p)!=h]
    atomic(EXPORT/'input_integrity.json',dict(checked=len(protocol['frozen_inputs'])+len(protocol['frozen_sources']),changed=changed))
    assert not changed


if __name__=='__main__':
    main()
