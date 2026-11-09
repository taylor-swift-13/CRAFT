"""Read-only analysis of frozen CRAFT@16 clauses; writes only a separate audit.

Strength is checked at target-reaching loop-head contexts. Exactness is a
reachability claim, certified by inductiveness plus well-founded predecessor
coverage. Unsupported syntax and solver unknowns are never positive results.
"""
import argparse, ast, concurrent.futures, hashlib, json, os, re, sys, time
from collections import Counter
from pathlib import Path
sys.path.insert(0, '/home/yangfp/CRAFT')
import z3
from pycparser import c_parser, c_ast
from rl_pipeline.common.program import iter_acsl_clauses, parse_program
from rl_pipeline.common.state import _acsl_to_py, _c_name

RUN = Path('/tmp/craft16_minimal_20261107_v2')
MIN, MAX, MOD = -(2**31), 2**31-1, 2**32
class Unsupported(Exception): pass
def conj(xs): return z3.simplify(z3.And(*xs))
def disj(xs): return z3.simplify(z3.Or(*xs))
def boolean(x): return x if z3.is_bool(x) else x != 0
def integer(x): return z3.If(x, 1, 0) if z3.is_bool(x) else x
def truncdiv(a,b):
    q=z3.Abs(a)/z3.Abs(b)
    return z3.If((a>=0)==(b>=0),q,-q)
def subst(x, mp): return z3.simplify(z3.substitute(x, *list(mp.items())))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def check(formula, ms=1500, witness=False):
    s=z3.Solver(); s.set(timeout=ms); s.add(formula); status=s.check()
    out={'status':str(status)}
    if status==z3.unknown: out['reason']=s.reason_unknown()
    if status==z3.sat and witness:
        out['model']={str(d):str(s.model()[d]) for d in s.model().decls()}
    return out

class Analysis:
    def __init__(self, source, record):
        self.source=source; self.record=record; self.types={}; self.nodes={}; self.labels={}
        self.next_id=0; self.fresh_id=0; self.extra=[]; self.goal_text=[]; self.loops=[]
        self.proof_queries={}
        self.pre={}; self.entry={}; self.cur={}; self.prefix_goals=[]
        self.program=parse_program(source)
        text=source
        goals=list(iter_acsl_clauses(source,'assert'))
        self.goal_text=[e for a,b,e in goals]
        def replace_comment(match):
            ids=[i for i,(a,b,e) in enumerate(goals) if match.start()<=a and b<=match.end()]
            return ' '.join(f'__craft_goal_{i}();' for i in ids) or ' '
        text=re.sub(r'/\*.*?\*/|//[^\n]*',replace_comment,text,flags=re.S)
        macros=dict(re.findall(r'^\s*#define\s+(\w+)\s+([^\n]+)',text,re.M))
        text=re.sub(r'^\s*#.*$', '', text, flags=re.M)
        for name,value in macros.items(): text=re.sub(r'\b'+re.escape(name)+r'\b','('+value+')',text)
        self.macros=macros
        tree=c_parser.CParser().parse(text)
        def has_loop(node):
            return isinstance(node,c_ast.While) or any(has_loop(ch) for _,ch in node.children())
        funcs=[n for n in tree.ext if isinstance(n,c_ast.FuncDef) and has_loop(n)]
        if len(funcs)!=1: raise Unsupported('multiple loop-containing function definitions')
        self.func=funcs[0]
        class Collect(c_ast.NodeVisitor):
            def __init__(me): me.decls=[]; me.calls=[]
            def visit_Decl(me,n):
                if not isinstance(n.type,c_ast.FuncDecl): me.decls.append(n)
                me.generic_visit(n)
        collector=Collect(); collector.visit(self.func)
        for n in collector.decls:
            if not isinstance(n.type,c_ast.TypeDecl) or not isinstance(n.type.type,c_ast.IdentifierType):
                raise Unsupported('non-scalar declaration '+str(n.name))
            names=n.type.type.names
            if any(t not in ['int','unsigned','signed','_Bool'] for t in names):
                raise Unsupported('non-int type '+str(names))
            if n.name in self.types: raise Unsupported('shadowed declaration '+n.name)
            self.types[n.name]='u' if 'unsigned' in names else ('b' if '_Bool' in names else 'i')
        args=self.func.decl.type.args
        self.params=[p.name for p in (args.params if args else []) if isinstance(p,c_ast.Decl)]
        self.pre={n:z3.Int(n+'__pre') for n in self.params}
        self.entry={n:z3.Int(n+'__entry') for n in self.types}
        self.cur={n:z3.Int(n) for n in self.types}
        self.start=self.compile(self.func.body,None,None,None)
        if len(self.loops)!=1: raise Unsupported('not exactly one loop')
        if any(n.coord.line>self.loop_line for n in collector.decls):
            raise Unsupported('declaration not in loop-head scope')
        self.head=self.loops[0]
        self.requirements=list(iter_acsl_clauses(source,'requires'))
        env=dict(self.pre)
        for n in self.types:
            if n not in env: env[n]=z3.Int(n+'__uninitialized')
        req=[self.bounds(n,v) for n,v in self.pre.items()]
        for _,_,e in self.requirements: req.append(boolean(self.logic(e,env)))
        entries, prior, _=self.walk(self.start,env,conj(req),'prefix')
        if any(check(z3.And(p,z3.Not(q)),1000)['status']!='unsat' for p,q,i in prior):
            raise Unsupported('unproved target before loop')
        if not entries: raise Unsupported('no loop-entry path')
        for pc,ev in entries:
            for n,v in ev.items():
                if '__uninitialized' in str(v) and re.search(r'\b'+re.escape(n)+r'\b',self.program.loop.body+' '+self.program.loop.guard):
                    raise Unsupported('uninitialized loop-head variable '+n)
        entry_terms=[]
        for pc,ev in entries:
            entry_terms.append(conj([pc]+[self.entry[n]==ev[n] for n in self.types]))
        self.entry_context=conj([disj(entry_terms)]+[self.bounds(n,v) for n,v in self.entry.items()])
        # Frame variables are preserved syntactically by all loop-head transitions.
        prefix_choice_count=len(self.extra)
        self.transitions,self.targets,self.exits=self.walk(self.head,dict(self.cur),z3.BoolVal(True),'body')
        self.target_choices=self.extra[prefix_choice_count:]
        self.modified={n for n in self.types if any(not z3.eq(z3.simplify(ev[n]),self.cur[n]) for pc,ev in self.transitions+self.exits)}
        self.frame=conj([self.cur[n]==self.entry[n] for n in self.types if n not in self.modified])
        self.domain=conj([self.entry_context,self.frame]+[self.bounds(n,v) for n,v in self.cur.items()]+[self.bounds(n,v) for n,v in self.entry.items()])
        self.clauses=[boolean(self.logic(c,self.cur)) for c in record['survivors']]
        self.inv=conj(self.clauses)

    def bounds(self,n,v):
        t=self.types[n]
        return z3.And(v>=0,v<=(1 if t=='b' else MOD-1)) if t!='i' else z3.And(v>=MIN,v<=MAX)
    def add(self,op,*args):
        k=self.next_id; self.next_id+=1; self.nodes[k]=(op,*args); return k
    def compile(self,n,nxt,brk,cont):
        if n is None:return nxt
        if isinstance(n,c_ast.Compound):
            for ch in reversed(n.block_items or []): nxt=self.compile(ch,nxt,brk,cont)
            return nxt
        if isinstance(n,c_ast.If): return self.add('if',n.cond,self.compile(n.iftrue,nxt,brk,cont),self.compile(n.iffalse,nxt,brk,cont))
        if isinstance(n,c_ast.While):
            self.loop_line=n.coord.line
            k=self.add('pending'); self.loops.append(k)
            body=self.compile(n.stmt,k,nxt,k); self.nodes[k]=('loop',n.cond,body,nxt);return k
        if isinstance(n,c_ast.Label):
            k=self.compile(n.stmt,nxt,brk,cont);self.labels[n.name]=k;return k
        if isinstance(n,c_ast.Goto):return self.add('goto',n.name)
        if isinstance(n,c_ast.Break):return self.add('jump',brk)
        if isinstance(n,c_ast.Continue):return self.add('jump',cont)
        if isinstance(n,c_ast.Return):return self.add('return')
        if isinstance(n,c_ast.EmptyStatement):return nxt
        if isinstance(n,(c_ast.Decl,c_ast.Assignment,c_ast.UnaryOp,c_ast.FuncCall)):return self.add('stmt',n,nxt)
        raise Unsupported('statement '+type(n).__name__)

    def cexpr(self,n,env):
        if isinstance(n,c_ast.ID):
            if n.name not in env:raise Unsupported('unbound C identifier '+n.name)
            if '__uninitialized' in str(env[n.name]):raise Unsupported('uninitialized local read '+n.name)
            return env[n.name],self.types.get(n.name,'i'),z3.BoolVal(True)
        if isinstance(n,c_ast.Constant):
            raw=re.sub(r'[uUlL]+$','',n.value);v=int(raw,0) if raw.lower().startswith('0x') else int(raw)
            return z3.IntVal(v),'u' if 'u' in n.type or 'u' in n.value.lower() else 'i',z3.BoolVal(True)
        if isinstance(n,c_ast.FuncCall):
            name=n.name.name if isinstance(n.name,c_ast.ID) else ''
            if not any(w in name.lower() for w in ['unknown','nondet']):raise Unsupported('function '+name)
            self.fresh_id+=1;v=z3.Int('choice_'+str(self.fresh_id));self.extra.append(v)
            t='u' if 'uint' in name or 'unsigned' in name else 'i'
            return v,t,z3.And(v>=0,v<MOD) if t=='u' else z3.And(v>=MIN,v<=MAX)
        if isinstance(n,c_ast.Cast):
            if not isinstance(n.to_type.type,c_ast.TypeDecl):raise Unsupported('cast')
            names=n.to_type.type.type.names
            v,t,d=self.cexpr(n.expr,env)
            if 'unsigned' in names:return integer(v)%MOD,'u',d
            if names==['int']:return integer(v),'i',z3.And(d,integer(v)>=MIN,integer(v)<=MAX)
            raise Unsupported('cast type')
        if isinstance(n,c_ast.UnaryOp):
            v,t,d=self.cexpr(n.expr,env)
            if n.op=='!':return z3.Not(boolean(v)),'b',d
            if n.op=='+':return v,t,d
            if n.op=='-':
                out=-integer(v)
                return (out%MOD,t,d) if t=='u' else (out,t,z3.And(d,out>=MIN,out<=MAX))
            if n.op=='~':return z3.BV2Int(~z3.Int2BV(integer(v),32),is_signed=t!='u'),t,d
            raise Unsupported('side-effect expression '+n.op)
        if isinstance(n,c_ast.TernaryOp):
            c,_,dc=self.cexpr(n.cond,env);a,ta,da=self.cexpr(n.iftrue,env);b,tb,db=self.cexpr(n.iffalse,env)
            if ta!=tb:raise Unsupported('mixed ternary types')
            return z3.If(boolean(c),integer(a),integer(b)),ta,z3.And(dc,z3.If(boolean(c),da,db))
        if isinstance(n,c_ast.BinaryOp):
            a,ta,da=self.cexpr(n.left,env);b,tb,db=self.cexpr(n.right,env);op=n.op
            if op=='&&':return z3.And(boolean(a),boolean(b)),'b',z3.And(da,z3.Implies(boolean(a),db))
            if op=='||':return z3.Or(boolean(a),boolean(b)),'b',z3.And(da,z3.Implies(z3.Not(boolean(a)),db))
            a,b=integer(a),integer(b);t='u' if 'u' in [ta,tb] else 'i'
            if t=='u':a,b=a%MOD,b%MOD
            d=z3.And(da,db)
            if op in ['==','!=','<','<=','>','>=']:
                return {'==':a==b,'!=':a!=b,'<':a<b,'<=':a<=b,'>':a>b,'>=':a>=b}[op],'b',d
            if op=='+':v=a+b
            elif op=='-':v=a-b
            elif op=='*':v=a*b
            elif op in ['/','%']:
                q=truncdiv(a,b);v=q if op=='/' else a-q*b;d=z3.And(d,b!=0,z3.Not(z3.And(a==MIN,b==-1)))
            elif op in ['&','|','^']:
                av,bv=z3.Int2BV(a,32),z3.Int2BV(b,32)
                v=z3.BV2Int({'&':av&bv,'|':av|bv,'^':av^bv}[op],is_signed=t!='u')
            elif op in ['<<','>>']:
                if not z3.is_int_value(z3.simplify(b)):raise Unsupported('variable C shift')
                k=z3.simplify(b).as_long()
                if not 0<=k<32:raise Unsupported('invalid C shift')
                v=a*(2**k) if op=='<<' else a/(2**k)
                if op=='<<' and t=='i':d=z3.And(d,a>=0)
            else:raise Unsupported('C binary '+op)
            return (v%MOD,t,d) if t=='u' else (v,t,z3.And(d,v>=MIN,v<=MAX))
        raise Unsupported('C expression '+type(n).__name__)

    def logic(self,text,env):
        for name,value in self.macros.items():text=re.sub(r'\b'+re.escape(name)+r'\b','('+value+')',text)
        tree=ast.parse(_acsl_to_py(text),mode='eval')
        def go(n):
            if isinstance(n,ast.Expression):return go(n.body)
            if isinstance(n,ast.Constant):
                if isinstance(n.value,bool):return z3.BoolVal(n.value)
                if isinstance(n.value,int):return z3.IntVal(n.value)
                raise Unsupported('non-integer ACSL constant')
            if isinstance(n,ast.Name):
                name=_c_name(n.id)
                if name.endswith('__PRE__'):
                    key=name[:-7]
                    if key not in self.pre:raise Unsupported('local variable at Pre '+key)
                    return self.pre[key]
                if name.endswith('__LOOP_ENTRY__'):return self.entry[name[:-14]]
                if name not in env:raise Unsupported('unknown ACSL name '+name)
                return env[name]
            if isinstance(n,ast.UnaryOp):
                v=go(n.operand)
                if isinstance(n.op,ast.Not):return z3.Not(boolean(v))
                if isinstance(n.op,ast.USub):return -integer(v)
                if isinstance(n.op,ast.UAdd):return integer(v)
                raise Unsupported('ACSL unary')
            if isinstance(n,ast.BoolOp):return (z3.And if isinstance(n.op,ast.And) else z3.Or)(*[boolean(go(v)) for v in n.values])
            if isinstance(n,ast.BinOp):
                a,b=integer(go(n.left)),integer(go(n.right))
                if isinstance(n.op,ast.Add):return a+b
                if isinstance(n.op,ast.Sub):return a-b
                if isinstance(n.op,ast.Mult):return a*b
                if isinstance(n.op,(ast.FloorDiv,ast.Mod)):
                    if not z3.is_int_value(z3.simplify(b)) or z3.simplify(b).as_long()==0:raise Unsupported('nonconstant ACSL divisor')
                    q=truncdiv(a,b);return q if isinstance(n.op,ast.FloorDiv) else a-b*q
                if isinstance(n.op,(ast.LShift,ast.RShift)):
                    if not z3.is_int_value(z3.simplify(b)):raise Unsupported('variable ACSL shift')
                    k=z3.simplify(b).as_long()
                    if not 0<=k<64:raise Unsupported('large ACSL shift')
                    return a*(2**k) if isinstance(n.op,ast.LShift) else a/(2**k)
                raise Unsupported('ACSL binary '+type(n.op).__name__)
            if isinstance(n,ast.Compare):
                left=integer(go(n.left));parts=[]
                for op,r in zip(n.ops,n.comparators):
                    right=integer(go(r))
                    if isinstance(op,ast.Eq):v=left==right
                    elif isinstance(op,ast.NotEq):v=left!=right
                    elif isinstance(op,ast.Lt):v=left<right
                    elif isinstance(op,ast.LtE):v=left<=right
                    elif isinstance(op,ast.Gt):v=left>right
                    elif isinstance(op,ast.GtE):v=left>=right
                    else:raise Unsupported('ACSL comparison')
                    parts.append(v);left=right
                return conj(parts)
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='abs':return z3.Abs(integer(go(n.args[0])))
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='bool':return boolean(go(n.args[0]))
            raise Unsupported('ACSL syntax '+type(n).__name__)
        return z3.simplify(go(tree))

    def walk(self,start,env,pc,phase):
        queue=[(start,env,pc,0,False)]; heads=[];goals=[];exits=[];steps=0
        while queue:
            k,ev,p,depth,left=queue.pop();steps+=1
            if steps>2500 or depth>250:raise Unsupported('control-flow explosion')
            if z3.is_false(z3.simplify(p)):continue
            if k is None:exits.append((p,ev));continue
            if k==self.head and (phase=='prefix' or left):heads.append((p,ev));continue
            op,*args=self.nodes[k];ev=dict(ev)
            def push(t,e=ev,c=p):queue.append((t,dict(e),z3.simplify(c),depth+1,left or k==self.head))
            if op in ['if','loop']:
                val,_,defined=self.cexpr(args[0],ev)
                push(args[1],c=z3.And(p,defined,boolean(val)));push(args[2],c=z3.And(p,defined,z3.Not(boolean(val))))
            elif op=='goto':
                if args[0] not in self.labels:raise Unsupported('missing label')
                push(self.labels[args[0]])
            elif op=='jump':push(args[0])
            elif op=='return':exits.append((p,ev))
            elif op=='stmt':
                n,nxt=args
                if isinstance(n,c_ast.FuncCall):
                    name=n.name.name
                    if name.startswith('__craft_goal_'):
                        idx=int(name.rsplit('_',1)[1]);q=boolean(self.logic(self.goal_text[idx],ev))
                        goals.append((p,q,idx));push(nxt)
                    elif 'assume' in name.lower():
                        if not n.args or len(n.args.exprs)!=1:raise Unsupported('assume arguments')
                        v,_,d=self.cexpr(n.args.exprs[0],ev);push(nxt,c=z3.And(p,d,boolean(v)))
                    else:raise Unsupported('statement call '+name)
                else:
                    if isinstance(n,c_ast.Decl):
                        name=n.name
                        if n.init is None:push(nxt);continue
                        rhs=n.init
                    elif isinstance(n,c_ast.Assignment):
                        if not isinstance(n.lvalue,c_ast.ID):raise Unsupported('non-scalar assignment')
                        name=n.lvalue.name;rhs=n.rvalue
                        if n.op!='=':rhs=c_ast.BinaryOp(n.op[:-1],c_ast.ID(name),rhs)
                    elif isinstance(n,c_ast.UnaryOp) and n.op in ['p++','p--','++','--']:
                        if not isinstance(n.expr,c_ast.ID):raise Unsupported('increment')
                        name=n.expr.name;rhs=c_ast.BinaryOp('+' if '+' in n.op else '-',c_ast.ID(name),c_ast.Constant('int','1'))
                    else:raise Unsupported('statement expression')
                    val,t,d=self.cexpr(rhs,ev);val=integer(val)
                    if self.types[name]=='u':val=val%MOD
                    elif self.types[name]=='b':val=z3.If(val!=0,1,0)
                    ev[name]=z3.simplify(val);push(nxt,c=z3.And(p,d,self.bounds(name,ev[name])))
            else:raise Unsupported('CFG node '+op)
        return heads,goals,exits

    def strength(self):
        if not self.targets:return {'classification':'no_reachable_target_path'}
        paths=disj([p for p,q,i in self.targets])
        bad=disj([z3.And(p,z3.Not(q)) for p,q,i in self.targets])
        safety_label=any(z3.is_false(z3.simplify(q)) for p,q,i in self.targets)
        # Error-label assertions express avoidance of the path, not a meaningful
        # postcondition "false" to compare only on the error path itself.
        if safety_label:paths=disj([p for p,ev in self.transitions+self.exits])
        # Future nondeterministic choices are not components of a loop-head
        # state. A safe target obligation must hold for every such choice.
        if self.target_choices:
            paths=z3.Exists(self.target_choices,paths)
            bad=z3.Exists(self.target_choices,bad)
            try:
                g=z3.Goal();g.add(paths);paths=z3.TryFor(z3.Tactic('qe-light'),300)(g).as_expr()
                g=z3.Goal();g.add(bad);bad=z3.TryFor(z3.Tactic('qe-light'),300)(g).as_expr()
            except z3.Z3Exception:pass
        target=z3.Not(bad)
        ctx=conj([self.domain,paths])
        # Nonvacuity is required; an impossible exit is not useful extra strength.
        feasible=check(z3.And(ctx,self.inv),1500,True)
        fwd=check(z3.And(ctx,self.inv,z3.Not(target)),2000,True)
        rev=check(z3.And(ctx,target,z3.Not(self.inv)),2000,True)
        base=check(z3.And(ctx,z3.Not(target)),1500,True)
        for name,formula in [('strength_nonvacuity',z3.And(ctx,self.inv)),('strength_forward',z3.And(ctx,self.inv,z3.Not(target))),('strength_reverse',z3.And(ctx,target,z3.Not(self.inv)))]:
            s=z3.Solver();s.add(formula);self.proof_queries[name]=s.to_smt2()
        if feasible['status']=='unsat':label='invariant_target_context_unsatisfiable'
        elif feasible['status']!='sat':label='unknown_nonvacuity'
        elif fwd['status']=='unsat' and rev['status']=='sat':label='strictly_stronger'
        elif fwd['status']=='unsat' and rev['status']=='unsat':label='target_equivalent'
        elif fwd['status']=='sat' and rev['status']=='unsat':label='strictly_weaker'
        elif fwd['status']=='sat' and rev['status']=='sat':label='incomparable'
        else:label='unknown_implication'
        return {'classification':label,'target_condition_kind':'path-safety obligation' if safety_label else 'target-reaching context','invariant_context':feasible,'I_not_target':fwd,'target_not_I':rev,'target_without_invariant':base,'target_sites':len(set(i for p,q,i in self.targets)),'target_paths':len(self.targets)}

    def exact(self, budget=15):
        begin=time.monotonic();mp={self.cur[n]:self.entry[n] for n in self.types}
        init_formula=z3.And(self.entry_context,z3.Not(subst(self.inv,mp)))
        init_bad=check(init_formula,1500,True)
        next_bad=[]
        for pc,ev in self.transitions:
            next_bad.append(z3.And(pc,z3.Not(subst(self.inv,{self.cur[n]:ev[n] for n in self.types}))))
        pres_formula=z3.And(self.domain,self.inv,disj(next_bad))
        preservation=check(pres_formula,2500,True)
        for name,formula in [('initialization',init_formula),('preservation',pres_formula)]:
            s=z3.Solver();s.add(formula);self.proof_queries[name]=s.to_smt2()
        out={'initialization':init_bad,'preservation':preservation}
        if init_bad['status']=='sat' or preservation['status']=='sat':
            out['classification']='not_inductive_in_audit_semantics';return out
        if init_bad['status']!='unsat' or preservation['status']!='unsat':
            out['classification']='unknown_inductiveness';return out
        feasible=check(z3.And(self.domain,self.inv),1000,True)
        if feasible['status']!='sat':out['classification']='unknown_or_empty_initial_context';return out
        initial=conj([self.cur[n]==self.entry[n] for n in self.types])
        # First certify exactness for loop heads with no possible noninitial state.
        trivial=check(z3.And(self.domain,self.inv,z3.Not(initial)),1000)
        if trivial['status']=='unsat':
            s=z3.Solver();s.add(z3.And(self.domain,self.inv,z3.Not(initial)));self.proof_queries['exact_completeness']=s.to_smt2()
            out.update(classification='proven_exact',certificate='all invariant states are initial');return out
        # A stronger inductive lemma gives a concrete unreachable I-state.
        candidates=[]
        for n in sorted(self.modified):
            candidates += [(n+' == entry',self.cur[n]==self.entry[n]),(n+' >= entry',self.cur[n]>=self.entry[n]),(n+' <= entry',self.cur[n]<=self.entry[n])]
        existing=set(map(str,self.clauses))
        for c in self.record['union']:
            try:v=boolean(self.logic(c,self.cur))
            except Exception:continue
            if str(v) not in existing:candidates.append((c,v))
        seen=set()
        for name,j in candidates[:70]:
            if time.monotonic()-begin>budget*.55:break
            if str(j) in seen:continue
            seen.add(str(j))
            w=check(z3.And(self.domain,self.inv,z3.Not(j)),180,True)
            if w['status']!='sat':continue
            if check(z3.And(self.entry_context,z3.Not(subst(j,mp))),180)['status']!='unsat':continue
            nb=disj([z3.And(pc,z3.Not(subst(j,{self.cur[n]:ev[n] for n in self.types}))) for pc,ev in self.transitions])
            if check(z3.And(self.domain,self.inv,j,nb),400)['status']=='unsat':
                for label,formula in [('lemma_initialization',z3.And(self.entry_context,z3.Not(subst(j,mp)))),('lemma_preservation',z3.And(self.domain,self.inv,j,nb)),('lemma_unreachable_state',z3.And(self.domain,self.inv,z3.Not(j)))]:
                    s=z3.Solver();s.add(formula);self.proof_queries[label]=s.to_smt2()
                out.update(classification='proven_not_exact',inductive_lemma=name,unreachable_state=w['model']);return out
        prev={n:z3.Int(n+'__previous') for n in self.types};sp={self.cur[n]:prev[n] for n in self.types}
        rels=[]
        for pc,ev in self.transitions:
            rels.append(conj([subst(pc,sp)]+[self.cur[n]==subst(ev[n],sp) for n in self.types]))
        relation=disj(rels);previous=conj([subst(self.domain,sp),subst(self.inv,sp),relation])
        witnesses=list(prev.values())+self.extra
        attempts=[]
        # Strict monotonicity of a bounded integer gives a well-founded predecessor order.
        for n in sorted(self.modified):
            for direction in [1,-1]:
                if time.monotonic()-begin>budget:break
                order=prev[n]<self.cur[n] if direction==1 else prev[n]>self.cur[n]
                ex=z3.Exists(witnesses,z3.And(previous,order))
                try:
                    g=z3.Goal();g.add(ex)
                    qe=z3.TryFor(z3.Tactic('qe-light'),500)(g).as_expr()
                except z3.Z3Exception:qe=ex
                test=check(z3.And(self.domain,self.inv,z3.Not(initial),z3.Not(qe)),700)
                attempts.append({'variable':n,'direction':direction,**test})
                if test['status']=='unsat':
                    s=z3.Solver();s.add(z3.And(self.domain,self.inv,z3.Not(initial),z3.Not(qe)));self.proof_queries['exact_completeness']=s.to_smt2()
                    out.update(classification='proven_exact',certificate='inductive + well-founded predecessor coverage',rank_variable=n,rank_direction=direction,attempts=attempts);return out
        out.update(classification='unknown_reachability',attempts=attempts);return out

def worker(job):
    key,outdir,exact_budget=job;start=time.time();p=RUN/'verification'/(key+'.json');src=RUN/'sources'/(key+'.original.c')
    r=json.loads(p.read_text());result={'key':key,'suite':r['suite'],'original_verified':r['verified'],'survivor_count':len(r['survivors']),'source_sha256':sha(src),'verification_sha256':sha(p)}
    try:
        a=Analysis(src.read_text(),r)
        result['strength']=a.strength();result['exact']=a.exact(exact_budget)
        result['modeled_variables']=a.types;result['frame_variables']=sorted(set(a.types)-a.modified)
        proof_path=Path(outdir).parent/'proof_queries';proof_path.mkdir(exist_ok=True)
        (proof_path/(key+'.json')).write_text(json.dumps(a.proof_queries))
    except Exception as e:result['error']=type(e).__name__+': '+str(e)
    result['seconds']=time.time()-start
    (Path(outdir)/(key+'.json')).write_text(json.dumps(result,indent=2))
    return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);ap.add_argument('--jobs',type=int,default=8);ap.add_argument('--limit',type=int);ap.add_argument('--keys');ap.add_argument('--exact-budget',type=float,default=15);args=ap.parse_args()
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True);cases=out/'cases';cases.mkdir(exist_ok=True)
    keys=sorted(p.stem for p in (RUN/'verification').glob('*.json'))
    if args.keys:keys=args.keys.split(',')
    if args.limit:keys=keys[:args.limit]
    results=[];todo=[]
    for k in keys:
        p=cases/(k+'.json')
        if p.exists():results.append(json.loads(p.read_text()))
        else:todo.append((k,str(cases),args.exact_budget))
    def summary():
        s={'total':len(keys),'completed':len(results),'errors':dict(Counter(r.get('error') for r in results if 'error' in r)), 'strength':dict(Counter(r.get('strength',{}).get('classification','unsupported') for r in results)), 'exact':dict(Counter(r.get('exact',{}).get('classification','unsupported') for r in results))}
        s['by_suite']={suite:{field:dict(Counter(r.get(field,{}).get('classification','unsupported') for r in results if r['suite']==suite)) for field in ['strength','exact']} for suite in ['linear','NLA_lipus','Loopy']}
        (out/'summary.json').write_text(json.dumps(s,indent=2));return s
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for f in concurrent.futures.as_completed([pool.submit(worker,j) for j in todo]):
            results.append(f.result())
            if len(results)%100==0:
                s=summary();print(json.dumps({k:s[k] for k in ['completed','strength','exact']}),flush=True)
    print(json.dumps(summary()),flush=True)
if __name__=='__main__':main()
