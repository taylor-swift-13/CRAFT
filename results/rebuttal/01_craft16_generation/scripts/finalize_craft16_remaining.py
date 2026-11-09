"""No API access: finish cached partial-pool checks and preserve @16 evidence.

Partial pools are explicitly separate from complete compose@16 results.
"""
from concurrent.futures import ProcessPoolExecutor,as_completed
import csv
import gzip
import io
import json
import logging
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time

sys.dont_write_bytecode=True
import craft16_minimal_v2 as core

PARTIAL=Path('/tmp/craft16_partial_pools_20261108')


def partial_check(job):
    path=core.OUT/'generations'/(job['key']+'.json')
    original=path.read_bytes();g=json.loads(original)
    raw=[t for c in g['calls'] for t in c['raw_responses']]
    out=PARTIAL/'verification'/(job['key']+'.json');out.parent.mkdir(parents=True,exist_ok=True)
    if out.exists():
        previous=json.loads(out.read_text())
        if previous['source_generation_sha256']!=core.sha256_text(original.decode()):raise ValueError('Partial input changed')
        if previous['status']=='complete':return previous
    if g['status']!='blocked' or not 0<len(raw)<16:raise ValueError('Not an incomplete cached pool')
    scratch=PARTIAL/'scratch'/job['key'];scratch.mkdir(parents=True,exist_ok=True)
    os.chdir(scratch);tempfile.tempdir=str(scratch);os.environ['TMPDIR']=str(scratch)
    logs=PARTIAL/'logs';logs.mkdir(exist_ok=True);log=logs/(job['key']+'.jsonl.gz')
    old_run=subprocess.run;fatal=[];start=time.monotonic()
    result=dict(key=job['key'],suite=job['task'].suite,case_id=job['task'].case_id,status='running',
        scope='PARTIAL CACHED POOL ONLY; not a complete compose@16 result',
        available_responses=len(raw),source_generation_sha256=core.sha256_text(original.decode()),
        base_protocol=g['fingerprint'],started_utc=core.now())
    core.atomic(out,result)
    def captured(*args,**kwargs):
        tick=time.monotonic()
        try:
            r=old_run(*args,**kwargs)
            entry=dict(args=args[0] if args else kwargs.get('args'),returncode=r.returncode,
                       stdout=r.stdout,stderr=r.stderr,seconds=time.monotonic()-tick)
            if r.returncode<0 or any(s in str(r.stderr).lower() for s in ['out of memory','segmentation fault']):fatal.append(entry)
        except Exception as exc:
            entry=dict(args=str(args),error=str(exc),seconds=time.monotonic()-tick)
            if isinstance(exc,FileNotFoundError):fatal.append(entry)
            with gzip.open(log,'at') as f:f.write(json.dumps(entry,default=str)+'\n')
            raise
        with gzip.open(log,'at') as f:f.write(json.dumps(entry,default=str)+'\n')
        return r
    try:
        subprocess.run=captured
        rollouts=[core.extract_invariants(t,max_invariants=20) for t in raw]
        union=core.dedup_normalized(c for cs in rollouts for c in cs)
        filt=core.auto_filter()
        if filt.name!='cascade(pre-frama->houdini)':raise ValueError('Verifier changed')
        survivors=filt.filter(core.parse_program(job['task'].hidden_source),0,union,positives=None)
        judgment=core.judge_invariants(job['task'],survivors)
        if fatal or judgment['verified'] is None:raise RuntimeError('Verification infrastructure failed')
        result.update(status='complete',verified=judgment['verified'],rollouts=rollouts,union=union,
            survivors=survivors,judgment={k:judgment.get(k) for k in ['verified','judge_error','judge_seconds','invariants','invariant_count']})
    except Exception as exc:result.update(status='error',error=f'{type(exc).__name__}: {exc}')
    finally:subprocess.run=old_run
    result.update(seconds=time.monotonic()-start,updated_utc=core.now());core.atomic(out,result);return result


def archive():
    target=core.EXPORT/'evidence_after_recovery_20261108.tar.gz';temp=target.with_suffix('.gz.tmp')
    with tarfile.open(temp,'w:gz',compresslevel=3) as tar:
        for p in sorted(core.OUT.iterdir()):
            if p.name in ['sources','generations','verification','logs','invocations'] or p.is_file() or p.name.startswith(('rejection_archive','recovery_state')):
                tar.add(p,arcname='craft16/'+p.name)
        for folder in ['verification','logs']:
            p=PARTIAL/folder
            if p.exists():tar.add(p,arcname='partial_pools/'+folder)
        for p in [Path(__file__),Path(__file__).with_name('recover_craft16_remaining.py')]:tar.add(p,arcname='recovery_drivers/'+p.name)
    temp.replace(target)
    core.atomic(core.EXPORT/'evidence_after_recovery_20261108.sha256.json',dict(file=str(target),bytes=target.stat().st_size,sha256=core.digest(target)))


def main():
    runtime=json.loads((core.EXPORT/'remaining_runtime_20261108.json').read_text());pid=runtime['verification_pid']
    state=core.EXPORT/'remaining_completion_status_20261108.json'
    core.atomic(state,dict(state='waiting_for_complete_pool_verification',verification_pid=pid,started_utc=core.now()))
    def verifier_alive():
        try:
            stat=Path('/proc',str(pid),'stat').read_text().split()
            return len(stat)>2 and stat[2]!='Z'
        except FileNotFoundError:return False
    # Use idle verification capacity without exceeding 12 concurrent proof jobs.
    while verifier_alive():
        outstanding=0
        for gp in (core.OUT/'generations').glob('*.json'):
            generation=json.loads(gp.read_text())
            if generation['status']!='complete':continue
            vp=core.OUT/'verification'/gp.name
            if not vp.exists() or json.loads(vp.read_text())['status'] not in ['complete','error']:outstanding+=1
        if outstanding<=4:break
        time.sleep(5)
    logging.basicConfig(level=logging.CRITICAL)
    jobs,manifest,_=core.prepare();selected=[]
    for job in jobs:
        g=json.loads((core.OUT/'generations'/(job['key']+'.json')).read_text())
        if g['status']=='blocked':selected.append(job)
    core.atomic(state,dict(state='checking_partial_pools',programs=len(selected),updated_utc=core.now()))
    PARTIAL.mkdir(exist_ok=True)
    with ProcessPoolExecutor(max_workers=8,mp_context=multiprocessing.get_context('spawn')) as pool:
        futures=[pool.submit(partial_check,j) for j in selected]
        for future in as_completed(futures):
            r=future.result();print(json.dumps(dict(key=r['key'],scope='partial_pool',available=r['available_responses'],status=r['status'],verified=r.get('verified'))),flush=True)
    while verifier_alive():time.sleep(5)
    recovery=json.loads((core.EXPORT/'recovery_summary_20261108_remaining.json').read_text())
    addendum=json.loads((core.OUT/'recovery_addendum_20261108_remaining.json').read_text())
    rows=[]
    for key in addendum['selected']:
        g=json.loads((core.OUT/'generations'/(key+'.json')).read_text())
        vp=core.OUT/'verification'/(key+'.json');v=json.loads(vp.read_text()) if vp.exists() else {}
        pp=PARTIAL/'verification'/(key+'.json');partial=json.loads(pp.read_text()) if pp.exists() else {}
        rows.append(dict(key=key,available_responses=sum(len(c['raw_responses']) for c in g['calls']),
            generation_status=g['status'],complete_at16_verification_status=v.get('status','not_applicable'),
            complete_at16_success=v.get('verified',''),partial_pool_status=partial.get('status','not_applicable'),
            partial_pool_success=partial.get('verified',''),api_error=g.get('error','')))
    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (core.EXPORT/'remaining_35_resolution.csv').write_text(stream.getvalue())
    changed=[p for p,h in addendum['earlier_complete_file_hashes'].items() if core.digest(p)!=h]
    total=[]
    for j in jobs:
        p=core.OUT/'verification'/(j['key']+'.json')
        if p.exists():total.append(json.loads(p.read_text()))
    exact=[r for r in total if r['status']=='complete']
    summary=dict(state='complete_with_provider_rejections',original_pending=35,
        recovered_full_pools=recovery['recovered'],still_provider_rejected=recovery['still_pending'],
        newly_verified_full_pools=sum(r['complete_at16_verification_status']=='complete' for r in rows),
        new_full_pool_success=sum(r['complete_at16_success'] is True for r in rows),
        partial_pools_checked=sum(r['partial_pool_status']=='complete' for r in rows),
        partial_pool_success=sum(r['partial_pool_success'] is True for r in rows),
        partial_pool_errors=sum(r['partial_pool_status']=='error' for r in rows),
        complete_at16_scored=len(exact),complete_at16_success=sum(r.get('verified') is True for r in exact),
        earlier_complete_files_checked=len(addendum['earlier_complete_file_hashes']),changed_earlier_complete_files=changed,
        scope='Partial-pool scores are separate and are not included in the complete compose@16 numerator or denominator.',updated_utc=core.now())
    core.atomic(core.EXPORT/'remaining_35_resolution.json',summary)
    lines=['原CRAFT@16剩余35项处理结果',
        f"补齐完整16回答：{summary['recovered_full_pools']}；新验证完成：{summary['newly_verified_full_pools']}；其中成功：{summary['new_full_pool_success']}",
        f"供应商仍以invalid_prompt拒绝：{summary['still_provider_rejected']}；这部分没有计为完整@16失败。",
        f"已有部分候选池验证：{summary['partial_pools_checked']}；成功：{summary['partial_pool_success']}；执行错误：{summary['partial_pool_errors']}",
        f"完整@16候选池共验证：{summary['complete_at16_scored']}；成功：{summary['complete_at16_success']}",
        f"原已完成文件完整性检查：{summary['earlier_complete_files_checked']}；变更：{changed}",
        '部分池结果单独保存，不并入完整@16成功率。详见remaining_35_resolution.csv。','']
    (core.EXPORT/'remaining_35_resolution.txt').write_text('\n'.join(lines))
    if changed:raise ValueError('Earlier complete result changed')
    core.atomic(state,dict(state='archiving_evidence',updated_utc=core.now()))
    archive();core.atomic(state,dict(state=summary['state'],updated_utc=core.now()))
    print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
