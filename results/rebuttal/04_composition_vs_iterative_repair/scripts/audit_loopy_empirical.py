"""Read-only protocol audit and human-readable cost/time reconciliation.

No model client, credentials, or verifier execution. Writes only new reports.
"""
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
import csv
import gzip
import hashlib
import io
import json

RUN=Path('/home/yangfp/craft_empirical_runs/loopy_compose_refine_20261107')
OUT=Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20/outputs/loopy_compose_refine_20261107')


def read(p):
    return json.loads(p.read_text()) if p.exists() else None


def write(p,x):
    q=p.with_suffix(p.suffix+'.tmp');q.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n');q.replace(p)


def sha(x):
    return hashlib.sha256(x if isinstance(x,bytes) else x.encode()).hexdigest()


def main():
    manifest=read(RUN/'manifest.json');result=read(OUT/'result.json');settings=manifest['settings']
    violations=[];requests=[];lookup={};attempts=Counter()
    for f in sorted((RUN/'calls').glob('*/*/*.json')):
        c=read(f);key=(c['visibility'],c['case'],c['role'],c['step']);lookup[key]=c
        usage=c.get('usage') or (c.get('received_response') or {}).get('usage') or {}
        p=usage.get('prompt_tokens',0);o=usage.get('completion_tokens',0)
        cache=(usage.get('prompt_tokens_details') or {}).get('cached_tokens',0) or 0
        for a in c.get('attempts',[]):attempts[str(a.get('http_status',a.get('status')))]+=1
        requests.append(dict(visibility=c['visibility'],case_id=c['case'],role=c['role'],step=c['step'],status=c['status'],
            input_tokens=p,output_tokens=o,cached_input_tokens=cache,
            reasoning_tokens=(usage.get('completion_tokens_details') or {}).get('reasoning_tokens',0) or 0,
            usage_available=bool(usage),api_attempts=len(c.get('attempts',[])),
            failed_attempts=sum(a.get('status')=='error' for a in c.get('attempts',[])),
            api_wall_seconds=c.get('api_wall_seconds',0),response_seconds=c.get('response_seconds',0),
            openai_reference_usd=((p-cache)*.05+cache*.005+o*.4)/1e6,
            openlux_lowest_group_scenario_usd=(p*.0022+o*.0176)/1e6,
            request_sha256=c['request_sha256'],response_id=c.get('response_id','')))
        if c['fingerprint']!=manifest['fingerprint']:violations.append([str(f),'fingerprint'])
        if c['status']!='complete':continue
        if c['role']=='initial' and c['step']!=1:violations.append([str(f),'initial step'])
        if c['role']!='initial' and not 2<=c['step']<=8:violations.append([str(f),'step range'])
        program=(RUN/'sources'/f"{c['case']}.{'original' if c['visibility']=='visible' else 'hidden'}.c").read_text()
        if c['role'] in ['initial','compose']:
            expected=settings['naive_prompt'].format(program=program)
        else:
            k=c['step']-1;role='initial' if k==1 else 'refine'
            previous=read(RUN/'calls'/c['visibility']/c['case']/f'{role}_{k}.json')
            feedback=read(RUN/'feedback'/c['visibility']/c['case']/f'{k}.json')
            if not previous or not feedback:violations.append([str(f),'missing refinement predecessor']);continue
            expected=settings['refine_prompt'].format(program=program,
                previous_invariants='\n'.join('loop invariant '+i+';' for i in previous['invariants']) or '(empty invariant set)',
                verifier_feedback=feedback['feedback'])
        if c['messages']!=[dict(role='user',content=expected)]:violations.append([str(f),'prompt/predecessor mismatch'])
        expected_hash=sha(json.dumps(dict(model='gpt-5-nano',messages=c['messages'],n=1,reasoning_effort='minimal',max_completion_tokens=8192),sort_keys=True,ensure_ascii=False))
        if c['request_sha256']!=expected_hash:violations.append([str(f),'request hash'])
        response=c.get('received_response') or {}
        if len(response.get('choices',[]))!=1:violations.append([str(f),'response count'])
    proof_status=Counter();proof_cost=[];all_logs=[]
    for folder in ['feedback','scores']:
        for f in (RUN/folder).glob('*/*/*.json'):
            r=read(f);proof_status[folder+'_'+r['status']]+=1
            proof_cost.append(dict(kind=folder,visibility=r['visibility'],case_id=r['case'],label=r['label'],status=r['status'],
                wall_seconds=r.get('seconds',0),cpu_seconds=r.get('cpu_seconds',0),wp_calls=r.get('wp_calls',0),
                subprocess_calls=r.get('subprocess_calls',0),error=r.get('error','')))
            if folder=='feedback' and r['visibility']=='hidden' and r.get('target_valid'):
                violations.append([str(f),'hidden target feedback'])
            if folder=='scores' and r.get('verified'):
                if not r.get('syntax_correct') or not all(r.get('invariant_valid',[])) or not r.get('target_valid') or not all(r['target_valid']):
                    violations.append([str(f),'positive proof evidence'])
    # Include previous/incomplete attempts still retained in compressed raw logs.
    log_wall=0.;log_cpu=0.;log_calls=0;log_errors=0
    for f in (RUN/'logs').glob('*/*/*.gz*'):
        if not f.is_file():continue
        for line in gzip.open(f,'rt'):
            z=json.loads(line);log_calls+=1;log_wall+=z.get('seconds',0);log_cpu+=z.get('cpu_seconds',0);log_errors+=bool(z.get('error'))
    changed=[p for p,h in settings['original_inputs'].items() if sha(Path(p).read_bytes())!=h]
    changed_code=[p for p,h in manifest['verifier_sources'].items() if sha(Path(p).read_bytes())!=h]
    if changed:violations.append(['original_inputs',changed])
    if changed_code:violations.append(['verifier_sources',changed_code])
    for pair in result['paired_comparisons']:
        if pair['budget']==1 and (pair['compose_only'] or pair['refine_only']):violations.append(['n1_shared',pair])
    for name,data in [('requests.csv',requests),('proof_costs.csv',proof_cost)]:
        stream=io.StringIO()
        if data:
            writer=csv.DictWriter(stream,fieldnames=list(data[0]));writer.writeheader();writer.writerows(data)
        (OUT/name).write_text(stream.getvalue())
    audit=dict(utc=datetime.now(timezone.utc).isoformat(),status='pass' if not violations else 'fail',
        violations=violations,request_records=len(requests),successful_requests=sum(r['status']=='complete' for r in requests),
        api_attempt_status_counts=dict(attempts),proof_status_counts=dict(proof_status),
        all_recorded_proof_subprocess_attempts=dict(calls=log_calls,wall_seconds=log_wall,cpu_seconds=log_cpu,exceptions=log_errors),
        recorded_usage_totals={k:sum(r[k] for r in requests) for k in ['input_tokens','output_tokens','cached_input_tokens','reasoning_tokens','api_wall_seconds','openai_reference_usd','openlux_lowest_group_scenario_usd']},
        original_inputs_checked=466,changed_inputs=changed,changed_verifier_sources=changed_code)
    write(OUT/'protocol_audit.json',audit)
    lines=['Loopy独立经验实验：结果与成本',f"状态：{result['state']}；协议审计：{audit['status']}",
           '目标可见性 × 推理策略；每次调用1个回答；检查点为1/4/8次调用。',
           '目标可见性 | 策略 | n | 成功/已评分 | 未决 | 平均输入token | 平均输出token | 平均API秒 | 平均反馈验证秒 | 平均最终验证秒',]
    for g in result['groups']:
        def fmt(k):return f"{g[k]:.2f}" if g.get(k) is not None else '--'
        lines.append(f"{g['visibility']} | {g['strategy']} | {g['budget']} | {g['success']}/{g['scored']} | {g['unresolved']} | {fmt('mean_input_tokens')} | {fmt('mean_output_tokens')} | {fmt('mean_api_wall_seconds')} | {fmt('mean_feedback_wall_seconds')} | {fmt('mean_checkpoint_wall_seconds')}")
    lines+=['','配对比较：']
    for p in result['paired_comparisons']:
        lines.append(f"{p['visibility']} n={p['budget']}: 配对{p['paired']}，仅compose成功{len(p['compose_only'])}，仅refine成功{len(p['refine_only'])}")
    totals=audit['recorded_usage_totals']
    lines+=['',f"实际唯一成功请求：{audit['successful_requests']}；输入token：{totals['input_tokens']}；输出token：{totals['output_tokens']}",
            f"按OpenAI公开单价估值：${totals['openai_reference_usd']:.6f}；按Openlux公开最低组价情景估值：${totals['openlux_lowest_group_scenario_usd']:.6f}",
            '以上金额是单价情景换算，不是账户扣费凭证；失败请求账单可能未知。',
            f"整个实验经过时间：{result.get('experiment_elapsed_wall_seconds',0)/3600:.3f}小时（含阶段间等待）；各程序工作耗时不能直接当作整体并发耗时。",
            f"记录到的证明子进程工作时间总和：{log_wall/3600:.3f}小时；CPU时间总和：{log_cpu/3600:.3f}核小时。",
            '各方法的n成本仅计它的前n次调用、refine前n-1次反馈验证及本检查点最终验证；不计其他离线检查点。',
            '未决项不计为证明失败。四组的首答共享、三个预算点前缀重叠，不能把各行金额相加当作实际支出。',
            '未进行多训练种子或多采样种子复现实验。','']
    (OUT/'results_and_costs.txt').write_text('\n'.join(lines))
    print(json.dumps(dict(audit=audit['status'],requests=audit['successful_requests'],violations=len(violations),output=str(OUT))))


if __name__=='__main__':main()
