"""Matched target-visible generation; filtering and judgments match hidden CRAFT@16."""
import argparse
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
import gzip
import hashlib
import json
import logging
import multiprocessing
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
REPO = Path('/home/yangfp/CRAFT')
CONTROL = Path('/tmp/craft16_minimal_20261107_v2')
OUT = Path('/home/yangfp/craft_empirical_runs/craft16_target_visible_20261110')
EXPORT = Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20/outputs/craft16_target_visible_20261110')
sys.path.insert(0, str(REPO))
from experiments.gpt5nano_full832.common import discover_tasks, ensure_frama_c_available, judge_invariants, sha256_text
from rl_pipeline.common import prompts
from rl_pipeline.common.program import parse_program
from rl_pipeline.common.state import extract_invariants, dedup_normalized
from rl_pipeline.reward.filters import auto_filter

STOP = threading.Event()


def now(): return datetime.now(timezone.utc).isoformat()
def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def atomic(p, x):
    p = Path(p); q = p.with_name(p.name + '.' + str(os.getpid()) + '.tmp')
    q.write_text(json.dumps(x, ensure_ascii=False, indent=2) + '\n'); q.replace(p)


def configuration():
    c = {}
    for line in Path('/home/yangfp/.config/craft/openlux.env').read_text().splitlines():
        line = line.strip().removeprefix('export ')
        if '=' not in line or line.startswith('#'): continue
        k, v = line.split('=', 1)
        try: parts = shlex.split(v, comments=True)
        except ValueError: continue
        c[k.strip()] = parts[0] if parts else ''
    base = c.get('OPENAI_BASE_URL', '').rstrip('/'); u = urlsplit(base)
    if u.scheme != 'https' or u.hostname != 'api.openlux.ai' or u.username or u.query or not c.get('OPENAI_API_KEY'):
        raise ValueError('Unexpected authorized API configuration')
    return base, c['OPENAI_API_KEY']


def prepare():
    for sub in ['sources', 'generations', 'verification', 'logs', 'scratch', 'invocations']:
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    EXPORT.mkdir(parents=True, exist_ok=True)
    os.environ['CRAFT_WP_TIMEOUT'] = '5'; os.environ['CRAFT_WP_PAR'] = '2'
    os.environ['CRAFT_WP_PROVERS'] = 'alt-ergo,z3'
    ensure_frama_c_available()
    tasks = discover_tasks(); jobs = []
    control = json.loads((CONTROL / 'manifest.json').read_text())
    if len(tasks) != 832: raise ValueError('Expected the identical 832-program benchmark')
    drift = [p for p, h in control['sources'].items() if digest(p) != h]
    if drift: raise ValueError('Control implementation drift: ' + repr(drift))
    original_hashes = {}
    for t in tasks:
        key = t.suite + '_' + t.case_id
        original = t.source_path.read_text(errors='ignore')
        original_hashes[str(t.source_path)] = digest(t.source_path)
        if original_hashes[str(t.source_path)] != control['original_inputs'].get(str(t.source_path)):
            raise ValueError('Control input hash mismatch: ' + key)
        if original != (CONTROL / 'sources' / (key + '.original.c')).read_text():
            raise ValueError('Frozen original source mismatch: ' + key)
        if t.hidden_source != (CONTROL / 'sources' / (key + '.hidden.c')).read_text():
            raise ValueError('Frozen hidden source mismatch: ' + key)
        p = OUT / 'sources' / (key + '.original.c'); h = OUT / 'sources' / (key + '.hidden.c')
        for path, value in [(p, original), (h, t.hidden_source)]:
            if path.exists() and path.read_text() != value: raise ValueError('Source snapshot drift')
            if not path.exists(): path.write_text(value)
        jobs.append(dict(task=replace(t, source_path=p), key=key,
                         source_sha256=t.source_sha256, hidden_source_sha256=t.hidden_source_sha256))
    files = {p for folder in ['rl_pipeline', 'src'] for p in (REPO / folder).rglob('*.py')}
    files.update([REPO / 'experiments/gpt5nano_full832/common.py', REPO / 'experiments/gpt5nano_full832/protocol.json'])
    files.update((REPO / 'prompt').glob('*.txt'))
    manifest = dict(schema=2, prompt_rejection_policy='Keep and skip explicitly rejected programs; do not retry or alter their prompts; continue independent programs', expected=832, model='gpt-5-nano', reasoning_effort='minimal',
                    endpoint_host='api.openlux.ai', n_rollouts=16, request_schedule=[4,4,4,4],
                    max_completion_tokens=8192, max_invariants_per_response=20,
                    unsupported_sampling_parameters_omitted=['temperature','top_p'],
                    target_hidden=False, target_visibility_scope='generation_only', target_free_filtering=True,
                    control_fingerprint=control['fingerprint'], control_manifest_sha256=digest(CONTROL / 'manifest.json'),
                    repair=False, wp=dict(timeout=5, parallel=2, provers='alt-ergo,z3'),
                    script_sha256=digest(__file__), sources={str(p): digest(p) for p in sorted(files)},
                    original_inputs=original_hashes,
                    note='Matched to new-prompt minimal hidden pool. Only generation program contains original targets; identical templates, sampling, parser, target-free filtering, and final judgment. Prefixes use the same ordered pool.')
    matched_fields = ['expected','model','reasoning_effort','endpoint_host','n_rollouts','request_schedule',
                      'max_completion_tokens','max_invariants_per_response','unsupported_sampling_parameters_omitted',
                      'repair','wp','sources','original_inputs']
    mismatch = [k for k in matched_fields if manifest[k] != control[k]]
    if mismatch: raise ValueError('Control protocol mismatch: ' + repr(mismatch))
    fp = sha256_text(json.dumps(manifest, sort_keys=True)); manifest['fingerprint'] = fp
    path = OUT / 'manifest.json'
    if path.exists() and json.loads(path.read_text()) != manifest: raise ValueError('Refusing mixed-protocol resume')
    if not path.exists(): atomic(path, manifest); (OUT / 'driver.py').write_bytes(Path(__file__).read_bytes())
    atomic(EXPORT / 'protocol.json', manifest)
    atomic(EXPORT / 'alignment_audit.json', dict(control_fingerprint=control['fingerprint'],
           experiment_fingerprint=fp, matched_fields=matched_fields, mismatches=mismatch,
           checked_programs=len(tasks), checked_code_and_prompt_files=len(control['sources']),
           intentional_difference='Generation user message uses frozen original.c instead of frozen hidden.c; all other inference and verification settings match.',
           operational_note='Concurrency and timestamps are recorded separately; API service execution is stochastic.'))
    for j in jobs: j['fingerprint'] = fp
    groups = {s:[j for j in jobs if j['task'].suite==s] for s in ['linear','NLA_lipus','Loopy']}
    ordered = [groups[s][i] for i in range(max(map(len, groups.values()))) for s in groups if i<len(groups[s])]
    pilot_keys = {groups[s][i]['key'] for s in groups for i in [0,len(groups[s])//3,2*len(groups[s])//3,len(groups[s])-1]}
    return ordered, manifest, pilot_keys


def generate(job):
    import openai
    task = job['task']; path = OUT / 'generations' / (job['key'] + '.json')
    state = json.loads(path.read_text()) if path.exists() else dict(key=job['key'], suite=task.suite,
            case_id=task.case_id, fingerprint=job['fingerprint'], status='running', calls=[], created_utc=now())
    if state['fingerprint'] != job['fingerprint']: raise ValueError('Generation protocol drift')
    if state['status'] in ['complete','blocked']: return state
    base, secret = configuration()
    def safe(e): return re.sub(r'sk-[A-Za-z0-9_-]{6,}', '[REDACTED]', str(e).replace(secret,'[REDACTED]'))[:1500]
    messages = [{'role':'system','content':prompts.system_prompt()},
                {'role':'user','content':prompts.GENERATE_PROMPT.format(program=task.source_path.read_text())}]
    state.update(status='running', messages=messages, prompt_sha256=sha256_text(json.dumps(messages,sort_keys=True)))
    atomic(path,state)
    client = openai.OpenAI(api_key=secret,base_url=base,max_retries=0,timeout=180)
    try:
        for batch in range(len(state['calls']),4):
            if STOP.is_set(): raise RuntimeError('Generation circuit breaker is open')
            attempts=[]; response=None; start=time.monotonic()
            for attempt in range(4):
                try:
                    request_started=time.monotonic()
                    response=client.chat.completions.create(model='gpt-5-nano',messages=messages,n=4,
                                 reasoning_effort='minimal',max_completion_tokens=8192)
                    success_seconds=time.monotonic()-request_started
                    break
                except Exception as e:
                    code=getattr(e,'status_code',None)
                    attempts.append(dict(type=type(e).__name__,http_status=code,error=safe(e),utc=now()))
                    state.setdefault('failed_request_history', []).append(dict(batch=batch, **attempts[-1]))
                    state['pending_attempts']=attempts;atomic(path,state)
                    if code==400 and (getattr(e,'code',None)=='invalid_prompt' or 'invalid_prompt' in str(e)):
                        state['blocked_reason']='invalid_prompt';raise
                    if code in [400,401,402,403,404] or any(s in str(e).lower() for s in ['insufficient_quota','billing_hard_limit']):
                        STOP.set();raise
                    if attempt==3: raise
                    time.sleep(min(2**(attempt+1),16))
            choices=list(response.choices)
            state['pending_response']=dict(batch=batch,requested_n=4,returned_model=response.model,
                  response_id=response.id,choice_count=len(choices),
                  raw_responses=[c.message.content or '' for c in choices],
                  usage=response.usage.model_dump() if response.usage else None,
                  saved_utc=now())
            atomic(path,state)
            if len(choices)!=4 or sorted(c.index for c in choices)!=list(range(4)):
                STOP.set(); raise RuntimeError('Batch endpoint did not return exactly four choices')
            if not response.model.startswith('gpt-5-nano'):
                STOP.set(); raise RuntimeError('Endpoint reported a different model')
            choices.sort(key=lambda x:x.index)
            call=dict(batch=batch,requested_n=4,returned_model=response.model,response_id=response.id,
                      raw_responses=[c.message.content or '' for c in choices],
                      finish_reasons=[c.finish_reason for c in choices],
                      usage=response.usage.model_dump() if response.usage else None,
                      seconds=time.monotonic()-start,successful_request_seconds=success_seconds,
                      failed_attempts=attempts,saved_utc=now())
            state['calls'].append(call);state.pop('pending_attempts',None);state.pop('pending_response',None);atomic(path,state)
        state.update(status='complete',updated_utc=now(),responses=[t for c in state['calls'] for t in c['raw_responses']])
        if len(state['responses'])!=16: raise RuntimeError('Incomplete response pool')
    except Exception as e: state.update(status='blocked' if state.get('blocked_reason') else 'error',error=safe(e),updated_utc=now())
    finally: client.close()
    atomic(path,state);return state


def verify(job):
    task=job['task'];path=OUT/'verification'/(job['key']+'.json')
    gen_path=OUT/'generations'/(job['key']+'.json');g=json.loads(gen_path.read_text())
    result=dict(key=job['key'],suite=task.suite,case_id=task.case_id,fingerprint=job['fingerprint'],
                generation_sha256=digest(gen_path),status='running',started_utc=now())
    atomic(path,result);old_run=subprocess.run;fatal=[];start=time.monotonic()
    scratch=OUT/'scratch'/job['key'];scratch.mkdir(exist_ok=True);tempfile.tempdir=str(scratch);os.environ['TMPDIR']=str(scratch);os.chdir(scratch)
    logpath=OUT/'logs'/(job['key']+'.jsonl.gz')
    try:
        if g['status']!='complete' or g['fingerprint']!=job['fingerprint']:raise ValueError('Incomplete generation')
        ensure_frama_c_available()
        def captured(*args,**kwargs):
            tick=time.monotonic()
            try:
                q=old_run(*args,**kwargs)
                entry=dict(args=args[0] if args else kwargs.get('args'),returncode=q.returncode,
                           stdout=q.stdout,stderr=q.stderr,seconds=time.monotonic()-tick)
                if q.returncode<0 or any(x in str(q.stderr).lower() for x in ['out of memory','segmentation fault']):fatal.append(entry)
            except Exception as e:
                entry=dict(args=str(args),error=str(e),seconds=time.monotonic()-tick)
                if isinstance(e,FileNotFoundError):fatal.append(entry)
                with gzip.open(logpath,'at') as f:f.write(json.dumps(entry,default=str)+'\n')
                raise
            with gzip.open(logpath,'at') as f:f.write(json.dumps(entry,default=str)+'\n')
            return q
        subprocess.run=captured
        rollouts=[extract_invariants(t,max_invariants=20) for t in g['responses']]
        union=dedup_normalized(c for cs in rollouts for c in cs)
        filt=auto_filter()
        if filt.name!='cascade(pre-frama->houdini)':raise RuntimeError('Non-authoritative filter')
        survivors=filt.filter(parse_program(task.hidden_source),0,union,positives=None)
        judgment=judge_invariants(task,survivors)
        if fatal or judgment['verified'] is None:raise RuntimeError('Verification execution failed or unavailable')
        result.update(status='complete',verified=judgment['verified'],rollouts=rollouts,union=union,
                      survivors=survivors,judgment={k:judgment.get(k) for k in ['verified','judge_error','judge_seconds','invariants','invariant_count']})
    except Exception as e:result.update(status='error',error=f'{type(e).__name__}: {e}')
    finally:subprocess.run=old_run
    result.update(seconds=time.monotonic()-start,updated_utc=now());atomic(path,result);return result


def summary(jobs):
    gens=[];ver=[]
    for j in jobs:
        for folder,collection in [('generations',gens),('verification',ver)]:
            p=OUT/folder/(j['key']+'.json')
            if p.exists():collection.append(json.loads(p.read_text()))
    calls=[c for g in gens for c in g.get('calls',[])];usage=[c['usage'] for c in calls if c.get('usage')]
    good=[v for v in ver if v['status']=='complete']
    report=dict(updated_utc=now(),expected=len(jobs),generated=sum(g['status']=='complete' for g in gens),
                generation_blocked=sum(g['status']=='blocked' for g in gens),generation_errors=sum(g['status']=='error' for g in gens),verified_complete=len(good),
                verification_errors=sum(v['status']=='error' for v in ver),success=sum(v['verified'] is True for v in good),
                recorded_calls=len(calls),recorded_tokens=sum(u.get('total_tokens',0) for u in usage),
                reasoning_tokens=sum((u.get('completion_tokens_details') or {}).get('reasoning_tokens',0) or 0 for u in usage),
                per_suite={s:dict(completed=sum(v['suite']==s for v in good),success=sum(v['suite']==s and v['verified'] is True for v in good)) for s in ['linear','NLA_lipus','Loopy']},
                note='Partial results are not a full-dataset rate; failed request billing may be unknown.')
    atomic(EXPORT/'progress.json',report);return report


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['audit','generate','verify']);p.add_argument('--workers',type=int,default=12)
    p.add_argument('--pilot',action='store_true');p.add_argument('--limit',type=int);p.add_argument('--watch',action='store_true');a=p.parse_args()
    logging.basicConfig(level=logging.CRITICAL)
    jobs,manifest,pilot=prepare();selected=[j for j in jobs if not a.pilot or j['key'] in pilot]
    if a.limit:selected=selected[:a.limit]
    atomic(OUT/'invocations'/(str(time.time_ns())+'.json'),dict(mode=a.mode,workers=a.workers,pilot=a.pilot,selected=len(selected),utc=now()))
    print(json.dumps(dict(mode=a.mode,selected=len(selected),workers=a.workers,fingerprint=manifest['fingerprint'])),flush=True)
    if a.mode=='audit':return
    if a.mode=='generate':
        with ThreadPoolExecutor(max_workers=a.workers) as pool:
            todo=[j for j in selected if not (OUT/'generations'/(j['key']+'.json')).exists() or json.loads((OUT/'generations'/(j['key']+'.json')).read_text())['status'] not in ['complete','blocked']]
            for f in as_completed([pool.submit(generate,j) for j in todo]):
                x=f.result();print(json.dumps(dict(key=x['key'],status=x['status'],calls=len(x['calls']))),flush=True);summary(jobs)
        print(json.dumps(summary(jobs)),flush=True);return
    attempted=set();active={}
    with ProcessPoolExecutor(max_workers=a.workers,mp_context=multiprocessing.get_context('spawn')) as pool:
        while True:
            for f in list(active):
                if f.done():
                    active.pop(f);x=f.result();print(json.dumps(dict(key=x['key'],status=x['status'],verified=x.get('verified'))),flush=True)
            terminal=0
            for j in selected:
                g=OUT/'generations'/(j['key']+'.json');v=OUT/'verification'/(j['key']+'.json')
                if not g.exists():continue
                state=json.loads(g.read_text())['status']
                if state in ['error','blocked']:terminal+=1;continue
                if state!='complete':continue
                terminal+=1
                if j['key'] in attempted:continue
                if v.exists() and json.loads(v.read_text())['status']=='complete':attempted.add(j['key']);continue
                attempted.add(j['key']);active[pool.submit(verify,j)]=j['key']
            status=summary(jobs)
            if not active and (not a.watch or terminal==len(selected)):break
            time.sleep(10)
    changed=[q for q,h in manifest['original_inputs'].items() if digest(q)!=h]
    atomic(EXPORT/'input_integrity.json',dict(checked=len(manifest['original_inputs']),changed=changed,utc=now()))
    print(json.dumps(summary(jobs)),flush=True)


if __name__=='__main__':main()
