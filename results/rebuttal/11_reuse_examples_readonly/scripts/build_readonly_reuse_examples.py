"""Render hand-derived reuse examples from saved audits. No solver or model calls."""
import json,hashlib
from pathlib import Path

W=Path(__file__).resolve().parent.parent
OUT=W/'outputs/reuse_examples_readonly_20261112'
# key | original target | selected facts of the frozen invariant (not a new invariant) | suggested targets | explanation
TEXT=r'''
Loopy_135|c≥0|0≤i≤1000；2c=i(i−1)|H: 2c=i(i−1)；H: c≤499500；E: i=1000；E: c=499500|退出时 i≥1000 与已有上界给出 i=1000，代入已保存求和关系得到精确结果。
linear_255|v≤52|1≤v≤51；v 为奇数|H: v mod 2=1；H: 1≤v≤51；E: v=51；E: (v−1)/2=25|退出时 v>50，结合已有上界得到 v=51；原目标仍允许 v=52。
linear_146|x=0|0≤y≤1024；x∈{0,1}；x=1 ⇔ y=0|H: x=1 ⇔ y=0；H: 0≤y≤1024；E: y=1024；E: x=0 ∧ y=1024|退出条件固定 y=1024；已有阶段关系再给出 x=0，摘要同时描述计数与状态切换。
linear_278|k>0|i₀≤i≤j；k−i=k₀−i₀|H: k−i=k₀−i₀；H: i₀≤i≤j；E: i=j；E: k=k₀+j−i₀；E: k>k₀|j 不变，退出时 i≥j，故 i=j；由精确差值恢复 k 的输出值，且入口 i₀<j。
linear_284|y≥0|x=4y；0≤y≤y₀|H: x=4y；H: 0≤y≤y₀；E: x=0；E: y=0|退出 x≤0 与 x=4y、y≥0 联合确定两个变量均为零。
linear_298|z=0|0≤x≤10⁷；x≤5·10⁶⇒z=5·10⁶；x≥5·10⁶⇒x+z=10⁷|H: x≤5·10⁶⇒z=5·10⁶；H: x≥5·10⁶⇒x+z=10⁷；E: x=10⁷；E: x=10⁷ ∧ z=0|摘要保留两个计算阶段，退出条件与上界确定 x，再推出 z。
NLA_lipus_32|sn=a·x ∨ sn=0|0≤x≤10；sn=a·x|H: 0≤x≤10；H: sn=a·x；E: a>0⇒sn≥0；E: a≠0⇒(sn=0 ⇔ x=0)|精确乘法关系去掉原目标的宽松析取分支；范围和代数关系分别可复用。
linear_209|x≥0|x≥0；y=2x；z=3x|H: y=2x；H: z=3x；E: z=x+y；E: 3y=2z；E: x≤y≤z|原目标只看 x 的符号；冻结摘要还刻画了三个计数器间的精确比例。
linear_16|n>0⇒m≥0|x,m≥0；m≤x；m≠0⇒m<x；x=m 或 x=n 或 x≤n|H: 0≤m≤x；H: m≠0⇒m<x；E: x=max(n,0)；E: n>0⇒0≤m<n|若 x=m，则严格关系迫使二者为零；结合其余析取与退出 x≥n 得到精确终点及选中下标范围。
linear_267|k≥0|0≤j≤n；k+j=k₀|H: k+j=k₀；H: 0≤j≤n；E: j=n；E: k=k₀−n|入口 n≥1、k₀≥n；退出 j>n−1 与上界确定 j=n，再恢复剩余额度。
linear_277|2k≥n−1|i≥0 且为偶数；i≤2⌈n/2⌉；k=n−i/2|H: 2k+i=2n；H: i mod 2=0；E: i=2⌈n/2⌉；E: k=⌊n/2⌋|入口 n≥0；退出 i≥n，结合偶数性和上界确定向上取偶数的终点。
linear_287|x=100 ∨ y=100|x,y≤100；x−y=x₀−y₀；x≥x₀、y≥y₀|H: x−y=x₀−y₀；H: x≥x₀ ∧ y≥y₀；E: max(x,y)=100；E: min(x,y)=100−abs(x₀−y₀)|边界条件只决定至少一个变量到 100；保存的差值进一步决定另一个变量。
Loopy_58|x=100|0≤x≤100；x≤50⇒y=x；x≥50⇒y=100−x|H: y=min(x,100−x)；H: 0≤y≤50；E: y=−1；E: x=100 ∧ y=−1|break 在 y 更新后、x 更新前发生。循环头 x=100,y=0，经 y-- 后退出；不能把循环头 y≥0 错用到退出点。
Loopy_128|i=j⇒y=0|x−y=i−j；x≤i；y≤j|H: x−y=i−j；H: x≤i ∧ y≤j；E: y=j−i；E: y=0 ⇔ i=j|正常退出 x=0，差值关系给出所有 i,j 下的输出，而不只相等输入这一种条件。
linear_194|y=−z|0≤x,z≤i；x+z=i；y+2z=i|H: x+z=i；H: y+2z=i；E: z=i；E: y=−i|退出 x=0 先确定 z，再确定 y；原目标只约束两个输出之间的关系。
linear_94|i+j+k>2n|0≤i≤n+1；2j=i(i+1)|H: 2j=i(i+1)；H: 0≤i≤n+1；E: i=n+1；E: 2j=(n+1)(n+2)|退出 i>n 后确定迭代次数和精确和式，远比原不等式提供的信息多。
Loopy_390|l≥1|i≥l；i=l 或 i≤n|H: i≥l；H: i=l ∨ i≤n；E: i=max(l,n)；E: i−l=max(n−l,0)|原目标仅重述入口条件。结合退出 i≥n，可完整描述循环对 i 的作用。
linear_145|lo=hi|hi−lo=2mid；lo+hi=2m₀；mid≥0|H: hi−lo=2mid；H: lo+hi=2m₀；E: lo=m₀；E: hi=m₀；E: mid=0|m₀ 为入口 mid。退出与非负性给出 mid=0，和、差两个关系确定 lo 与 hi。
linear_39|c=n⇒c≤n（恒真）|0≤c≤n|H: 0≤c≤n；E: c≥0；E: c≤n；E: c(n−c)≥0|原目标是恒真蕴含，作为弱目标极端例子；范围摘要仍有实质内容。不把四个相关性质称为四个独立目标。
linear_263|n=k|0≤i≤2k；n=⌈i/2⌉|H: 2n−i∈{0,1}；H: 0≤i≤2k；E: i=2k；E: i=2n|保存了中间状态的奇偶计数关系；退出时 i=2k，进而确定计数和迭代次数。
linear_78|i<y⇒i≥0|0≤i≤y；入口 x≥y≥0|H: 0≤i≤y；E: i≤x；E: y=0⇒i=0；E: i(i−y)≤0|范围摘要同时限制上界和下界；其中上界及与 x 的关系并非原目标所给出的信息。
Loopy_123|x=y|x,y,w,z∈{0,1}；x=y；w+z=1；w=1⇔x=0|H: x+w=1；H: y=z；H: w+z=1；E: x,y∈{0,1}|冻结摘要描述计数器与控制标志的对应关系，而不仅是两个计数器相等。
linear_10|y=0⇒x≠4|x−y=x₀−y₀；x≥x₀、y≥y₀；保留奇偶性|H: x−y=x₀−y₀；H: x mod 2=x₀ mod 2；H: x≥x₀ ∧ y≥y₀；E: −2≤x−y≤2|入口 x₀,y₀∈[0,2]。摘要保留差值、单调性和同余，原目标只排除一个条件状态。
linear_35|c≠40⇒c≥0|0≤c≤40|H: 0≤c≤40；E: c≤40；E: c<40⇒c+1≤40；E: c=0⇔c≤0|原目标未提供上界；上界可以支持后续有界索引或递增安全条件。
NLA_lipus_43|(r+1)²≥a|a=2x+r(r−1)；r≥0；x≥0；r(r−1)<a|H: a=2x+r(r−1)；H: r(r−1)<a；E: a≤r(r+1)；E: 0≤x≤r|退出 x≤r，代入保存的代数关系得到更紧的上下夹逼，而不只原始粗上界。
linear_27|n≥1⇒x=1|x≤n；n≥1⇒x≥1；n−x>0⇒x>0|H: x≤n；H: n≥1⇒x≥1；E: x=min(n,1)；E: n≤0⇒x=n|摘要还刻画原目标没有约束的 n≤0 分支：若 x<n≤0，则与 n−x>0⇒x>0 矛盾。
linear_102|n≥0⇒x=n|x≥0；n≥0⇒x≤n；x>0⇒x≤n|H: x≥0；H: x>0⇒x≤n；E: x=max(n,0)；E: n<0⇒x=0|负参数分支中 x>0 不可能，因此 x=0；补全原条件目标没有规定的行为。
linear_72|c<36⇒z≥0|0≤c≤36；z=36y+c；入口 0≤y≤127|H: z=36y+c；H: 0≤c≤36；E: 36y≤z≤36y+36；E: z≤4608|摘要把基址项与增量分开，给出相对范围和全局上界，不只条件性的非负性。
Loopy_185|j≤100|i=from+j；from≤i≤k|H: i=from+j；H: from≤i≤k；E: i=k；E: j=k−from|入口 0≤from≤k≤100；退出时 i≥k，恢复精确终点和区间长度。
linear_93|x+y=3n|0≤i≤n；x+y=3i；0≤x,y≤2i|H: x+y=3i；H: 0≤x,y≤2i；E: n≤x≤2n；E: n≤y≤2n；E: abs(x−y)≤n；E: i=n|退出 i=n；总和结合每一分量上界导出另一分量下界，得到原总和目标未包含的分量范围。
'''

PAIRS=[
('Loopy_135','i=1000','c=499500','i=1000,c=0','i=1001,c=499500','退出 i≥1000，原目标 c≥0'),
('linear_209','y=2x','z=3x','x=1,y=2,z=0','x=1,y=0,z=3','原目标 x≥0；未知条件可结束循环'),
('linear_278','i=j','k=k₀+j−i₀','i₀=0,j=2,k₀=1；i=2,k=1','i₀=0,j=2,k₀=1；i=3,k=3','入口约束、退出 i≥j、原目标 k>0'),
('linear_267','j=n','k=k₀−n','n=2,k₀=3；j=2,k=2','n=2,k₀=3；j=3,k=1','入口约束、退出 j≥n、原目标 k≥0'),
('Loopy_185','i=k','j=k−from','from=1,k=3；i=3,j=0','from=1,k=3；i=4,j=2','入口约束、退出 i≥k、原目标 j≤100'),
('NLA_lipus_32','0≤x≤10','sn=a·x','a=2,x=1,sn=0','a=2,x=11,sn=22','原目标 sn=a·x ∨ sn=0；未知条件可结束循环'),
('linear_72','0≤c≤36','z=36y+c','y=1,c=0,z=1','y=1,c=37,z=73','入口约束及原目标 c<36⇒z≥0'),
('linear_10','x mod 2=x₀ mod 2','x−y=x₀−y₀','x₀=y₀=0；x=2,y=1','x₀=y₀=0；x=1,y=1','入口约束及原目标 y=0⇒x≠4'),
('linear_93','i=n','n≤x≤2n','n=2,i=2,x=0,y=6','n=2,i=3,x=2,y=4','入口约束、退出 i≥n、原目标 x+y=3n'),
('linear_287','x≤100','y≤100','x₀=y₀=0；x=100,y=101','x₀=y₀=0；x=101,y=100','入口约束、退出条件及原目标 x=100 ∨ y=100'),
]

def main():
    records={r['key']:r for r in json.loads((OUT/'selected_existing_records.json').read_text())}
    rows=[]
    for line in TEXT.strip().splitlines():
        key,old,facts,goals,reason=line.split('|')
        r=records[key];a=r['audit'];rep=r['replay']
        assert a['original_verified'] and a['strength']['classification']=='strictly_stronger'
        assert a['exact']['classification']=='proven_exact' and rep['properties']['strength'] and rep['properties']['exact']
        assert hashlib.sha256(r['source'].encode()).hexdigest()==a['source_sha256']
        rows.append(dict(key=key,original_target=old,frozen_facts=facts,proposed_goals=goals.split('；'),
            derivation=reason,status='manual consequence of existing certified invariant; no new solver/verifier run'))
    assert len(rows)==30 and len({r['key'] for r in rows})==30
    lines=['# 旧 strict/exact 实验中的 30 个目标复用案例','',
      '全部程序、不变量和 strict/exact 证书来自此前 GPT-5-nano/minimal CRAFT@16 目标隐藏实验。'
      '本次只读取旧记录，未调用模型、验证器或 SMT；新增目标为人工推导的复用示例，不是新增验证成功率或独立 held-out 目标实验。','',
      'H 表示循环头，E 表示正常退出后的程序点；带下标 ₀ 的变量表示函数入口值（m₀ 特指入口 mid）。'
      '表达式使用数学整数含义、已有入口/frame 上下文和此前建模的有定义 32 位执行语义；不声称程序一定终止。'
      '“关键事实”是冻结不变量的相关信息节选，不是重新生成或重新验证的不变量。','',
      '| # | 原程序 | 原始目标 | 冻结摘要中的关键事实 | 可复用的新增目标（人工推导） |',
      '|---:|---|---|---|---|']
    for i,r in enumerate(rows,1):
        lines.append(f"| {i} | [{r['key']}](cases/{r['key']}.md) | {r['original_target']} | {r['frozen_facts']} | {'；'.join(r['proposed_goals'])} |")
        case=OUT/'cases'/f"{r['key']}.md";case.parent.mkdir(exist_ok=True)
        raw=records[r['key']]
        case.write_text('\n'.join(['# '+r['key'],'', '## 原始程序（未修改）','', '```c',raw['source'],'```','',
          '## 原有证据','',f"已有 original_verified=true、strictly_stronger、proven_exact，且保存的两项 replay property 均为 true。源文件 SHA256：{raw['audit']['source_sha256']}。",'',
          '## 冻结不变量（原样列出）','','```text','\n'.join(raw['survivors']),'```','',
          '## 本次人工设计的复用目标','',*[f'- {g}' for g in r['proposed_goals']],'',r['derivation'],'',
          '这些目标由旧摘要及对应控制流条件作逻辑推导；本次没有对新目标运行验证器或 SMT。','']),encoding='utf-8')
    lines+=['','## 同一程序中互不蕴含的目标对','',
      '以下为人工给出的逻辑反例，未运行 SMT。A 真 B 假和 B 真 A 假均满足列出的公共上下文。'
      '它们可以是程序不可达的赋值：用途是说明旧目标不能排除这些状态、新目标之间也不相互蕴含；不是声称程序存在错误执行。'
      '比较时不把完整不变量或可达性集合放入前提，否则所有受支持目标都已成立，比较会失去区分意义。','',
      '| 程序 | A | B | A 真、B 假 | B 真、A 假 | 公共上下文 |',
      '|---|---|---|---|---|---|']
    for pair in PAIRS:lines.append('| '+' | '.join(pair)+' |')
    lines+=['','## 建议优先选入正文的例子','',
      '优先采用 linear_209（单变量非负到多变量精确比例）、Loopy_135（非负到精确求和结果）、'
      'NLA_lipus_32（宽松析取到精确乘法关系与范围）、linear_72（条件非负到基址/增量关系）、'
      'Loopy_185（粗上界到精确区间长度）。Loopy_390 与 linear_39 的原目标分别重述入口条件和恒真式，'
      '适合作为极端弱目标补充例，不宜据此代表整个基准的难度。','',
      '表内新增目标不要求彼此全部独立；独立性仅对上面明确列出的 10 对作了人工反例说明。', '']
    (OUT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    (OUT/'reuse_table.json').write_text(json.dumps({'programs':rows,'incomparable_pairs':PAIRS,
        'model_calls':0,'verifier_calls':0,'smt_calls':0,'basis':'previously saved certificates plus manual logical derivations'},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'programs':len(rows),'proposed_goals':sum(len(r['proposed_goals']) for r in rows),'manual_incomparable_pairs':len(PAIRS),'out':str(OUT)},ensure_ascii=False))

if __name__=='__main__':main()
