"""Offline, paired exit-goal entailment from immutable saved CRAFT summaries."""
import ast, concurrent.futures, hashlib, json, sys, time
from pathlib import Path
import z3
from pycparser import c_ast
from analyze_craft16_semantics import Analysis, Unsupported, conj, disj, boolean, _acsl_to_py

W=Path(__file__).resolve().parent.parent
OUT=W/'outputs/reuse_goal_comparison_20261112'
ROOTS={
 'hidden':Path('/home/yangfp/craft_empirical_runs/recovered_hidden_evidence_20261110/craft16'),
 'visible':Path('/home/yangfp/craft_empirical_runs/craft16_target_visible_20261110')}
TIMEOUT=5000
# All formulas are mathematical-integer postconditions at the immediate normal
# loop exit (including break). Original assertions are never assumed.
CATALOG=r'''
Loopy_135|i == 1000|c == 499500|2*c == i*(i-1)|c < 500000|c % 1000 == 500|c >= i|c + i == 500500|c / 1000 == 499
linear_255|v == 51|v < 52|v % 2 == 1|(v-1)/2 == 25|v >= 51|v*v == 2601|v % 3 == 0|100 <= 2*v && 2*v <= 102
linear_146|y == 1024|x == 0 && y == 1024|x+y == 1024|y-x == 1024|y > x|y % 1024 == 0|x*y == 0|x+y <= 1024
linear_278|i == j|k == \at(k,Pre)+j-\at(i,Pre)|k > \at(k,Pre)|i > \at(i,Pre)|k-i == \at(k,Pre)-\at(i,Pre)|k-\at(k,Pre) == i-\at(i,Pre)|k >= \at(k,Pre)+1|\at(k,Pre) == \at(i,Pre)-j+1 ==> k == 1
linear_284|x == 0|y == 0|x+y == 0|x == 4*y|x <= y|y <= x|x*x+y*y == 0|x <= 0 && y <= 0
linear_298|x == 10000000|x+z == 10000000|x-z == 10000000|x > z|x*z == 0|x % 10000000 == 0|x >= 10000000 && z <= 0|x <= 10000000 && z >= 0
NLA_lipus_32|sn == x*a|0 <= x && x <= 10|a > 0 ==> sn >= 0|a < 0 ==> sn <= 0|a == 0 ==> sn == 0|a != 0 ==> ((sn == 0) <==> (x == 0))|a >= 0 ==> sn <= 10*a|a <= 0 ==> sn >= 10*a
linear_209|y == 2*x|z == 3*x|z == x+y|3*y == 2*z|x <= y && y <= z|y % 2 == 0|z % 3 == 0|z-y == x
linear_16|n > 0 ==> x == n|n <= 0 ==> x == 0|n > 0 ==> m < n|n <= 0 ==> m == 0|0 <= m && m <= x|m != 0 ==> m < x|x >= n|m <= x && x >= 0
linear_267|j == n|k == \at(k,Pre)-n|k+j == \at(k,Pre)|k < \at(k,Pre)|j >= 1|\at(k,Pre) == n ==> k == 0|\at(k,Pre) > n ==> k > 0|k <= \at(k,Pre)-1
linear_277|i == 2*((n+1)/2)|k == n/2|n <= i && i <= n+1|2*k <= n|i % 2 == 0|2*k+i == 2*n|n % 2 == 0 ==> i == n|n % 2 == 1 ==> i == n+1
linear_287|x-y == \at(x,Pre)-\at(y,Pre)|x <= 100 && y <= 100|x > \at(x,Pre)|y > \at(y,Pre)|\at(x,Pre) == \at(y,Pre) ==> (x == 100 && y == 100)|\at(x,Pre) > \at(y,Pre) ==> x == 100|\at(x,Pre) < \at(y,Pre) ==> y == 100|x+y == 200-abs(\at(x,Pre)-\at(y,Pre))
Loopy_58|y == -1|x == 100 && y == -1|x+y == 99|x-y == 101|y < 0|x*y == -100|x+100*y == 0|x > y
Loopy_128|y == j-i|x == 0|((y == 0) <==> (i == j))|i >= 0|y <= j|i > j ==> y < 0|i < j ==> y > 0|y+i == j
linear_194|z == i|y == -i|x == 0|x+z == i|y+2*z == i|0 <= z && z <= i|y <= 0|i > 0 ==> z > 0
linear_94|i == n+1|2*j == (n+1)*(n+2)|j >= i|j >= n+1|2*j == i*(i+1)|n == 0 ==> j == 1|n > 0 ==> j > i|j >= 1
Loopy_390|n <= l ==> i == l|n > l ==> i == n|i >= n|i >= l|i == l || i == n|i-l >= 0|i-l <= abs(n-l)|i <= abs(n)+l
linear_145|lo == \at(mid,Pre)|hi == \at(mid,Pre)|mid == 0|lo > 0|hi > 0|lo+hi == 2*\at(mid,Pre)|lo-mid == \at(mid,Pre)|hi+mid == \at(mid,Pre)
linear_39|c >= 0|c <= n|c*(n-c) >= 0|c < n ==> c+1 <= n|c > 0 ==> c >= 1|n == 1 ==> (c == 0 || c == 1)|c == 0 || 1 <= c && c <= n|c*c <= n*n
linear_263|i == 2*k|i == 2*n|i % 2 == 0|2*n-i == 0|i >= n|0 <= n && n <= k|k == 0 ==> i == 0|k > 0 ==> i > n
linear_78|0 <= i && i <= y|i <= x|y == 0 ==> i == 0|i*(i-y) <= 0|i < y ==> i+1 <= y|i == 0 || 1 <= i && i <= y|x == 0 ==> i == 0|i*i <= y*y
Loopy_123|0 <= x && x <= 1|0 <= y && y <= 1|x+w == 1|y == z|w+z == 1|x == 0 ==> w == 1|x == 1 ==> w == 0|(x == 0 && y == 0 && w == 1 && z == 0) || (x == 1 && y == 1 && w == 0 && z == 1)
linear_10|x-y == \at(x,Pre)-\at(y,Pre)|x >= \at(x,Pre)|y >= \at(y,Pre)|x % 2 == \at(x,Pre) % 2|y % 2 == \at(y,Pre) % 2|-2 <= x-y && x-y <= 2|x >= 0 && y >= 0|\at(x,Pre) == \at(y,Pre) ==> x == y
linear_35|c <= 40|0 <= c && c <= 40|c < 40 ==> c+1 <= 40|((c == 0) <==> (c <= 0))|c*(40-c) >= 0|c*c <= 1600|c > 0 ==> c >= 1|c == 40 || c <= 39
NLA_lipus_43|a <= r*(r+1)|r*(r-1) < a|0 <= x && x <= r|r >= 1|2*x == a-r*(r-1)|r <= a/2|x <= a/2|a == 2 ==> (r == 1 && x == 1)
linear_27|n <= 0 ==> x == n|x <= 1|x <= n|x == n || x == 1|n-x >= 0|n > 1 ==> n-x == n-1|n <= 1 ==> n-x == 0|((x < 0) <==> (n < 0))
linear_102|n < 0 ==> x == 0|x >= 0|x >= n|x == 0 || x == n|((x == 0) <==> (n <= 0))|n > 0 ==> x > 0|x <= abs(n)|x*(x-n) == 0
linear_72|z == 36*y+c|0 <= c && c <= 36|36*y <= z && z <= 36*y+36|z <= 4608|z >= 0|c == 36 ==> z == 36*(y+1)|c == 0 ==> z == 36*y|z-36*y >= 0
Loopy_185|i == k|j == k-from|i == from+j|j <= k|from == k ==> j == 0|from < k ==> j > 0|j >= 0|i >= from
linear_93|i == n|n <= x && x <= 2*n|n <= y && y <= 2*n|abs(x-y) <= n|x-y <= n|y-x <= n|n == 0 ==> (x == 0 && y == 0)|n > 0 ==> (x > 0 && y > 0)
'''

class ExitAnalysis(Analysis):
    def compile(self,n,nxt,brk,cont):
        # Stop at the immediate loop exit, rather than the function end.
        if isinstance(n,c_ast.While):
            self.loop_line=n.coord.line
            k=self.add('pending');self.loops.append(k)
            end=self.add('return')
            body=self.compile(n.stmt,k,end,k)
            self.nodes[k]=('loop',n.cond,body,end)
            return k
        return super().compile(n,nxt,brk,cont)

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,obj):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
def solve(formula,p):
    s=z3.Solver();s.set(timeout=TIMEOUT,random_seed=0);s.add(formula)
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s.to_smt2())
    t=time.monotonic();r=s.check()
    out={'status':str(r),'seconds':time.monotonic()-t,'smt2':str(p.relative_to(OUT)),'sha256':digest(p)}
    if r==z3.sat:out['model']={str(d):str(s.model()[d]) for d in s.model().decls()}
    if r==z3.unknown:out['reason']=s.reason_unknown()
    return out

def abstract_divisions(a,text):
    """Only used to prove an unsupported clause redundant for ALL div values."""
    env=dict(a.cur);names={}
    class Replace(ast.NodeTransformer):
        def visit_BinOp(self,n):
            if isinstance(n.op,(ast.FloorDiv,ast.Mod,ast.Div)):
                key=ast.dump(n,include_attributes=False)
                if key not in names:
                    name='abstract_div_'+str(len(names));names[key]=name;env[name]=z3.Int(name)
                return ast.copy_location(ast.Name(id=names[key],ctx=ast.Load()),n)
            return self.generic_visit(n)
    tree=Replace().visit(ast.parse(_acsl_to_py(text),mode='eval'))
    return boolean(a.logic(ast.unparse(ast.fix_missing_locations(tree)),env))

def job(task):
    key,arm,goals=task;root=ROOTS[arm];d=OUT/'queries'/key/arm
    src=root/'sources'/f'{key}.original.c';vr=root/'verification'/f'{key}.json'
    rec=json.loads(vr.read_text());source=src.read_text()
    result={'key':key,'arm':arm,'source':str(src),'source_sha256':digest(src),'verification':str(vr),
            'verification_sha256':digest(vr),'survivor_count':len(rec['survivors']),'goals':[]}
    try:
        a=ExitAnalysis(source,{'survivors':[]});cs=[];unsupported=[]
        for c in rec['survivors']:
            try:cs.append(boolean(a.logic(c,a.cur)))
            except Exception as e:unsupported.append((c,str(e)))
        inv=conj(cs);redundancy=[]
        for j,(c,error) in enumerate(unsupported):
            try:
                q=abstract_divisions(a,c)
                proof=solve(z3.And(a.domain,inv,z3.Not(q)),d/f'redundancy_{j}.smt2')
            except Exception as e:proof={'status':'unsupported','reason':str(e)}
            redundancy.append({'clause':c,'translation_error':error,'proof':proof})
        result['unsupported_clause_redundancy']=redundancy
        complete=all(x['proof']['status']=='unsat' for x in redundancy)
        paths=disj([p for p,ev in a.exits])
        feasible=solve(z3.And(a.domain,inv,paths),d/'nonvacuity.smt2')
        result['nonvacuity']=feasible;result['exit_path_count']=len(a.exits)
        for j,g in enumerate(goals,1):
            try:
                bad=disj([z3.And(p,z3.Not(boolean(a.logic(g,ev)))) for p,ev in a.exits])
                proof=solve(z3.And(a.domain,inv,bad),d/f'goal_{j:02}.smt2')
                if not complete:status='unconfirmed_unsupported_clause'
                elif feasible['status']!='sat':status='unconfirmed_nonvacuity'
                elif proof['status']=='unsat':status='supported'
                elif proof['status']=='sat':status='not_entailed'
                else:status='unconfirmed_solver'
                result['goals'].append({'id':j,'goal':g,'classification':status,'proof':proof})
            except Exception as e:result['goals'].append({'id':j,'goal':g,'classification':'unconfirmed_translation','error':str(e)})
    except Exception as e:
        result['error']=repr(e)
        result['goals']=[{'id':j,'goal':g,'classification':'unconfirmed_translation'} for j,g in enumerate(goals,1)]
    assert digest(src)==result['source_sha256'] and digest(vr)==result['verification_sha256']
    write(OUT/'cases'/f'{key}_{arm}.json',result)
    return result

def main():
    global OUT, ROOTS
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=OUT)
    ap.add_argument('--hidden-root',type=Path,default=ROOTS['hidden']);ap.add_argument('--visible-root',type=Path,default=ROOTS['visible'])
    args=ap.parse_args();OUT=args.output;ROOTS={'hidden':args.hidden_root,'visible':args.visible_root}
    catalog={}
    # Split on a single |, preserving logical || in the formulas.
    import re
    for line in CATALOG.strip().splitlines():
        bits=re.split(r'(?<!\|)\|(?!\|)',line);catalog[bits[0]]=bits[1:]
    assert len(catalog)==30 and all(len(v)==8 for v in catalog.values())
    protocol={'scope':'immediate normal loop exit, including break','programs':30,'goals_per_program':8,
      'goals':catalog,'timeout_ms_per_query':TIMEOUT,'solver_seed':0,'selection':'Existing hidden strict-and-exact case set; not an unbiased or independently held-out sample.',
      'goal_dependence':'Goals can imply each other; 240 checks are not 240 logically independent properties.',
      'semantics':'Existing defined 32-bit scalar-integer program model; ACSL mathematical-integer postconditions.',
      'generation_calls':0,'Frama_C_calls':0,'Houdini_calls':0,'new_invariant_search':False}
    OUT.mkdir(parents=True,exist_ok=True)
    protocol_path=OUT/'protocol.json'
    if protocol_path.exists():assert json.loads(protocol_path.read_text())==protocol
    else:write(protocol_path,protocol)
    for key in catalog:
        assert (ROOTS['hidden']/'sources'/f'{key}.original.c').read_bytes()==(ROOTS['visible']/'sources'/f'{key}.original.c').read_bytes(),key
    tasks=[(key,arm,goals) for key,goals in catalog.items() for arm in ROOTS]
    t=time.monotonic();results=[]
    with concurrent.futures.ProcessPoolExecutor(max_workers=8) as pool:
        for result in pool.map(job,tasks):
            results.append(result)
            print(result['key'],result['arm'],sum(g['classification']=='supported' for g in result['goals']),flush=True)
    from collections import Counter
    totals={arm:dict(Counter(g['classification'] for r in results if r['arm']==arm for g in r['goals'])) for arm in ROOTS}
    by={(r['key'],r['arm']):r for r in results};paired=Counter();rows=[]
    for key in catalog:
        hs=by[key,'hidden']['goals'];vs=by[key,'visible']['goals']
        for h,v in zip(hs,vs):
            if h['classification'].startswith('unconfirmed') or v['classification'].startswith('unconfirmed'):paired['unconfirmed']+=1
            else:paired[('both' if h['classification']==v['classification']=='supported' else 'hidden_only' if h['classification']=='supported' else 'visible_only' if v['classification']=='supported' else 'neither')]+=1
        rows.append({'key':key,**{arm:sum(g['classification']=='supported' for g in by[key,arm]['goals']) for arm in ROOTS}})
    summary={'protocol_sha256':digest(protocol_path),'z3_version':z3.get_version_string(),'wall_seconds':time.monotonic()-t,'totals':totals,'paired':dict(paired),'programs':rows}
    write(OUT/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
