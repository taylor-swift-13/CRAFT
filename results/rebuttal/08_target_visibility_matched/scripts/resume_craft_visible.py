"""Bounded unchanged-request completion, preserving all failures and accepted calls.

The target-hidden reference used the same unchanged-request recovery procedure.
No prompt, endpoint, safety setting, or sampling parameter is changed.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import time

import craft16_target_visible as core


def recover(job, root):
    path = core.OUT / 'generations' / (job['key'] + '.json')
    state = json.loads(path.read_text())
    if state['status'] not in ['blocked', 'error']:
        raise ValueError('Only incomplete executions may be resumed')
    expected = [{'role':'system','content':core.prompts.system_prompt()},
                {'role':'user','content':core.prompts.GENERATE_PROMPT.format(program=job['task'].source_path.read_text())}]
    if state['messages'] != expected or state['fingerprint'] != job['fingerprint']:
        raise ValueError('Unchanged-request alignment check failed')
    backup = root / 'before' / path.name
    backup.write_bytes(path.read_bytes())
    old_calls = list(state['calls'])
    state.setdefault('resume_rounds', []).append(dict(before=str(backup), before_sha256=core.digest(backup), utc=core.now()))
    state.pop('error', None); state.pop('blocked_reason', None); state.pop('pending_attempts', None)
    state['status'] = 'pending'
    core.atomic(path, state)
    result = core.generate(job)
    if result['calls'][:len(old_calls)] != old_calls or len(result['calls']) > 4:
        raise AssertionError('Accepted response pool changed or exceeded its budget')
    row = dict(key=job['key'], status=result['status'], previous_batches=len(old_calls),
               accepted_batches=len(result['calls']), new_batches=len(result['calls'])-len(old_calls),
               original_calls_unchanged=True, updated_utc=core.now())
    core.atomic(root/'outcomes'/path.name, row)
    return row


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--round', required=True)
    parser.add_argument('--workers', type=int, default=12)
    args=parser.parse_args()
    if not args.round.isalnum(): parser.error('round must be alphanumeric')
    jobs, manifest, _=core.prepare()
    root=core.OUT/'resumes'/args.round
    root.mkdir(parents=True, exist_ok=False)
    (root/'before').mkdir(); (root/'outcomes').mkdir()
    selected=[]; unchanged={}
    for job in jobs:
        p=core.OUT/'generations'/(job['key']+'.json')
        if not p.exists(): raise ValueError('Initial full generation must finish first')
        state=json.loads(p.read_text())
        if state['status'] in ['blocked','error']: selected.append(job)
        elif state['status']=='complete': unchanged[str(p)]=core.digest(p)
        else: raise ValueError('Generation is still running')
    addendum=dict(round=args.round, started_utc=core.now(), fingerprint=manifest['fingerprint'],
        selected=[j['key'] for j in selected], workers=args.workers, driver_sha256=core.digest(__file__),
        policy='One bounded unchanged-request resume pass, matching the reference hidden run recovery procedure. Retain every accepted call and every error. Stop each program on renewed invalid_prompt. No prompt, endpoint, safety setting, model, batch size or sampling change; no replacement of successful responses.',
        previous_complete_generation_hashes=unchanged)
    core.atomic(root/'addendum.json',addendum)
    rows=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed([pool.submit(recover,job,root) for job in selected]):
            row=future.result(); rows.append(row)
            print(json.dumps(row),flush=True)
    changed=[p for p,h in unchanged.items() if core.digest(p)!=h]
    summary=dict(selected=len(rows), complete=sum(r['status']=='complete' for r in rows),
        unresolved=sum(r['status']!='complete' for r in rows),
        extra_accepted_batches=sum(r['new_batches'] for r in rows), changed_complete_generations=changed,
        finished_utc=core.now())
    core.atomic(root/'summary.json',summary)
    core.atomic(core.EXPORT/('resume_'+args.round+'.json'),summary)
    if changed: raise AssertionError('Previously completed generation changed')
    print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
