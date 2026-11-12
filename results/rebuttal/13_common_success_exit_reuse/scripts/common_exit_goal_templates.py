"""Source-only, deterministic grammar for post-loop reuse goals.

This module never reads a generated invariant or solver outcome.
"""
import hashlib, json, re
from collections import OrderedDict
from pycparser import c_ast, c_parser

FAMILIES=['exact_value','range','input_relation','pair_relation','conservation','modular','polynomial','conditional']
LIMIT=64

def make_pool(source):
    text=re.sub(r'/\*.*?\*/|//[^\n]*',' ',source,flags=re.S)
    macros=dict(re.findall(r'^\s*#define\s+(\w+)\s+([^\n]+)',text,re.M))
    text=re.sub(r'^\s*#.*$', '',text,flags=re.M)
    for k,v in macros.items():text=re.sub(r'\b'+re.escape(k)+r'\b','('+v+')',text)
    tree=c_parser.CParser().parse(text)
    def loops(node):return ([node] if isinstance(node,c_ast.While) else [])+sum((loops(ch) for _,ch in node.children()),[])
    funcs=[f for f in tree.ext if isinstance(f,c_ast.FuncDef) and loops(f)]
    if len(funcs)!=1 or len(loops(funcs[0]))!=1:raise ValueError('not one loop-containing function with one while')
    fn=funcs[0];loop=loops(fn)[0]
    params=sorted(p.name for p in (fn.decl.type.args.params if fn.decl.type.args else []) if isinstance(p,c_ast.Decl))
    class Scan(c_ast.NodeVisitor):
        def __init__(self):self.decls=[];self.changed=set();self.constants=[]
        def visit_Decl(self,n):
            if not isinstance(n.type,c_ast.FuncDecl):self.decls.append(n)
            self.generic_visit(n)
        def visit_Assignment(self,n):
            if isinstance(n.lvalue,c_ast.ID):self.changed.add(n.lvalue.name)
            self.generic_visit(n)
        def visit_UnaryOp(self,n):
            if n.op in ['p++','p--','++','--'] and isinstance(n.expr,c_ast.ID):self.changed.add(n.expr.name)
            self.generic_visit(n)
        def visit_Constant(self,n):
            try:self.constants.append(int(re.sub('[uUlL]+$','',n.value),0))
            except ValueError:pass
    scan=Scan();scan.visit(fn);body=Scan();body.visit(loop.stmt)
    names=sorted({d.name for d in scan.decls if d.coord.line<loop.coord.line}|set(params))
    outputs=sorted(set(names)&body.changed)
    if not outputs:outputs=names
    guard=Scan();guard.visit(loop.cond)
    # Prefer thresholds actually occurring in the guard, then source literals.
    raw=[0,1,-1]+guard.constants+scan.constants
    constants=list(dict.fromkeys(c for c in raw if abs(c)<2**31))[:12]
    anchors=[]
    for c in constants:
        for d in [0,-1,1]:
            if c+d not in anchors:anchors.append(c+d)
    anchors=anchors[:24]
    inputs=[(p,f'\\at({p},Pre)') for p in params]
    stationary=[n for n in names if n not in body.changed]
    refs=list(dict.fromkeys(stationary+[v for p,v in inputs]))
    fam={k:[] for k in FAMILIES};seen=set()
    def add(f,text):
        if len(fam[f])<LIMIT and text not in seen:
            fam[f].append(text);seen.add(text)
    for c in anchors:
        for x in outputs:add('exact_value',f'{x} == {c}')
    for c in anchors:
        for x in outputs:
            add('range',f'{x} >= {c}');add('range',f'{x} <= {c}')
    # Add source parameter bounds early enough to retain meaningful symbolic goals.
    symbolic=[]
    for p in refs:
        for x in outputs:
            if p==x:continue
            symbolic.extend([f'0 <= {x} && {x} <= {p}',f'{p} <= {x} && {x} <= ({p})+1',f'{x} >= {p}',f'{x} <= {p}'])
    fam['range']=list(dict.fromkeys(symbolic[:24]+fam['range']))[:LIMIT]
    for d in [0,1,-1,2,-2]:
        for p,ref in inputs:
            for x in outputs:
                add('input_relation',f'{x} == ({ref})+({d})')
                if d==0:
                    add('input_relation',f'{x} >= {ref}');add('input_relation',f'{x} <= {ref}')
    pairs=[(x,y) for i,x in enumerate(names) for y in names[i+1:] if x in outputs or y in outputs]
    for op in ['==','<=','>=']:
        for x,y in pairs:add('pair_relation',f'{x} {op} {y}')
    for c in [2,3,4,-1]:
        for x,y in pairs:
            add('pair_relation',f'{x} == ({c})*{y}');add('pair_relation',f'{y} == ({c})*{x}')
    for x,y in pairs:
        if x in params and y in params:
            add('conservation',f'{x}-{y} == \\at({x},Pre)-\\at({y},Pre)')
            add('conservation',f'{x}+{y} == \\at({x},Pre)+\\at({y},Pre)')
    for c in [1,2,3,-1]:
        for x,y in pairs:
            for p in refs:
                add('conservation',f'{x}+{y} == ({c})*({p})')
                add('conservation',f'{x}-{y} == ({c})*({p})')
    for c in constants:
        for x,y in pairs:add('conservation',f'{x}+{y} == {c}');add('conservation',f'{x}-{y} == {c}')
    mods=list(dict.fromkeys([2,3,4]+[c for c in constants if 2<=c<=16]))
    for m in mods:
        for rem in range(min(m,4)):
            for x in outputs:add('modular',f'{x} % {m} == {rem}')
    for p,ref in inputs:
        if p in outputs:
            for m in mods:add('modular',f'{p} % {m} == ({ref}) % {m}')
    for x,y in pairs:
        for sign in [-1,0,1]:
            add('polynomial',f'2*{x} == {y}*({y}+({sign}))')
            add('polynomial',f'2*{y} == {x}*({x}+({sign}))')
        add('polynomial',f'{x}*{y} == 0')
        add('polynomial',f'{x}*{x} <= {y}*{y}')
        add('polynomial',f'{y}*{y} <= {x}*{x}')
    for p in refs:
        for x in outputs:
            add('polynomial',f'{x}*{x} == {p}')
            add('polynomial',f'{x}*(({p})-{x}) >= 0')
    for p in refs:
        for x in outputs:
            if p==x:continue
            for g in [f'{p} <= 0 ==> {x} == 0',f'{p} == 0 ==> {x} == 0',f'{p} > 0 ==> {x} > 0',
                      f'{p} >= 0 ==> {x} >= 0',f'{p} < 0 ==> {x} <= 0',f'{p} > 0 ==> {x} == {p}',
                      f'{p} <= 0 ==> {x} == {p}',f'({x} == 0) <==> ({p} <= 0)']:
                add('conditional',g)
    # A fixed weakening menu provides useful bounds for small scalar loops.
    # It is generated before evaluating any model, not filled adaptively from wins.
    for d in [2,3,4,8,16,32]:
        for c in constants[:4]:
            for x in outputs:
                add('range',f'{x} >= {c-d}');add('range',f'{x} <= {c+d}')
    entries=[]
    for family in FAMILIES:
        for i,g in enumerate(fam[family]):entries.append({'id':f'{family}_{i+1:02}','family':family,'formula':g})
    return {'parameters':params,'variables':names,'modified_variables':outputs,'source_constants':constants,'families':FAMILIES,'candidates':entries}
