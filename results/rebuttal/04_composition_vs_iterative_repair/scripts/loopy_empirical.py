"""Isolated, resumable Loopy empirical study; original experiments are read-only.

Two target visibility settings x two strategies, checkpoints at 1/4/8 calls.
Refinement sees raw WP feedback, NEVER checkpoint/Houdini results.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from contextlib import contextmanager
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
import re
import resource
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
REPO = Path('/home/yangfp/CRAFT')
RUN = Path('/home/yangfp/craft_empirical_runs/loopy_compose_refine_20261107')
EXPORT = Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20/outputs/loopy_compose_refine_20261107')
sys.path.insert(0, str(REPO))
from experiments.gpt5nano_full832.common import discover_tasks, ensure_frama_c_available, assert_target_hidden
from rl_pipeline.common.program import parse_program
from rl_pipeline.common.state import extract_invariants, dedup_normalized
from rl_pipeline.reward import annotate
from rl_pipeline.reward.filters import auto_filter
from src.output_verify import OutputVerifier

NAIVE = '''Generate inductive ACSL loop invariants for the following C program.
Any assertions or postconditions present in the program are verification goals.

Output the complete invariant set, using at most 20 lines.
Each line must have the following format:
loop invariant <expression>;
Output only invariant lines.

Program:
{program}'''

REFINE = '''Revise the previous ACSL loop invariants for the C program below
using the verifier feedback.

Fix the reported problems and strengthen the invariants where needed.
Preserve useful valid invariants.
Any assertions or postconditions present in the program are verification goals.

Output the complete revised invariant set, not a patch.
Use at most 20 lines, each in the following format:
loop invariant <expression>;
Output only invariant lines.

Program:
{program}

Previous invariants:
{previous_invariants}

Verifier feedback:
{verifier_feedback}'''

STOP = threading.Event()
API_SEMAPHORE = threading.BoundedSemaphore(32)


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(x):
    return hashlib.sha256(x if isinstance(x, bytes) else x.encode()).hexdigest()


def objsha(x):
    return sha(json.dumps(x, sort_keys=True, ensure_ascii=False))


def read(p):
    return json.loads(Path(p).read_text()) if Path(p).exists() else None


def atomic(p, value):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    q = p.with_name(p.name + f'.{os.getpid()}.{threading.get_ident()}.tmp')
    q.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    q.replace(p)


def put(p, value):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    q = p.with_name(p.name + f'.{os.getpid()}.{threading.get_ident()}.tmp')
    q.write_text(value); q.replace(p)


def configure_proof():
    os.environ['CRAFT_WP_TIMEOUT'] = '5'
    os.environ['CRAFT_WP_PAR'] = '2'
    os.environ['CRAFT_WP_PROVERS'] = 'alt-ergo,z3'
    logging.disable(logging.WARNING)


def prepare():
    configure_proof(); ensure_frama_c_available()
    for folder in ['sources', 'calls', 'feedback', 'scores', 'logs', 'scratch', 'jobs', 'invocations']:
        (RUN / folder).mkdir(parents=True, exist_ok=True)
    EXPORT.mkdir(parents=True, exist_ok=True)
    originals = {}; jobs = []
    for task in discover_tasks():
        if task.suite != 'Loopy':
            continue
        original = task.source_path.read_text(errors='ignore')
        originals[str(task.source_path)] = sha(task.source_path.read_bytes())
        assert_target_hidden(original, task.hidden_source)
        for suffix, text in [('original', original), ('hidden', task.hidden_source)]:
            path = RUN / 'sources' / f'{task.case_id}.{suffix}.c'
            if path.exists() and path.read_text() != text:
                raise ValueError('Input snapshot drift')
            if not path.exists():
                path.write_text(text)
        for visibility in ['hidden', 'visible']:
            jobs.append(dict(case=task.case_id, visibility=visibility,
                             program=original if visibility == 'visible' else task.hidden_source,
                             original=original, hidden=task.hidden_source))
    if len(jobs) != 932:
        raise ValueError('Expected 466 programs x 2 visibility settings')
    settings = dict(schema=1, population='Loopy 466 only', expected_programs=466,
                    visibility=['hidden', 'visible'], strategies=['compose', 'refine'], checkpoints=[1,4,8],
                    model='gpt-5-nano', reasoning_effort='minimal', endpoint_host='api.openlux.ai',
                    n_per_request=1, max_completion_tokens=8192, max_invariants_per_response=20,
                    naive_prompt=NAIVE, refine_prompt=REFINE, system_prompt=None,
                    shared_initial=True, fixed_calls=8, early_stop_on_target=False,
                    refine_history='original program + immediately previous parsed complete candidate + latest raw WP feedback',
                    feedback='Full syntax/WP stdout and stderr; no Houdini; no goal-based branch or selection',
                    houdini='Only checkpoint evaluation; union of independent prefixes versus current refinement answer; always target-free filtering',
                    evaluation='Original targets restored after Houdini; only designated checkpoint output is scored',
                    empty_candidate='Omit empty ACSL block; still judge original targets',
                    wp=dict(timeout_per_goal=5, parallel=2, provers='alt-ergo,z3', process_timeout_seconds=1200),
                    api_retries=dict(max_transient_attempts=5, max_unchanged_invalid_prompt_attempts=2,
                                     change_rejected_request=False, persistent_errors='pending; never proof failure'),
                    unsupported_parameters_omitted=['temperature','top_p'],
                    original_inputs=originals)
    files = [p for folder in ['rl_pipeline', 'src'] for p in (REPO / folder).rglob('*.py')]
    files += [REPO / 'experiments/gpt5nano_full832/common.py', REPO / 'experiments/gpt5nano_full832/protocol.json']
    sources = {str(p):sha(p.read_bytes()) for p in sorted(set(files))}
    fingerprint = objsha(dict(settings=settings, verifier_sources=sources))
    manifest = dict(fingerprint=fingerprint, settings=settings, verifier_sources=sources,
                    driver_sha256=sha(Path(__file__).read_bytes()), run=str(RUN), created_utc=None)
    previous = read(RUN / 'manifest.json')
    if previous:
        manifest['created_utc'] = previous['created_utc']
        if manifest != previous:
            raise ValueError('Protocol or driver drift: keep the old run and use a new directory')
    else:
        manifest['created_utc'] = utc(); atomic(RUN / 'manifest.json', manifest)
        shutil.copy2(__file__, RUN / 'driver.py')
    atomic(EXPORT / 'protocol.json', manifest)
    put(EXPORT / 'naive_prompt.txt', NAIVE + '\n')
    put(EXPORT / 'refine_prompt.txt', REFINE + '\n')
    prices = dict(checked_utc='2026-11-07', currency='USD', unit='per 1 million tokens',
                  openai_reference=dict(input=0.05,cached_input=0.005,output=0.40,
                    source='https://developers.openai.com/api/docs/models/gpt-5-nano'),
                  openlux_public_lowest_group_scenario=dict(input=0.0022,output=0.0176,
                    cached_input='not specified; use ordinary input rate',source='https://www.openlux.ai/pricing.html'),
                  actual_account_charge='Unknown unless the provider supplies per-request charge metadata; price scenarios are not billing receipts')
    atomic(EXPORT / 'pricing_reference.json', prices)
    for job in jobs:
        job['fingerprint'] = fingerprint
    return jobs, manifest


def credentials():
    c = {}
    for line in Path('/home/yangfp/.config/craft/openlux.env').read_text().splitlines():
        line = line.strip().removeprefix('export ')
        if '=' not in line or line.startswith('#'):
            continue
        k, v = line.split('=',1)
        parts = shlex.split(v, comments=True)
        c[k.strip()] = parts[0] if parts else ''
    base = c.get('OPENAI_BASE_URL','').rstrip('/'); u = urlsplit(base)
    if u.scheme != 'https' or u.hostname != 'api.openlux.ai' or u.username or u.query or not c.get('OPENAI_API_KEY'):
        raise ValueError('Unexpected API configuration')
    return base,c['OPENAI_API_KEY']


def call_path(job, role, step):
    return RUN / 'calls' / job['visibility'] / job['case'] / f'{role}_{step}.json'


def get_calls(job, strategy, budget, cache=None):
    paths = [call_path(job,'initial',1)] + [call_path(job,strategy,k) for k in range(2,budget+1)]
    cs = [read(p) if cache is None else cache.get(str(p)) for p in paths]
    if any(not c or c.get('status') != 'complete' for c in cs):
        return None
    if any(c['fingerprint'] != job['fingerprint'] for c in cs):
        raise ValueError('Mixed request protocol')
    return cs


class RequestPending(Exception):
    pass


def request(job, role, step, prompt, client, secret):
    path=call_path(job,role,step); messages=[dict(role='user',content=prompt)]
    request_sha=objsha(dict(model='gpt-5-nano',messages=messages,n=1,reasoning_effort='minimal',max_completion_tokens=8192))
    state=read(path)
    if state:
        if state['request_sha256'] != request_sha or state['fingerprint'] != job['fingerprint']:
            raise ValueError('Request drift')
        if state['status']=='complete':
            return state
        # Persistent explicit errors are not automatically replayed on resume.
        if state['status']=='pending':
            raise RequestPending(state.get('error','request pending'))
    else:
        state=dict(case=job['case'],visibility=job['visibility'],role=role,step=step,
                   fingerprint=job['fingerprint'],request_sha256=request_sha,messages=messages,
                   status='running',attempts=[],created_utc=utc())
    if state.get('received_response'):
        # Recover a persisted paid response after an interrupted local write.
        body=state['received_response'];choices=body.get('choices',[])
        if len(choices)!=1 or choices[0].get('index')!=0 or not body.get('model','').startswith('gpt-5-nano'):
            STOP.set();raise RequestPending('Persisted response shape or model mismatch')
        text=choices[0].get('message',{}).get('content') or ''
        state.update(status='complete',text=text,invariants=extract_invariants(text,max_invariants=20),
                     usage=body.get('usage'),returned_model=body['model'],response_id=body.get('id'),
                     finish_reason=choices[0].get('finish_reason'),refusal=choices[0].get('message',{}).get('refusal'),
                     api_wall_seconds=state.get('api_wall_seconds',sum(a.get('seconds',0) for a in state['attempts'])),
                     recovered_persisted_response=True,updated_utc=utc())
        atomic(path,state);return state
    def safe(x):
        return re.sub(r'sk-[A-Za-z0-9_-]{6,}','[REDACTED]',str(x).replace(secret,'[REDACTED]'))[:1600]
    start=time.monotonic(); invalid=0; retry_sleep=0.0
    for attempt in range(5):
        if STOP.is_set():
            raise RequestPending('Global API circuit breaker is open')
        tick=time.monotonic()
        try:
            with API_SEMAPHORE:
                response=client.chat.completions.create(model='gpt-5-nano',messages=messages,n=1,
                    reasoning_effort='minimal',max_completion_tokens=8192)
            duration=time.monotonic()-tick
            # Persist the received body before interpreting it, to avoid repeat billing.
            state['received_response']=response.model_dump()
            state['attempts'].append(dict(status='received',seconds=duration,utc=utc()))
            atomic(path,state)
            choices=list(response.choices)
            if len(choices)!=1 or choices[0].index!=0 or not response.model.startswith('gpt-5-nano'):
                STOP.set(); raise RuntimeError('Unexpected response shape or model')
            text=choices[0].message.content or ''
            state.update(status='complete',text=text,invariants=extract_invariants(text,max_invariants=20),
                         usage=response.usage.model_dump() if response.usage else None,
                         returned_model=response.model,response_id=response.id,
                         finish_reason=choices[0].finish_reason,refusal=getattr(choices[0].message,'refusal',None),
                         api_wall_seconds=time.monotonic()-start,response_seconds=duration,
                         retry_sleep_seconds=retry_sleep,updated_utc=utc())
            atomic(path,state); return state
        except Exception as exc:
            if state.get('received_response'):
                state.update(status='pending',error=safe(exc),updated_utc=utc());atomic(path,state)
                raise RequestPending(state['error'])
            code=getattr(exc,'status_code',None)
            state['attempts'].append(dict(status='error',http_status=code,error=safe(exc),
                                          seconds=time.monotonic()-tick,utc=utc()))
            atomic(path,state)
            is_invalid=code==400 and 'invalid_prompt' in str(exc)
            if is_invalid:
                invalid+=1; retry=invalid<2
            else:
                retry=code in [408,409,429,500,502,503,504] or type(exc).__name__ in ['APIConnectionError','APITimeoutError']
            if code in [401,402,403,404] or (code==400 and not is_invalid):
                STOP.set()
            if not retry or attempt==4:
                state.update(status='pending',error=safe(exc),updated_utc=utc(),api_wall_seconds=time.monotonic()-start)
                atomic(path,state);raise RequestPending(state['error'])
            delay=min(2**(attempt+1),20);time.sleep(delay);retry_sleep+=delay
    raise AssertionError('Unreachable request loop')


def annotated(source, invariants):
    return annotate.build_annotated(parse_program(source),invariants,0) if invariants else source


@contextmanager
def capture(logpath):
    old=subprocess.run; events=[]; fatal=[]
    def observed(*args,**kwargs):
        argv=args[0] if args else kwargs.get('args',[])
        before=resource.getrusage(resource.RUSAGE_CHILDREN);tick=time.monotonic()
        event=dict(args=argv,started_utc=utc())
        if isinstance(argv,list) and argv and str(argv[-1]).endswith('.c'):
            source=Path(argv[-1])
            if source.exists():event['input_source']=source.read_text()
        if 'timeout' not in kwargs:kwargs['timeout']=1200
        try:
            result=old(*args,**kwargs)
            event.update(returncode=result.returncode,stdout=result.stdout,stderr=result.stderr)
            text=str(result.stdout)+'\n'+str(result.stderr)
            if result.returncode<0 or any(t in text.lower() for t in ['out of memory','segmentation fault','fatal error: exception']):fatal.append('subprocess_fatal')
            if isinstance(argv,list) and '-wp' in argv and result.returncode!=0:fatal.append('wp_execution_error')
            return result
        except Exception as exc:
            event.update(error=f'{type(exc).__name__}: {exc}')
            if isinstance(exc,(FileNotFoundError,subprocess.TimeoutExpired)):fatal.append(type(exc).__name__)
            raise
        finally:
            after=resource.getrusage(resource.RUSAGE_CHILDREN)
            event.update(seconds=time.monotonic()-tick,cpu_seconds=(after.ru_utime+after.ru_stime)-(before.ru_utime+before.ru_stime))
            events.append(event)
            with gzip.open(logpath,'at') as f:f.write(json.dumps(event,default=str)+'\n')
    subprocess.run=observed
    try:yield events,fatal
    finally:subprocess.run=old


def proof_work(payload):
    configure_proof();ensure_frama_c_available()
    job=payload['job'];kind=payload['kind'];label=payload['label']
    result_path=Path(payload['result_path']); prior=read(result_path)
    input_sha=objsha({k:v for k,v in payload.items() if k!='queued_utc'})
    if prior and prior.get('status')=='complete':
        if prior['input_sha256']!=input_sha:raise ValueError('Proof input drift')
        return prior
    scratch=RUN/'scratch'/job['visibility']/job['case']/label;scratch.mkdir(parents=True,exist_ok=True)
    os.chdir(scratch);tempfile.tempdir=str(scratch);os.environ['TMPDIR']=str(scratch)
    logpath=RUN/'logs'/job['visibility']/job['case']/(label+'.jsonl.gz');logpath.parent.mkdir(parents=True,exist_ok=True)
    if logpath.exists():logpath.rename(logpath.with_name(logpath.name+'.previous.'+str(time.time_ns())))
    start=time.monotonic();r0=resource.getrusage(resource.RUSAGE_CHILDREN);s0=resource.getrusage(resource.RUSAGE_SELF)
    result=dict(status='running',kind=kind,label=label,case=job['case'],visibility=job['visibility'],
                fingerprint=job['fingerprint'],input_sha256=input_sha,started_utc=utc(),queued_utc=payload['queued_utc'])
    atomic(result_path,result)
    events=[]
    try:
        with capture(logpath) as (events,fatal):
            invs=dedup_normalized(payload['invariants'])
            if kind=='feedback':
                code=annotated(job['program'],invs);cpath=scratch/'candidate.c';cpath.write_text(code)
                verifier=OutputVerifier();verifier.run(str(cpath))
                wp=[e for e in events if isinstance(e.get('args'),list) and '-wp' in e['args']]
                if job['visibility']=='hidden':
                    for e in wp:
                        if OutputVerifier.filter_goal_assertion((e.get('stdout') or '').split('------------------------------------------------------------')):
                            raise ValueError('Target obligation appeared in hidden feedback')
                chunks=['Verifier results for the previous invariant candidate.\nUnproved or timed-out obligations are not counterexamples.']
                for e in events:
                    if e.get('stdout'):chunks.append(str(e['stdout']))
                    if e.get('stderr'):chunks.append(str(e['stderr']))
                    if e.get('error'):chunks.append(str(e['error']))
                if not invs:chunks.insert(1,'No loop invariant lines were extracted from the response.')
                feedback='\n\n'.join(chunks).replace(str(cpath),'candidate.c')
                result.update(feedback=feedback,feedback_sha256=sha(feedback),feedback_characters=len(feedback),
                              invariants=invs,syntax_correct=verifier.syntax_correct,syntax_error=verifier.syntax_error,
                              invariant_valid=list(verifier.validate_result),target_valid=list(verifier.verify_result))
            else:
                filt=auto_filter()
                if filt.name!='cascade(pre-frama->houdini)':raise RuntimeError('Non-authoritative filter')
                survivors=filt.filter(parse_program(job['hidden']),0,invs,positives=None)
                cpath=scratch/'final.c';cpath.write_text(annotated(job['original'],survivors))
                verifier=OutputVerifier();verifier.run(str(cpath))
                has_target=job['original']!=job['hidden']
                syntax=verifier.syntax_correct and verifier.syntax_error in ['', 'syntax Correct']
                invariant_ok=len(verifier.validate_result)==len(survivors) and all(verifier.validate_result)
                target_ok=bool(verifier.verify_result) and all(verifier.verify_result) if has_target else True
                result.update(input_invariants=invs,survivors=survivors,verified=bool(syntax and invariant_ok and target_ok),
                              syntax_correct=syntax,invariant_valid=list(verifier.validate_result),
                              target_valid=list(verifier.verify_result),final_source_sha256=sha(cpath.read_bytes()))
            if fatal:raise RuntimeError('Infrastructure failure: '+','.join(fatal))
        result['status']='complete'
    except Exception as exc:
        result.update(status='error',error=f'{type(exc).__name__}: {exc}')
    r1=resource.getrusage(resource.RUSAGE_CHILDREN);s1=resource.getrusage(resource.RUSAGE_SELF)
    result.update(seconds=time.monotonic()-start,
                  cpu_seconds=(r1.ru_utime+r1.ru_stime+s1.ru_utime+s1.ru_stime)-(r0.ru_utime+r0.ru_stime+s0.ru_utime+s0.ru_stime),
                  wp_calls=sum(isinstance(e.get('args'),list) and '-wp' in e['args'] for e in events),
                  subprocess_calls=len(events),updated_utc=utc(),log=str(logpath))
    atomic(result_path,result);return result


def feedback_path(job, step):
    return RUN/'feedback'/job['visibility']/job['case']/f'{step}.json'


def score_path(job, strategy, budget):
    name='shared_1' if budget==1 else f'{strategy}_{budget}'
    return RUN/'scores'/job['visibility']/job['case']/(name+'.json')


def payload(job, kind, label, path, invariants):
    return dict(job=job,kind=kind,label=label,result_path=str(path),invariants=invariants,queued_utc=utc())


def generate_trajectory(job, pool):
    import openai
    base,secret=credentials()
    client=openai.OpenAI(api_key=secret,base_url=base,max_retries=0,timeout=180)
    status=dict(case=job['case'],visibility=job['visibility'],fingerprint=job['fingerprint'],started_utc=utc())
    try:
        initial=request(job,'initial',1,NAIVE.format(program=job['program']),client,secret)
        for strategy in ['compose','refine']:
            try:
                previous=initial
                for step in range(2,9):
                    if strategy=='compose':
                        prompt=NAIVE.format(program=job['program'])
                    else:
                        fp=feedback_path(job,step-1);feedback=read(fp)
                        if not feedback or feedback['status']!='complete':
                            feedback=pool.submit(proof_work,payload(job,'feedback',f'feedback_{step-1}',fp,previous['invariants'])).result()
                        if feedback['status']!='complete':raise RequestPending('feedback verification execution pending')
                        prompt=REFINE.format(program=job['program'],
                            previous_invariants='\n'.join('loop invariant '+i+';' for i in previous['invariants']) or '(empty invariant set)',
                            verifier_feedback=feedback['feedback'])
                    previous=request(job,strategy,step,prompt,client,secret)
                status[strategy]='complete'
            except RequestPending as exc:
                status[strategy]='pending';status[strategy+'_reason']=str(exc)[:1600]
    except RequestPending as exc:
        status.update(compose='pending',refine='pending',initial_reason=str(exc)[:1600])
    except Exception as exc:
        status.update(compose='error',refine='error',error=f'{type(exc).__name__}: {exc}')
    finally:
        client.close()
    status['updated_utc']=utc();atomic(RUN/'jobs'/job['visibility']/(job['case']+'.json'),status)
    return status


def score_jobs(jobs,pool):
    active={}
    for job in jobs:
        for strategy,budget in [('compose',1),('compose',4),('compose',8),('refine',4),('refine',8)]:
            calls=get_calls(job,strategy,budget)
            if not calls:continue
            dest=score_path(job,strategy,budget);previous=read(dest)
            if previous and previous['status']=='complete':continue
            invs=[i for c in calls for i in c['invariants']] if strategy=='compose' else calls[-1]['invariants']
            label='shared_1' if budget==1 else f'{strategy}_{budget}'
            future=pool.submit(proof_work,payload(job,'score',label,dest,invs))
            active[future]=(job['case'],job['visibility'],strategy,budget)
    for future in as_completed(active):
        result=future.result()
        print(json.dumps(dict(stage='score',task=active[future],status=result['status'],verified=result.get('verified'))),flush=True)


def token_metrics(calls):
    us=[c.get('usage') or {} for c in calls]
    p=sum(u.get('prompt_tokens',0) for u in us);o=sum(u.get('completion_tokens',0) for u in us)
    cached=sum((u.get('prompt_tokens_details') or {}).get('cached_tokens',0) or 0 for u in us)
    reason=sum((u.get('completion_tokens_details') or {}).get('reasoning_tokens',0) or 0 for u in us)
    return dict(input_tokens=p,output_tokens=o,total_tokens=p+o,cached_input_tokens=cached,reasoning_tokens=reason,
                usage_missing=sum(not c.get('usage') for c in calls),
                api_wall_seconds=sum(c.get('api_wall_seconds',0) for c in calls),
                api_attempts=sum(len(c.get('attempts',[])) for c in calls),
                openai_reference_usd=((p-cached)*0.05+cached*0.005+o*0.40)/1e6,
                openlux_lowest_group_scenario_usd=(p*0.0022+o*0.0176)/1e6)


def export_results(jobs,phase,final=False):
    rows=[]; physical=[]; call_cache={}; state_counts={}; feedback_stats=[]; score_stats=[]
    for f in (RUN/'calls').glob('*/*/*.json'):
        c=read(f);state_counts[c['status']]=state_counts.get(c['status'],0)+1
        c={k:v for k,v in c.items() if k in ['status','fingerprint','usage','api_wall_seconds','attempts']}
        call_cache[str(f)]=c
        if c['status']=='complete':physical.append(c)
    for job in jobs:
        for strategy in ['compose','refine']:
            for budget in [1,4,8]:
                cs=get_calls(job,strategy,budget,cache=call_cache);score=read(score_path(job,strategy,budget))
                status='awaiting_generation' if not cs else ('awaiting_scoring' if not score else score['status'])
                ms=token_metrics(cs or [])
                feedback=[read(feedback_path(job,k)) for k in range(1,budget)] if strategy=='refine' else []
                fwall=sum(f.get('seconds',0) for f in feedback if f and f['status']=='complete')
                fcpu=sum(f.get('cpu_seconds',0) for f in feedback if f and f['status']=='complete')
                fwp=sum(f.get('wp_calls',0) for f in feedback if f and f['status']=='complete')
                swall=score.get('seconds',0) if score else 0;scpu=score.get('cpu_seconds',0) if score else 0
                rows.append(dict(case_id=job['case'],visibility=job['visibility'],strategy=strategy,budget=budget,
                    status=status,success=score.get('verified','') if score and status=='complete' else '',
                    nominal_model_calls=budget if cs else '',**ms,
                    feedback_wall_seconds=fwall,feedback_cpu_seconds=fcpu,feedback_wp_calls=fwp,
                    checkpoint_wall_seconds=swall,checkpoint_cpu_seconds=scpu,checkpoint_wp_calls=score.get('wp_calls',0) if score else 0,
                    serial_service_seconds=ms['api_wall_seconds']+fwall+swall,
                    error=score.get('error','') if score else ''))
    for f in (RUN/'feedback').glob('*/*/*.json'):
        r=read(f)
        if r['status']=='complete':feedback_stats.append(r)
    for f in (RUN/'scores').glob('*/*/*.json'):
        r=read(f)
        if r['status']=='complete':score_stats.append(r)
    groups=[]
    for visibility in ['hidden','visible']:
        for strategy in ['compose','refine']:
            for budget in [1,4,8]:
                rs=[r for r in rows if (r['visibility'],r['strategy'],r['budget'])==(visibility,strategy,budget)]
                complete=[r for r in rs if r['status']=='complete'];generated=[r for r in rs if r['nominal_model_calls']!='']
                success=sum(r['success'] is True for r in complete);unresolved=466-len(complete)
                means=['input_tokens','output_tokens','total_tokens','cached_input_tokens','reasoning_tokens','api_wall_seconds',
                       'feedback_wall_seconds','feedback_cpu_seconds','feedback_wp_calls','checkpoint_wall_seconds','checkpoint_cpu_seconds',
                       'checkpoint_wp_calls','serial_service_seconds','openai_reference_usd','openlux_lowest_group_scenario_usd']
                # Timing means require completed scoring; token means require complete trajectories.
                group=dict(visibility=visibility,strategy=strategy,budget=budget,expected=466,
                    generated=len(generated),scored=len(complete),success=success,unresolved=unresolved,
                    conditional_success_rate=success/len(complete) if complete else None,
                    full_population_lower_bound=success/466,full_population_upper_bound=(success+unresolved)/466,
                    errors=sum(r['status']=='error' for r in rs))
                if not unresolved:group['success_rate']=success/466
                for field in means:
                    subset=generated if field in ['input_tokens','output_tokens','total_tokens','cached_input_tokens','reasoning_tokens','openai_reference_usd','openlux_lowest_group_scenario_usd','api_wall_seconds'] else complete
                    group['mean_'+field]=sum(r[field] for r in subset)/len(subset) if subset else None
                groups.append(group)
    pairs=[]
    for visibility in ['hidden','visible']:
        for budget in [1,4,8]:
            a={r['case_id']:r for r in rows if (r['visibility'],r['strategy'],r['budget'],r['status'])==(visibility,'compose',budget,'complete')}
            b={r['case_id']:r for r in rows if (r['visibility'],r['strategy'],r['budget'],r['status'])==(visibility,'refine',budget,'complete')}
            both=sorted(a.keys()&b.keys())
            win=[k for k in both if a[k]['success'] and not b[k]['success']]
            lose=[k for k in both if b[k]['success'] and not a[k]['success']]
            pairs.append(dict(visibility=visibility,budget=budget,paired=len(both),compose_only=win,refine_only=lose,
                              identical_at_one=not(win or lose) if budget==1 else None))
    totals=token_metrics(physical)
    lifecycle=read(RUN/'lifecycle.json') or {}
    output=dict(state='complete' if final and all(g['unresolved']==0 for g in groups) else ('incomplete_pending' if final else 'running'),
                phase=phase,updated_utc=utc(),groups=groups,paired_comparisons=pairs,
                physical_unique_successful_requests=len(physical),physical_request_states=state_counts,
                maximum_successful_requests=13980,physical_token_and_api_totals=totals,
                physical_feedback_jobs=len(feedback_stats),physical_checkpoint_jobs=len(score_stats),
                physical_proof_cpu_seconds=sum(r['cpu_seconds'] for r in feedback_stats+score_stats),
                physical_proof_service_seconds=sum(r['seconds'] for r in feedback_stats+score_stats),
                lifecycle=lifecycle,
                pricing='USD fields are public price scenarios, not observed account debits; provider failed-request billing may be unavailable',
                cost_scope='Per-method checkpoint costs include its first n model calls, n-1 raw feedback verifications for refine, and only its own final Houdini/judge; exclude other offline checkpoint evaluations. Physical totals deduplicate the shared initial response and n=1 score.',
                missing_data='Incomplete API/proof tasks are unresolved, never automatically counted as verification failures.')
    if lifecycle.get('started_utc'):
        end=datetime.fromisoformat(lifecycle.get('finished_utc') or utc());start=datetime.fromisoformat(lifecycle['started_utc'])
        output['experiment_elapsed_wall_seconds']=(end-start).total_seconds()
    atomic(EXPORT/'result.json',output)
    for name,data in [('per_program.csv',rows),('summary.csv',groups)]:
        stream=io.StringIO();fields=list(dict.fromkeys(k for row in data for k in row))
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(data);put(EXPORT/name,stream.getvalue())
    lines=['Loopy empirical study: independent composition vs verifier-guided refinement',
           f"State: {output['state']}; phase: {phase}; updated UTC: {output['updated_utc']}",
           '466 programs; hidden/visible targets; n=1,4,8; one answer/request; common first answer.',
           'Refinement uses raw syntax/WP feedback only; checkpoint Houdini results never enter its trajectory.',
           'Incomplete items are unresolved, not proof failures. Partial rates are conditional.',
           f"Unique successful requests: {len(physical)}/13980; tokens: {totals['total_tokens']}",
           'visibility | strategy | n | scored | success | unresolved | conditional rate']
    for g in groups:
        rate=f"{g['conditional_success_rate']:.2%}" if g['scored'] else '--'
        lines.append(f"{g['visibility']} | {g['strategy']} | {g['budget']} | {g['scored']} | {g['success']} | {g['unresolved']} | {rate}")
    lines+=['','Cost definitions: '+output['cost_scope'],output['pricing'],f'Run directory: {RUN}','']
    put(EXPORT/'report.txt','\n'.join(lines))
    return output


def audit_integrity(manifest):
    changed_inputs=[p for p,h in manifest['settings']['original_inputs'].items() if sha(Path(p).read_bytes())!=h]
    changed_sources=[p for p,h in manifest['verifier_sources'].items() if sha(Path(p).read_bytes())!=h]
    result=dict(checked_inputs=466,changed_inputs=changed_inputs,changed_verifier_sources=changed_sources,utc=utc())
    atomic(EXPORT/'input_integrity.json',result)
    if changed_inputs or changed_sources:raise ValueError('Original inputs or verification implementation changed')


def archive():
    target=EXPORT/'evidence.tar.gz';temporary=target.with_suffix('.gz.tmp')
    with tarfile.open(temporary,'w:gz',compresslevel=3) as tar:
        for name in ['manifest.json','driver.py','lifecycle.json','sources','calls','feedback','scores','logs','jobs','invocations']:
            path=RUN/name
            if path.exists():tar.add(path,arcname=name)
    temporary.replace(target)
    atomic(EXPORT/'evidence.sha256.json',dict(file=str(target),sha256=sha(target.read_bytes()),bytes=target.stat().st_size))


def selfcheck():
    # Real verifier checks, with no API or credential access.
    source='void f(void) { int x=0; while(x<10) { x++; } /*@ assert x == 10; */ }'
    hidden='void f(void) { int x=0; while(x<10) { x++; } }'
    job=dict(case='selfcheck',visibility='hidden',program=hidden,original=source,hidden=hidden,fingerprint='selfcheck')
    checks=[]
    for tag,invs,expected in [('valid',['x >= 0','x <= 10'],True),('empty',[],False)]:
        p=RUN/'selfcheck'/(tag+'.json')
        r=proof_work(payload(job,'score','selfcheck_'+tag,p,invs))
        checks.append(dict(tag=tag,status=r['status'],verified=r.get('verified'),expected=expected))
        if r['status']!='complete' or r['verified'] is not expected:raise AssertionError(checks)
    for visibility in ['hidden','visible']:
        j={**job,'visibility':visibility,'program':hidden if visibility=='hidden' else source}
        r=proof_work(payload(j,'feedback','selfcheck_feedback',RUN/'selfcheck'/('feedback_'+visibility+'.json'),['x <= 10']))
        if r['status']!='complete':raise AssertionError(r)
        if visibility=='hidden' and r['target_valid']:raise AssertionError('Hidden feedback leak')
        checks.append(dict(tag='feedback_'+visibility,status=r['status'],target_goals=len(r['target_valid'])))
    atomic(EXPORT/'selfcheck.json',checks);print(json.dumps(checks),flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['audit','selfcheck','pilot','run','score','report'])
    p.add_argument('--workers',type=int,default=12);p.add_argument('--trajectory-workers',type=int,default=32)
    args=p.parse_args();configure_proof()
    if args.mode=='selfcheck':
        RUN.mkdir(parents=True,exist_ok=True);EXPORT.mkdir(parents=True,exist_ok=True);selfcheck();return
    jobs,manifest=prepare()
    atomic(RUN/'invocations'/(str(time.time_ns())+'.json'),dict(mode=args.mode,workers=args.workers,trajectory_workers=args.trajectory_workers,utc=utc()))
    if args.mode=='audit':
        audit_integrity(manifest);print(json.dumps(dict(jobs=len(jobs),fingerprint=manifest['fingerprint'],run=str(RUN))),flush=True);return
    if args.mode=='report':
        print(json.dumps(export_results(jobs,'report')),flush=True);return
    if args.mode=='pilot':
        cases={jobs[0]['case'],jobs[len(jobs)//2]['case']}
        selected=[j for j in jobs if j['case'] in cases]
    else:selected=jobs
    lifecycle=read(RUN/'lifecycle.json') or dict(started_utc=utc(),stages=[],workers=args.workers,trajectory_workers=args.trajectory_workers)
    lifecycle.pop('finished_utc',None);atomic(RUN/'lifecycle.json',lifecycle)
    phase={'name':'generation_and_raw_feedback'};monitor_stop=threading.Event()
    def monitor():
        while not monitor_stop.is_set():
            try:export_results(jobs,phase['name'])
            except Exception as exc:print(json.dumps(dict(monitor_error=f'{type(exc).__name__}: {exc}')),flush=True)
            monitor_stop.wait(45)
    monitor_thread=threading.Thread(target=monitor,daemon=True);monitor_thread.start()
    try:
        with ProcessPoolExecutor(max_workers=args.workers,mp_context=multiprocessing.get_context('spawn')) as pool:
            if args.mode!='score':
                lifecycle['stages'].append(dict(name='generation_and_raw_feedback',started_utc=utc()));atomic(RUN/'lifecycle.json',lifecycle)
                with ThreadPoolExecutor(max_workers=args.trajectory_workers) as trajectories:
                    futures=[trajectories.submit(generate_trajectory,j,pool) for j in selected]
                    for future in as_completed(futures):
                        r=future.result();print(json.dumps(dict(stage='trajectory',case=r['case'],visibility=r['visibility'],compose=r.get('compose'),refine=r.get('refine'),error=r.get('error'))),flush=True)
                lifecycle['stages'][-1]['finished_utc']=utc();atomic(RUN/'lifecycle.json',lifecycle)
            phase['name']='checkpoint_houdini_and_target_scoring'
            lifecycle['stages'].append(dict(name=phase['name'],started_utc=utc()));atomic(RUN/'lifecycle.json',lifecycle)
            score_jobs(selected,pool)
            lifecycle['stages'][-1]['finished_utc']=utc()
        if args.mode!='pilot':lifecycle['finished_utc']=utc()
        atomic(RUN/'lifecycle.json',lifecycle);audit_integrity(manifest)
    finally:
        monitor_stop.set();monitor_thread.join()
    result=export_results(jobs,'pilot_finished' if args.mode=='pilot' else 'finished',final=args.mode!='pilot')
    if args.mode!='pilot':archive()
    print(json.dumps(dict(state=result['state'],phase=result['phase'],physical_requests=result['physical_unique_successful_requests'],output=str(EXPORT))),flush=True)


if __name__=='__main__':main()
