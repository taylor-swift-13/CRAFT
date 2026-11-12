"""Inventory every originally verified pair; no goal selection or generation."""
import concurrent.futures, hashlib, json, sys, time
from collections import Counter
from pathlib import Path
from compare_exit_reuse_goals import ROOTS, W, ExitAnalysis

OUT=W/'outputs/common_success_exit_reuse_20261112'
AUDITS={
 'hidden':Path('/home/yangfp/craft_empirical_runs/craft16_hidden_prefix_semantics_control_20261110/k16/cases'),
 'visible':Path('/home/yangfp/craft_empirical_runs/craft16_target_visible_semantics_20261110/k16/cases')}

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def inspect(row):
    r=dict(row);source=Path(r['arms']['hidden']['source_path']).read_text()
    try:
        a=ExitAnalysis(source,{'survivors':[]})
        r['model']={'status':'supported','variables':list(a.types),'parameters':a.params,'exit_paths':len(a.exits),'original_goals':a.goal_text}
    except Exception as e:r['model']={'status':'unsupported','error':repr(e)}
    return r

def main():
    allrows={arm:{p.stem:json.loads(p.read_text()) for p in (root/'verification').glob('*.json')} for arm,root in ROOTS.items()}
    success={arm:{k for k,r in rows.items() if r.get('verified') is True} for arm,rows in allrows.items()}
    common=sorted(success['hidden']&success['visible']);rows=[]
    for key in common:
        arms={}
        for arm,root in ROOTS.items():
            src=root/'sources'/f'{key}.original.c';vr=root/'verification'/f'{key}.json';audit=AUDITS[arm]/f'{key}.json'
            a=json.loads(audit.read_text())
            assert a['original_verified'] and a['source_sha256']==sha(src) and a['verification_sha256']==sha(vr),(key,arm)
            arms[arm]={'source_path':str(src),'source_sha256':sha(src),'verification_path':str(vr),'verification_sha256':sha(vr),
                       'audit_path':str(audit),'audit_sha256':sha(audit),'survivor_count':len(allrows[arm][key]['survivors'])}
        assert arms['hidden']['source_sha256']==arms['visible']['source_sha256'],key
        rows.append({'key':key,'arms':arms})
    with concurrent.futures.ProcessPoolExecutor(max_workers=8) as pool:rows=list(pool.map(inspect,rows))
    result={'selection':'All and only programs with verified=true in both original frozen @16 verification records.',
            'original_success_counts':{k:len(v) for k,v in success.items()},'common_success_count':len(common),
            'model_counts':dict(Counter(r['model']['status'] for r in rows)),
            'model_errors':dict(Counter(r['model'].get('error') for r in rows if r['model']['status']!='supported')),'programs':rows}
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'inventory.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='programs'},indent=2))

if __name__=='__main__':main()
