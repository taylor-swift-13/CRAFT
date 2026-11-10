"""Cumulative success over every cached iteration; no generation or source edits.

Only missing proofs needed to decide each cumulative OR are executed. A proved
success is an exact witness, so later unknown iterations need not be evaluated.
Generation tokens always include the full recorded budget, not hypothetical
early stopping. New proof costs are offline audit costs, not original latency.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import socket
import sys
import time

sys.dont_write_bytecode = True
OLD = Path('/home/yangfp/craft_empirical_runs/loopy_compose_refine_20261107')
NEW = Path('/home/yangfp/craft_empirical_runs/loopy_any_iteration_20261110')
OUT = Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20/outputs/loopy_any_iteration_20261110')
DRIVER = None
INPUT_HASHES = {}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


def frozen_read(path):
    path = Path(path)
    data = path.read_bytes()
    INPUT_HASHES[str(path)] = digest(data)
    return data


def frozen_json(path):
    return json.loads(frozen_read(path))


def no_network(*args, **kwargs):
    raise RuntimeError('Network and model calls are disabled for local rescoring')


def init_driver():
    global DRIVER
    os.environ['CRAFT_FRAMA_C_BIN'] = '/home/yangfp/.opam/frama-c.27.1/bin'
    spec = importlib.util.spec_from_file_location('frozen_loopy_driver', OLD / 'driver.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.RUN = NEW
    module.EXPORT = OUT
    module.credentials = no_network
    module.request = no_network
    module.configure_proof()
    module.ensure_frama_c_available()
    DRIVER = module
    socket.create_connection = no_network
    socket.socket.connect = no_network


def verify(payload):
    return DRIVER.proof_work(payload)


def semantic_key(job, invariants):
    content = dict(case=job['case'], visibility=job['visibility'],
                   original=digest(job['original']), hidden=digest(job['hidden']),
                   invariants=invariants)
    return digest(json.dumps(content, sort_keys=True, ensure_ascii=False))


def check_positive(record):
    if not record.get('verified'):
        return
    assert record['syntax_correct']
    assert len(record['survivors']) == len(record['invariant_valid'])
    assert all(record['invariant_valid'])
    assert record['target_valid'] and all(record['target_valid'])


def reference(record, path, origin):
    assert record['status'] == 'complete'
    check_positive(record)
    return dict(verified=bool(record['verified']), path=str(path), origin=origin,
                seconds=record['seconds'], cpu_seconds=record['cpu_seconds'])


def decide(points, budget):
    witnesses = [step for step in range(1, budget + 1)
                 if step in points and points[step]['verified']]
    missing = [step for step in range(1, budget + 1) if step not in points]
    if witnesses:
        return True, min(witnesses), missing
    if missing:
        return None, None, missing
    return False, None, []


def selfcheck():
    assert decide({1: {'verified': False}, 4: {'verified': True}}, 4)[0] is True
    assert decide({1: {'verified': False}, 4: {'verified': False}}, 4)[0] is None
    assert decide({i: {'verified': False} for i in range(1, 9)}, 8)[0] is False
    assert decide({2: {'verified': True}}, 4)[0] is True
    assert decide({7: {'verified': True}}, 4)[0] is None
    return dict(passed=True, checks=5)


def prepare():
    manifest = frozen_json(OLD / 'manifest.json')
    assert digest(frozen_read(OLD / 'driver.py')) == manifest['driver_sha256']
    drift = [name for name, expected in manifest['verifier_sources'].items()
             if digest(frozen_read(name)) != expected]
    if drift:
        raise RuntimeError('Verifier source drift: ' + repr(drift))
    jobs, states, cache, conflicts = {}, {}, {}, []
    ids = sorted([p.stem.split('.')[0] for p in (OLD / 'sources').glob('*.original.c')], key=int)
    assert len(ids) == 466
    for case_index, case in enumerate(ids):
        if case_index % 50 == 0:
            print(json.dumps(dict(stage='indexing_saved_responses', programs=case_index, total=466)), flush=True)
        original = frozen_read(OLD / 'sources' / f'{case}.original.c').decode()
        hidden = frozen_read(OLD / 'sources' / f'{case}.hidden.c').decode()
        for visibility in ['hidden', 'visible']:
            job = dict(case=case, visibility=visibility, original=original, hidden=hidden,
                       program=original if visibility == 'visible' else hidden,
                       fingerprint=manifest['fingerprint'])
            jobs[(visibility, case)] = job
            for strategy in ['compose', 'refine']:
                calls = []
                for step in range(1, 9):
                    name = 'initial_1' if step == 1 else f'{strategy}_{step}'
                    record = frozen_json(OLD / 'calls' / visibility / case / (name + '.json'))
                    assert record['status'] == 'complete'
                    assert record['fingerprint'] == manifest['fingerprint']
                    assert record['usage']
                    calls.append(dict(invariants=record['invariants'], usage=record['usage'],
                                      api_wall_seconds=record['api_wall_seconds']))
                candidates, points = {}, {}
                for step in range(1, 9):
                    invariants = ([inv for call in calls[:step] for inv in call['invariants']]
                                  if strategy == 'compose' else calls[step-1]['invariants'])
                    invariants = DRIVER.dedup_normalized(invariants)
                    key = semantic_key(job, invariants)
                    candidates[step] = dict(key=key, invariants=invariants)
                    if step in [1, 4, 8]:
                        name = 'shared_1' if step == 1 else f'{strategy}_{step}'
                        path = OLD / 'scores' / visibility / case / (name + '.json')
                        score = frozen_json(path)
                        assert score['fingerprint'] == manifest['fingerprint']
                        assert score['input_invariants'] == invariants
                        ref = reference(score, path, 'original_checkpoint')
                        points[step] = ref
                        if key in cache and cache[key]['verified'] != ref['verified']:
                            conflicts.append(dict(key=key, prior=cache[key], current=ref))
                        # Fixed insertion order, independent of success value.
                        cache.setdefault(key, ref)
                states[(visibility, strategy, case)] = dict(
                    job=job, calls=calls, candidates=candidates, points=points)
    expected = {('hidden','compose',1):217, ('hidden','compose',4):277,
                ('hidden','compose',8):304, ('hidden','refine',1):217,
                ('hidden','refine',4):226, ('hidden','refine',8):235,
                ('visible','compose',1):248, ('visible','compose',4):317,
                ('visible','compose',8):345, ('visible','refine',1):248,
                ('visible','refine',4):254, ('visible','refine',8):249}
    for (visibility, strategy, budget), count in expected.items():
        assert sum(s['points'][budget]['verified'] for (v,t,c),s in states.items()
                   if v == visibility and t == strategy) == count
    for path in sorted((NEW / 'proofs').glob('*.json')):
        record = json.loads(path.read_text())
        if record['status'] == 'complete':
            cache[path.stem] = reference(record, path, 'new_local_proof')
    return manifest, jobs, states, cache, conflicts


def apply_cache(states, cache):
    for state in states.values():
        for step, candidate in state['candidates'].items():
            if step not in state['points'] and candidate['key'] in cache:
                state['points'][step] = cache[candidate['key']]


def summary(states):
    groups, rows = [], []
    for (visibility, strategy, case), state in states.items():
        for budget in [1, 4, 8]:
            success, witness, missing = decide(state['points'], budget)
            old_success = state['points'][budget]['verified']
            checkpoint_union = any(state['points'][k]['verified'] for k in [1,4,8] if k <= budget)
            calls = state['calls'][:budget]
            usage = DRIVER.token_metrics(calls)
            rows.append(dict(visibility=visibility, strategy=strategy, case=case, budget=budget,
                             success=success, witness_step=witness,
                             witness_path=state['points'][witness]['path'] if witness else '',
                             untested_steps=missing, original_final_success=old_success,
                             checkpoint_1_4_8_union=checkpoint_union,
                             total_tokens=usage['total_tokens'], input_tokens=usage['input_tokens'],
                             output_tokens=usage['output_tokens'], api_seconds=usage['api_wall_seconds']))
    for visibility in ['hidden', 'visible']:
        for strategy in ['compose', 'refine']:
            for budget in [1, 4, 8]:
                subset = [r for r in rows if (r['visibility'],r['strategy'],r['budget']) == (visibility,strategy,budget)]
                success = sum(r['success'] is True for r in subset)
                unresolved = sum(r['success'] is None for r in subset)
                groups.append(dict(visibility=visibility, strategy=strategy, budget=budget,
                                   programs=len(subset), success=success, unresolved=unresolved,
                                   rate=success/466 if not unresolved else None,
                                   lower_rate=success/466, upper_rate=(success+unresolved)/466,
                                   original_final_success=sum(r['original_final_success'] for r in subset),
                                   old_checkpoint_union=sum(r['checkpoint_1_4_8_union'] for r in subset),
                                   mean_tokens=sum(r['total_tokens'] for r in subset)/466,
                                   mean_api_seconds=sum(r['api_seconds'] for r in subset)/466))
    return groups, rows


def progress(states, started, completed, active, errors, stage):
    groups, _ = summary(states)
    result = dict(state=stage, updated_utc=now(), elapsed_seconds=time.monotonic()-started,
                  completed_new_proofs=completed, active_proofs=active, errors=errors, groups=groups)
    write_json(OUT/'progress.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='groups'}), flush=True)
    return result


def run(states, cache, jobs, workers, started):
    completed = 0
    errors = {}
    last_progress = 0
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('fork'),
                             initializer=init_driver) as pool:
        for budget in [4,8]:
            active, keys_active = {}, set()
            while True:
                apply_cache(states,cache)
                for state in states.values():
                    if len(active) >= workers:
                        break
                    value, _, missing = decide(state['points'],budget)
                    if value is not None:
                        continue
                    # One outstanding proof per trajectory, and reuse identical candidates.
                    if any(state['candidates'][step]['key'] in keys_active for step in missing):
                        continue
                    step = next((k for k in missing if errors.get(state['candidates'][k]['key'],{}).get('attempts',0)<2), None)
                    if step is None:
                        continue
                    candidate=state['candidates'][step];key=candidate['key']
                    label='candidate_'+key[:24]
                    payload=DRIVER.payload(state['job'],'score',label,NEW/'proofs'/(key+'.json'),candidate['invariants'])
                    future=pool.submit(verify,payload)
                    active[future]=(key,payload);keys_active.add(key)
                if time.monotonic()-last_progress>25:
                    progress(states,started,completed,len(active),errors,f'budget_{budget}')
                    last_progress=time.monotonic()
                if not active:
                    break
                ready,_=wait(active,timeout=10,return_when=FIRST_COMPLETED)
                for future in ready:
                    key,payload=active.pop(future);keys_active.remove(key)
                    try:
                        record=future.result()
                        if record['status']!='complete':
                            raise RuntimeError(record.get('error','incomplete local proof'))
                        cache[key]=reference(record,payload['result_path'],'new_local_proof')
                        completed+=1
                        errors.pop(key,None)
                    except Exception as exc:
                        attempts=errors.get(key,{}).get('attempts',0)+1
                        errors[key]=dict(attempts=attempts,error=f'{type(exc).__name__}: {exc}')
            apply_cache(states,cache)
    return completed,errors


def finish(manifest, states, conflicts, completed, errors, started):
    groups,rows=summary(states)
    changed=[path for path,expected in INPUT_HASHES.items() if digest(Path(path).read_bytes())!=expected]
    assert not changed, changed
    unresolved=sum(g['unresolved'] for g in groups)
    points=[]
    for (visibility,strategy,case),state in states.items():
        for step in range(1,9):
            ref=state['points'].get(step)
            points.append(dict(visibility=visibility,strategy=strategy,case=case,step=step,
                               candidate_key=state['candidates'][step]['key'],
                               status='proved' if ref else 'unneeded_after_success_witness',
                               **(ref or {})))
        for a,b in [(1,4),(4,8)]:
            va=decide(state['points'],a)[0];vb=decide(state['points'],b)[0]
            assert not (va is True and vb is False)
    for visibility in ['hidden','visible']:
        for case in sorted({r['case'] for r in rows}):
            one=[r for r in rows if r['visibility']==visibility and r['case']==case and r['budget']==1]
            assert one[0]['success']==one[1]['success']
    new_records=[json.loads(p.read_text()) for p in (NEW/'proofs').glob('*.json')]
    result=dict(status='complete' if not unresolved else 'unresolved',generated_utc=now(),
                source_run=str(OLD),proof_run=str(NEW),groups=groups,
                protocol='Success within budget n iff any independent repair candidate, or any composition prefix union, at steps 1..n proves the original targets after the original target-free Houdini filter.',
                trajectories='All original fixed-length trajectories are unchanged. Offline successes never enter generation feedback. No early stopping or regeneration.',
                cost_scope='Token/API costs use all first n recorded calls. Earlier proof checks are now part of offline cumulative scoring; old final-only service-time comparisons do not apply.',
                new_model_calls=0,new_model_tokens=0,new_training_runs=0,
                new_proof_records=len(new_records),proof_errors=errors,cache_conflicts=conflicts,
                new_proof_cpu_seconds=sum(r.get('cpu_seconds',0) for r in new_records),
                new_proof_accumulated_seconds=sum(r.get('seconds',0) for r in new_records),
                rescore_elapsed_seconds=time.monotonic()-started,
                frozen_inputs_checked=len(INPUT_HASHES),changed_frozen_inputs=changed,
                unresolved_total=unresolved)
    write_json(OUT/'result.json',result)
    write_json(OUT/'proof_references.json',points)
    write_json(OUT/'input_integrity.json',dict(hashes=INPUT_HASHES,changed=changed))
    for filename,items in [('summary.csv',groups),('per_program.csv',rows)]:
        with (OUT/filename).open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(items[0]));writer.writeheader();writer.writerows(items)
    lines=['# Cumulative success over every iteration','',
           '| Visibility | Strategy | Budget | Success / 466 | Rate | Unresolved | Mean tokens |',
           '|---|---|---:|---:|---:|---:|---:|']
    for g in groups:
        rate=f"{100*g['rate']:.2f}%" if g['rate'] is not None else 'pending'
        lines.append(f"| {g['visibility']} | {g['strategy']} | {g['budget']} | {g['success']} | {rate} | {g['unresolved']} | {g['mean_tokens']:.2f} |")
    lines+=['',result['protocol'],'',result['trajectories'],'',result['cost_scope'],'',
            'For a positive cumulative result, one explicit proof witness suffices. A negative result requires all iterations within the budget to be checked. Repeated identical inputs reuse existing proofs. Original checkpoint records are not overwritten.',
            '',f"New model calls: 0. New local proof records: {len(new_records)}. Frozen input changes: {len(changed)}."]
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n')
    progress(states,started,completed,0,errors,result['status'])
    print(json.dumps(dict(status=result['status'],groups=groups)),flush=True)
    return 0 if not unresolved else 2


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--workers',type=int,default=12)
    parser.add_argument('--plan-only',action='store_true');args=parser.parse_args()
    started=time.monotonic();OUT.mkdir(parents=True,exist_ok=True);NEW.mkdir(parents=True,exist_ok=True)
    init_driver();write_json(OUT/'selfcheck.json',selfcheck())
    manifest,jobs,states,cache,conflicts=prepare();apply_cache(states,cache)
    write_json(OUT/'protocol.json',dict(source_fingerprint=manifest['fingerprint'],source_run=str(OLD),
        response_steps=list(range(1,9)),report_budgets=[1,4,8],workers=args.workers,
        shared_initial=True,model_calls=0,network_disabled=True,early_stop=False,
        same_verifier_sources=True,driver_sha256=digest(Path(__file__).read_bytes())))
    progress(states,started,0,0,{},'planned')
    if args.plan_only:
        groups,_=summary(states);print(json.dumps(dict(groups=groups,cache_conflicts=conflicts)),flush=True);return 0
    completed,errors=run(states,cache,jobs,args.workers,started)
    return finish(manifest,states,conflicts,completed,errors,started)


if __name__=='__main__':
    raise SystemExit(main())
