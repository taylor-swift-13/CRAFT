"""User-requested completion of the 35 unresolved @16 pools, unchanged requests.

Retain all previous calls, errors, recovery metadata, and successful proofs.
One bounded recovery pass; another explicit invalid_prompt remains unresolved.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import sys

sys.dont_write_bytecode=True
import craft16_minimal_v2 as core

ROUND='20261108_remaining'


def recover(job):
    key=job['key'];path=core.OUT/'generations'/(key+'.json')
    marker=core.OUT/('recovery_state_'+ROUND)/(key+'.json')
    if marker.exists():return json.loads(marker.read_text())
    state=json.loads(path.read_text())
    if state['status']!='blocked':return dict(key=key,status='not_blocked')
    expected=[{'role':'system','content':core.prompts.system_prompt()},
              {'role':'user','content':core.prompts.GENERATE_PROMPT.format(program=job['task'].hidden_source)}]
    if state['messages']!=expected or state['fingerprint']!=job['fingerprint']:
        raise ValueError('Request or protocol drift')
    backup=core.OUT/('rejection_archive_'+ROUND)/(key+'.json')
    if backup.exists():raise ValueError('Refusing to overwrite recovery snapshot')
    backup.write_bytes(path.read_bytes())
    old_calls=list(state['calls']);old_call_hash=core.sha256_text(json.dumps(old_calls,sort_keys=True,ensure_ascii=False))
    core.atomic(marker,dict(key=key,status='started',utc=core.now(),before_sha256=core.digest(backup)))
    state['recovery_failed_history']=list(state.get('recovery_failed_history',[]))+list(state.pop('pending_attempts',[]))
    state.setdefault('recovery_rounds',[]).append(dict(round=ROUND,before_record=str(backup),
        before_sha256=core.digest(backup),started_utc=core.now(),driver_sha256=core.digest(__file__)))
    state.pop('error',None);state.pop('blocked_reason',None);state['status']='pending'
    core.atomic(path,state)
    state=core.generate(job)
    preserved=core.sha256_text(json.dumps(state['calls'][:len(old_calls)],sort_keys=True,ensure_ascii=False))==old_call_hash
    if not preserved:raise AssertionError('Previous accepted calls were changed')
    if len(state['calls'])>4:raise AssertionError('Exceeded @16 successful response budget')
    result=dict(key=key,status=state['status'],previous_batches=len(old_calls),batches=len(state['calls']),
                new_batches=len(state['calls'])-len(old_calls),previous_calls_unchanged=preserved,
                before_record=str(backup),updated_utc=core.now())
    core.atomic(marker,result);return result


def main():
    jobs,manifest,_=core.prepare()
    for name in ['rejection_archive_'+ROUND,'recovery_state_'+ROUND]:(core.OUT/name).mkdir(exist_ok=True)
    selected=[];unchanged={}
    for job in jobs:
        path=core.OUT/'generations'/(job['key']+'.json');state=json.loads(path.read_text())
        if state['status']=='blocked':selected.append(job)
        elif state['status']=='complete':
            unchanged[str(path)]=core.digest(path)
            vp=core.OUT/'verification'/(job['key']+'.json')
            if vp.exists():unchanged[str(vp)]=core.digest(vp)
    addendum_path=core.OUT/('recovery_addendum_'+ROUND+'.json')
    if addendum_path.exists():raise RuntimeError('This recovery pass is already recorded')
    addendum=dict(round=ROUND,base_protocol=manifest['fingerprint'],started_utc=core.now(),workers=16,
        authorization='User explicitly requested processing the remaining 35 unresolved items.',
        selected=[j['key'] for j in selected],script_sha256=core.digest(__file__),
        policy='Reuse every accepted original response; only complete missing n=4 batches. Identical prompts, model, endpoint and parameters. Stop a program on renewed invalid_prompt; preserve all errors.',
        expected_missing_batches=sum(4-len(json.loads((core.OUT/'generations'/(j['key']+'.json')).read_text())['calls']) for j in selected),
        earlier_complete_file_hashes=unchanged)
    core.atomic(addendum_path,addendum);core.atomic(core.EXPORT/addendum_path.name,addendum)
    print(json.dumps(dict(selected=len(selected),missing_batches=addendum['expected_missing_batches'],workers=16)),flush=True)
    results=[]
    with ThreadPoolExecutor(max_workers=16) as pool:
        for future in as_completed([pool.submit(recover,j) for j in selected]):
            result=future.result();results.append(result);print(json.dumps(result),flush=True);core.summary(jobs)
    changed=[p for p,h in unchanged.items() if core.digest(p)!=h]
    report=dict(complete=True,programs=len(selected),recovered=sum(r['status']=='complete' for r in results),
        still_pending=sum(r['status']=='blocked' for r in results),
        errors=sum(r['status'] not in ['complete','blocked'] for r in results),
        extra_successful_batches=sum(r.get('new_batches',0) for r in results),
        previous_complete_files_checked=len(unchanged),changed_previous_complete_files=changed,updated_utc=core.now())
    core.atomic(core.EXPORT/('recovery_summary_'+ROUND+'.json'),report)
    if changed:raise AssertionError('Earlier complete files changed')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
