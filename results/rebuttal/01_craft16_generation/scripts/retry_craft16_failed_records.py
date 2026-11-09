"""Retry the remaining failed executions without dropping benchmark programs.

Clear active error flags only after retaining an immutable snapshot. Keep every
accepted response, the original request, and all previously completed proofs.
"""
from concurrent.futures import ThreadPoolExecutor,as_completed
import json
import sys

sys.dont_write_bytecode=True
import recover_craft16_remaining as recovery

recovery.ROUND='20261108_user_retry_14'
core=recovery.core


def main():
    jobs,manifest,_=core.prepare();selected=[];unchanged={}
    for job in jobs:
        p=core.OUT/'generations'/(job['key']+'.json');g=json.loads(p.read_text())
        if g['status']=='blocked':selected.append(job)
        elif g['status']=='complete':
            unchanged[str(p)]=core.digest(p)
            v=core.OUT/'verification'/(job['key']+'.json')
            if v.exists():unchanged[str(v)]=core.digest(v)
    for folder in ['rejection_archive_'+recovery.ROUND,'recovery_state_'+recovery.ROUND]:(core.OUT/folder).mkdir(exist_ok=True)
    target=core.OUT/('recovery_addendum_'+recovery.ROUND+'.json')
    if target.exists():raise RuntimeError('This retry is already recorded; refusing duplicate requests')
    addendum=dict(round=recovery.ROUND,started_utc=core.now(),base_protocol=manifest['fingerprint'],
        authorization='User requested rerunning remaining failed cases and clearing failed execution state.',
        workers=4,selected=[j['key'] for j in selected],script_sha256=core.digest(__file__),
        request_policy='One unchanged resume pass; normal transient retries remain enabled. No prompt, model, sampling, or batch-size changes.',
        deletion_policy='Reset active error flags; immutable old records and all successful answers are preserved. No benchmark program or genuine negative proof is removed.',
        expected_missing_batches=sum(4-len(json.loads((core.OUT/'generations'/(j['key']+'.json')).read_text())['calls']) for j in selected),
        earlier_complete_file_hashes=unchanged)
    core.atomic(target,addendum);core.atomic(core.EXPORT/target.name,addendum)
    print(json.dumps(dict(selected=len(selected),missing_batches=addendum['expected_missing_batches'],workers=4)),flush=True)
    results=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for f in as_completed([pool.submit(recovery.recover,j) for j in selected]):
            r=f.result();results.append(r);print(json.dumps(r),flush=True);core.summary(jobs)
    changed=[p for p,h in unchanged.items() if core.digest(p)!=h]
    report=dict(complete=True,programs=len(selected),recovered=sum(r['status']=='complete' for r in results),
        still_pending=sum(r['status']=='blocked' for r in results),errors=sum(r['status'] not in ['complete','blocked'] for r in results),
        extra_successful_batches=sum(r['new_batches'] for r in results),
        earlier_complete_files_checked=len(unchanged),changed_earlier_complete_files=changed,updated_utc=core.now())
    core.atomic(core.EXPORT/('recovery_summary_'+recovery.ROUND+'.json'),report)
    if changed:raise AssertionError('An earlier complete result changed')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
