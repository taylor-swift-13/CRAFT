"""Finalize current @16 views after retry; retain previous snapshots in history."""
import csv
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import time

sys.dont_write_bytecode=True
import craft16_minimal_v2 as core


def alive(pid):
    try:return Path('/proc',str(pid),'stat').read_text().split()[2]!='Z'
    except FileNotFoundError:return False


def main():
    runtime=json.loads((core.EXPORT/'user_retry14_runtime.json').read_text());pid=runtime['verification_pid']
    status=core.EXPORT/'user_retry14_completion_status.json'
    core.atomic(status,dict(state='waiting_for_local_verification',verification_pid=pid,started_utc=core.now()))
    while alive(pid):time.sleep(5)
    jobs,manifest,_=core.prepare();records=[];old_baseline=json.loads((core.OUT/'recovery_addendum_20261108_user_retry_14.json').read_text())
    changed=[p for p,h in old_baseline['earlier_complete_file_hashes'].items() if core.digest(p)!=h]
    for j in jobs:
        gp=core.OUT/'generations'/(j['key']+'.json');g=json.loads(gp.read_text())
        vp=core.OUT/'verification'/(j['key']+'.json');v=json.loads(vp.read_text()) if vp.exists() else {}
        records.append(dict(key=j['key'],suite=j['task'].suite,case_id=j['task'].case_id,
            generation_status=g['status'],available_responses=sum(len(c['raw_responses']) for c in g['calls']),
            verification_status=v.get('status','pending'),verified=v.get('verified',''),active_error=g.get('error',v.get('error','')),
            known_tokens=sum((c.get('usage') or {}).get('total_tokens',0) for c in g['calls'])))
    history=core.EXPORT/'history_before_user_retry_14';history.mkdir(exist_ok=True)
    history_hashes={}
    # Preserve the old derived views before refreshing their active versions.
    for name in ['remaining_35_resolution.csv','remaining_35_resolution.json','remaining_35_resolution.txt']:
        p=core.EXPORT/name;dest=history/name
        if p.exists() and not dest.exists():shutil.copy2(p,dest)
        if dest.exists():history_hashes[name]=core.digest(dest)
    initial=json.loads((core.OUT/'recovery_addendum_20261108_remaining.json').read_text())
    keyset=set(initial['selected']);selected=[r for r in records if r['key'] in keyset]
    for name,rows in [('remaining_35_resolution.csv',selected),('case_status_current.csv',records)]:
        text=io.StringIO();writer=csv.DictWriter(text,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        (core.EXPORT/name).write_text(text.getvalue())
    retry_keys=set(old_baseline['selected'])
    summary=dict(state='complete' if all(r['generation_status']=='complete' and r['verification_status']=='complete' for r in records) else 'incomplete',
        expected=832,generated=sum(r['generation_status']=='complete' for r in records),
        verified=sum(r['verification_status']=='complete' for r in records),success=sum(r['verified'] is True for r in records),
        unresolved_api=sum(r['generation_status']!='complete' for r in records),
        verification_errors=sum(r['verification_status']=='error' for r in records),
        recovered_last_14=sum(r['key'] in retry_keys and r['generation_status']=='complete' for r in records),
        recovered_last_14_success=sum(r['key'] in retry_keys and r['verified'] is True for r in records),
        earlier_complete_files_checked=len(old_baseline['earlier_complete_file_hashes']),changed_earlier_complete_files=changed,
        archived_derived_view_hashes=history_hashes,known_tokens=sum(r['known_tokens'] for r in records),updated_utc=core.now())
    if summary['state']=='complete':summary['success_rate']=summary['success']/832
    core.atomic(core.EXPORT/'user_retry14_completion.json',summary)
    current35=dict(state=summary['state'],original_pending=35,
        recovered_full_pools=sum(r['generation_status']=='complete' for r in selected),
        still_provider_rejected=sum(r['generation_status']!='complete' for r in selected),
        newly_verified_full_pools=sum(r['verification_status']=='complete' for r in selected),
        new_full_pool_success=sum(r['verified'] is True for r in selected),
        complete_at16_scored=summary['verified'],complete_at16_success=summary['success'],
        prior_partial_results='Archived in history_before_user_retry_14; replaced by complete-pool scores in this current view.',updated_utc=core.now())
    core.atomic(core.EXPORT/'remaining_35_resolution.json',current35)
    lines=['CRAFT@16：最新补齐结果',f"完整回答：{summary['generated']}/832；验证完成：{summary['verified']}/832；成功：{summary['success']}",
        f"活动接口未决项：{summary['unresolved_api']}；验证执行错误：{summary['verification_errors']}",
        f"最后14项恢复：{summary['recovered_last_14']}；其中成功：{summary['recovered_last_14_success']}",
        f"此前完成文件检查：{summary['earlier_complete_files_checked']}；变化：{changed}",
        '旧错误状态及旧部分候选池统计已归档；当前视图保留全部832个程序并采用完整候选池结果。','']
    if summary['state']=='complete':lines.insert(2,f"全832成功率：{summary['success_rate']:.2%}")
    (core.EXPORT/'remaining_35_resolution.txt').write_text('\n'.join(lines))
    (core.EXPORT/'user_retry14_completion.txt').write_text('\n'.join(lines))
    if changed:raise AssertionError('Earlier complete results changed')
    # Allow the existing exporter to publish its final, fully scored summary.
    for _ in range(12):
        r=json.loads((core.EXPORT/'result.json').read_text())
        if r.get('state')=='complete':break
        time.sleep(5)
    core.atomic(status,dict(state='archiving_complete_results',updated_utc=core.now()))
    dest=core.EXPORT/'evidence_complete_20261108.tar.gz';temp=dest.with_suffix('.gz.tmp')
    with tarfile.open(temp,'w:gz',compresslevel=3) as tar:
        for p in sorted(core.OUT.iterdir()):
            if p.is_file() or p.name in ['sources','generations','verification','logs','invocations'] or p.name.startswith(('rejection_archive','recovery_state')):
                tar.add(p,arcname='craft16/'+p.name)
        tar.add(history,arcname='previous_derived_views')
        for p in Path(__file__).parent.glob('*craft16*.py'):tar.add(p,arcname='drivers/'+p.name)
    temp.replace(dest)
    core.atomic(core.EXPORT/'evidence_complete_20261108.sha256.json',dict(file=str(dest),sha256=core.digest(dest),bytes=dest.stat().st_size))
    core.atomic(status,dict(state=summary['state'],updated_utc=core.now()))
    print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
