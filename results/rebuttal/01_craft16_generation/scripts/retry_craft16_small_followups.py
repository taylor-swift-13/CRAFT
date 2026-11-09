"""At most two small unchanged-request followups for the user-requested retry."""
import json
import sys
import time

sys.dont_write_bytecode=True
import recover_craft16_remaining as recovery

core=recovery.core


def main():
    jobs,manifest,_=core.prepare();reports=[]
    for number in [1,2]:
        recovery.ROUND=f'20261108_user_retry_followup{number}'
        target=core.OUT/('recovery_addendum_'+recovery.ROUND+'.json')
        if target.exists():raise RuntimeError('Followup already recorded; no duplicate execution')
        selected=[];unchanged={}
        for job in jobs:
            p=core.OUT/'generations'/(job['key']+'.json');g=json.loads(p.read_text())
            if g['status']=='blocked':selected.append(job)
            elif g['status']=='complete':
                unchanged[str(p)]=core.digest(p)
                vp=core.OUT/'verification'/(job['key']+'.json')
                if vp.exists() and json.loads(vp.read_text())['status']=='complete':unchanged[str(vp)]=core.digest(vp)
        if not selected:break
        for folder in ['rejection_archive_'+recovery.ROUND,'recovery_state_'+recovery.ROUND]:(core.OUT/folder).mkdir(exist_ok=True)
        addendum=dict(round=recovery.ROUND,started_utc=core.now(),base_protocol=manifest['fingerprint'],
            authorization='User requested rerunning failed executions; same request, no benchmark exclusion.',
            workers=1,selected=[j['key'] for j in selected],script_sha256=core.digest(__file__),
            policy='One serial resume pass. Two such followup passes maximum. Identical model, prompt, sampling parameters and n=4 batch size; preserve accepted responses and old errors.',
            earlier_complete_file_hashes=unchanged)
        core.atomic(target,addendum);core.atomic(core.EXPORT/target.name,addendum)
        results=[]
        for job in selected:
            r=recovery.recover(job);results.append(r);print(json.dumps(r),flush=True);core.summary(jobs)
        changed=[p for p,h in unchanged.items() if core.digest(p)!=h]
        report=dict(round=recovery.ROUND,programs=len(selected),recovered=sum(r['status']=='complete' for r in results),
            pending=sum(r['status']=='blocked' for r in results),new_batches=sum(r['new_batches'] for r in results),
            earlier_complete_files_checked=len(unchanged),changed_earlier_complete_files=changed,updated_utc=core.now())
        core.atomic(core.EXPORT/('recovery_summary_'+recovery.ROUND+'.json'),report)
        if changed:raise AssertionError('A previous complete file changed')
        reports.append(report);print(json.dumps(report),flush=True)
        if not report['pending']:break
        if number==1:time.sleep(10)
    core.atomic(core.EXPORT/'user_retry_followups_summary.json',dict(passes=reports,finished_utc=core.now()))


if __name__=='__main__':main()
