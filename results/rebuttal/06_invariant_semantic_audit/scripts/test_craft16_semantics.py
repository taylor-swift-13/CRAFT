from analyze_craft16_semantics import Analysis
def run(src,clauses):
    a=Analysis(src,{'survivors':clauses,'union':clauses})
    return a.strength(),a.exact(10)
src='void f(){int x=0; while(x<10){x++;} /*@ assert x>=0; */}'
s,e=run(src,['0<=x<=10'])
assert s['classification']=='strictly_stronger',s
assert e['classification']=='proven_exact',e
s,e=run(src,['x>=0'])
assert e['classification']!='proven_exact',e
s,e=run('void f(){int x=0; while(x<10){x++;} /*@ assert x==10; */}',['0<=x<=10'])
assert s['classification']=='target_equivalent',s
s,e=run('void f(){int x=0; while(x<10){x+=2;} /*@ assert x==10; */}',['0<=x<=10'])
assert e['classification']!='proven_exact',e
s,e=run('void f(){int x=0; while(x<10){x+=2;} /*@ assert x==10; */}',['0<=x<=10','x%2==0'])
assert e['classification']=='proven_exact',e
s,e=run('void f(){int x=0; while(1){if(x==10)break;x++;} if(x!=10)goto ERROR;return;ERROR:/*@ assert \\false; */;}',['0<=x<=10'])
assert s['classification']!='invariant_target_context_unsatisfiable',s
assert e['classification']=='proven_exact',e
s,e=run('void f(){int x=0; while(x<10){x++;} /*@ assert x==10; */}',['x<0'])
assert e['classification']=='not_inductive_in_audit_semantics',e
s,e=run('int unknown(); void f(int x,int y){while(1){if(unknown()){/*@ assert x>=0; */}else{/*@ assert y>=0; */}}}', ['x>=0','y>=0'])
# Both branches must satisfy their assertion; one favorable nondeterministic
# branch must not create a spurious strict-strength witness.
assert s['classification']=='target_equivalent',s
print('8 semantic checks passed, including universally quantified future choices.')
