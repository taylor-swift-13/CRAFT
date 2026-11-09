"""Export CRAFT@16 progress/final results, including upstream rejections."""
from pathlib import Path
from datetime import datetime, timezone
import csv
import hashlib
import io
import json
import time

RUN=Path('/tmp/craft16_minimal_20261107_v2')
OUT=Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20/outputs/craft16_minimal_v2')


def write(p,s):
    q=p.with_suffix(p.suffix+'.tmp');q.write_text(s,encoding='utf-8');q.replace(p)


def main():
    m=json.loads((RUN/'manifest.json').read_text())
    keys=[(Path(p).parent.name,Path(p).stem) for p in m['original_inputs']]
    if len(set(keys))!=832:raise ValueError('Unexpected task population')
    last=None
    while True:
        rows=[];calls=[];verify_seconds=[];pending_failed_attempts=0
        for suite,case in keys:
            key=suite+'_'+case;gp=RUN/'generations'/(key+'.json');vp=RUN/'verification'/(key+'.json')
            g=json.loads(gp.read_text()) if gp.exists() else {}
            v=json.loads(vp.read_text()) if vp.exists() else {}
            pending_failed_attempts+=len(g.get('pending_attempts',[]))+len(g.get('recovery_failed_history',[]))
            if g and g['fingerprint']!=m['fingerprint']:raise ValueError('Mixed generation protocol')
            if v and v['fingerprint']!=m['fingerprint']:raise ValueError('Mixed verification protocol')
            cs=g.get('calls',[]);calls.extend(cs)
            if v.get('status')=='complete':verify_seconds.append(v['seconds'])
            rows.append(dict(suite=suite,case_id=case,generation_status=g.get('status','pending'),
                responses=sum(len(c['raw_responses']) for c in cs),
                verification_status=v.get('status','pending'),verified=v.get('verified',''),
                known_tokens=sum((c.get('usage') or {}).get('total_tokens',0) for c in cs),
                rejection_reason=g.get('blocked_reason',''),error=g.get('error',v.get('error',''))))
        groups={}
        for suite in ['linear','NLA_lipus','Loopy','all']:
            rs=[r for r in rows if suite=='all' or r['suite']==suite]
            groups[suite]=dict(expected=len(rs),generated=sum(r['generation_status']=='complete' for r in rs),
                api_blocked=sum(r['generation_status']=='blocked' for r in rs),
                generation_errors=sum(r['generation_status']=='error' for r in rs),
                verified_complete=sum(r['verification_status']=='complete' for r in rs),
                verification_errors=sum(r['verification_status']=='error' for r in rs),
                success=sum(r['verification_status']=='complete' and r['verified'] is True for r in rs),
                known_tokens=sum(r['known_tokens'] for r in rs))
        t=groups['all']
        terminal_gen=t['generated']+t['api_blocked']+t['generation_errors']==832
        done=terminal_gen and t['verified_complete']+t['verification_errors']==t['generated']
        errors=t['generation_errors']+t['verification_errors']
        state=('needs_attention' if errors else ('awaiting_api_resolution' if t['api_blocked'] else 'complete')) if done else 'running'
        details=[c.get('usage') or {} for c in calls]
        report=dict(state=state,updated_utc=datetime.now(timezone.utc).isoformat(),by_suite=groups,
                    model=m['model'],reasoning_effort=m['reasoning_effort'],n_rollouts=16,
                    protocol_fingerprint=m['fingerprint'],recorded_calls=len(calls),
                    failed_request_attempts=sum(len(c.get('failed_attempts',[])) for c in calls)+pending_failed_attempts,
                    rejected_or_unresolved_request_attempts=pending_failed_attempts,
                    known_prompt_tokens=sum(u.get('prompt_tokens',0) for u in details),
                    known_completion_tokens=sum(u.get('completion_tokens',0) for u in details),
                    reasoning_tokens=sum((u.get('completion_tokens_details') or {}).get('reasoning_tokens',0) or 0 for u in details),
                    cached_prompt_tokens=sum((u.get('prompt_tokens_details') or {}).get('cached_tokens',0) or 0 for u in details),
                    mean_completed_verification_seconds=sum(verify_seconds)/len(verify_seconds) if verify_seconds else None,
                    scope='Fresh Openlux minimal outputs; four n=4 calls; historical none pools not mixed; original API rejections and unchanged-request recovery passes preserved in the listed addenda.',
                    recovery_addenda=sorted(p.name for p in RUN.glob('recovery_addendum*.json')),
                    interpretation='API-blocked programs do not have a complete @16 pool and are reported separately; conditional accuracy is not full-benchmark accuracy. Failed-request billing may be unavailable.')
        if done:
            changed=[p for p,h in m['original_inputs'].items() if hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h]
            changed_code=[p for p,h in m['sources'].items() if hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h]
            report['integrity']=dict(inputs_checked=832,changed_inputs=changed,changed_verification_sources=changed_code)
            if changed or changed_code:report['state']='invalid_source_changed'
            report['unresolved_programs']=t['api_blocked']+errors
            report['full_benchmark_lower_bound']=t['success']/832
            report['full_benchmark_upper_bound']=min(832,t['success']+t['api_blocked']+errors)/832
            if not t['api_blocked'] and not errors:report['success_over_all_attempted']=t['success']/832
            report['success_conditional_on_complete_generation']=t['success']/t['generated'] if t['generated'] else None
        write(OUT/'result.json',json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        buffer=io.StringIO();writer=csv.DictWriter(buffer,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        write(OUT/'per_program.csv',buffer.getvalue())
        lines=[f"状态：{report['state']}",f"更新（UTC）：{report['updated_utc']}",
               'GPT-5-nano / minimal；每程序4批×4回答，最多16条；每回答最多20条子句。',
               f"生成完整：{t['generated']}/832；接口拒绝：{t['api_blocked']}；生成错误：{t['generation_errors']}",
               f"验证完成：{t['verified_complete']}；验证错误：{t['verification_errors']}；已验证成功：{t['success']}",
               f"已记录调用：{len(calls)}；已知tokens：{t['known_tokens']}；reasoning tokens：{report['reasoning_tokens']}",
               '接口拒绝程序没有完整@16候选池，属于待处理，不是证明失败；不能将可用子集成功率当成全832结果。',
               '原实验数据仅作只读输入；本次minimal与历史none标记数据不同。','']
        for suite,c in groups.items():lines.append(f"{suite}: 生成{c['generated']}/{c['expected']}; 接口拒绝{c['api_blocked']}; 验证完成{c['verified_complete']}; 成功{c['success']}")
        if done:
            lines+=['',f"已证成功数：{t['success']}；全832未决：{t['api_blocked']+errors}，不能把未决项直接记为证明失败。",
                    f"生成成功子集条件成功率：{t['success']}/{t['generated']} = {t['success']/t['generated']:.2%}" if t['generated'] else '没有生成成功的完整候选池。',
                    f"完整性检查：{report['integrity']}"]
        else:lines+=['','结果未完成，当前成功数不能当作最终成功率。']
        write(OUT/'report.txt','\n'.join(lines)+'\n')
        sig=(state,t['generated'],t['api_blocked'],t['verified_complete'],t['success'],errors)
        if sig!=last:print(json.dumps(dict(state=state,generated=t['generated'],api_blocked=t['api_blocked'],verified_complete=t['verified_complete'],success=t['success'],errors=errors)),flush=True);last=sig
        if done and not t['api_blocked'] and not errors:return
        time.sleep(30)


if __name__=='__main__':main()
