# 旧 strict/exact 实验中的 30 个目标复用案例

全部程序、不变量和 strict/exact 证书来自此前 GPT-5-nano/minimal CRAFT@16 目标隐藏实验。本次只读取旧记录，未调用模型、验证器或 SMT；新增目标为人工推导的复用示例，不是新增验证成功率或独立 held-out 目标实验。

H 表示循环头，E 表示正常退出后的程序点；带下标 ₀ 的变量表示函数入口值（m₀ 特指入口 mid）。表达式使用数学整数含义、已有入口/frame 上下文和此前建模的有定义 32 位执行语义；不声称程序一定终止。“关键事实”是冻结不变量的相关信息节选，不是重新生成或重新验证的不变量。

| # | 原程序 | 原始目标 | 冻结摘要中的关键事实 | 可复用的新增目标（人工推导） |
|---:|---|---|---|---|
| 1 | [Loopy_135](cases/Loopy_135.md) | c≥0 | 0≤i≤1000；2c=i(i−1) | H: 2c=i(i−1)；H: c≤499500；E: i=1000；E: c=499500 |
| 2 | [linear_255](cases/linear_255.md) | v≤52 | 1≤v≤51；v 为奇数 | H: v mod 2=1；H: 1≤v≤51；E: v=51；E: (v−1)/2=25 |
| 3 | [linear_146](cases/linear_146.md) | x=0 | 0≤y≤1024；x∈{0,1}；x=1 ⇔ y=0 | H: x=1 ⇔ y=0；H: 0≤y≤1024；E: y=1024；E: x=0 ∧ y=1024 |
| 4 | [linear_278](cases/linear_278.md) | k>0 | i₀≤i≤j；k−i=k₀−i₀ | H: k−i=k₀−i₀；H: i₀≤i≤j；E: i=j；E: k=k₀+j−i₀；E: k>k₀ |
| 5 | [linear_284](cases/linear_284.md) | y≥0 | x=4y；0≤y≤y₀ | H: x=4y；H: 0≤y≤y₀；E: x=0；E: y=0 |
| 6 | [linear_298](cases/linear_298.md) | z=0 | 0≤x≤10⁷；x≤5·10⁶⇒z=5·10⁶；x≥5·10⁶⇒x+z=10⁷ | H: x≤5·10⁶⇒z=5·10⁶；H: x≥5·10⁶⇒x+z=10⁷；E: x=10⁷；E: x=10⁷ ∧ z=0 |
| 7 | [NLA_lipus_32](cases/NLA_lipus_32.md) | sn=a·x ∨ sn=0 | 0≤x≤10；sn=a·x | H: 0≤x≤10；H: sn=a·x；E: a>0⇒sn≥0；E: a≠0⇒(sn=0 ⇔ x=0) |
| 8 | [linear_209](cases/linear_209.md) | x≥0 | x≥0；y=2x；z=3x | H: y=2x；H: z=3x；E: z=x+y；E: 3y=2z；E: x≤y≤z |
| 9 | [linear_16](cases/linear_16.md) | n>0⇒m≥0 | x,m≥0；m≤x；m≠0⇒m<x；x=m 或 x=n 或 x≤n | H: 0≤m≤x；H: m≠0⇒m<x；E: x=max(n,0)；E: n>0⇒0≤m<n |
| 10 | [linear_267](cases/linear_267.md) | k≥0 | 0≤j≤n；k+j=k₀ | H: k+j=k₀；H: 0≤j≤n；E: j=n；E: k=k₀−n |
| 11 | [linear_277](cases/linear_277.md) | 2k≥n−1 | i≥0 且为偶数；i≤2⌈n/2⌉；k=n−i/2 | H: 2k+i=2n；H: i mod 2=0；E: i=2⌈n/2⌉；E: k=⌊n/2⌋ |
| 12 | [linear_287](cases/linear_287.md) | x=100 ∨ y=100 | x,y≤100；x−y=x₀−y₀；x≥x₀、y≥y₀ | H: x−y=x₀−y₀；H: x≥x₀ ∧ y≥y₀；E: max(x,y)=100；E: min(x,y)=100−abs(x₀−y₀) |
| 13 | [Loopy_58](cases/Loopy_58.md) | x=100 | 0≤x≤100；x≤50⇒y=x；x≥50⇒y=100−x | H: y=min(x,100−x)；H: 0≤y≤50；E: y=−1；E: x=100 ∧ y=−1 |
| 14 | [Loopy_128](cases/Loopy_128.md) | i=j⇒y=0 | x−y=i−j；x≤i；y≤j | H: x−y=i−j；H: x≤i ∧ y≤j；E: y=j−i；E: y=0 ⇔ i=j |
| 15 | [linear_194](cases/linear_194.md) | y=−z | 0≤x,z≤i；x+z=i；y+2z=i | H: x+z=i；H: y+2z=i；E: z=i；E: y=−i |
| 16 | [linear_94](cases/linear_94.md) | i+j+k>2n | 0≤i≤n+1；2j=i(i+1) | H: 2j=i(i+1)；H: 0≤i≤n+1；E: i=n+1；E: 2j=(n+1)(n+2) |
| 17 | [Loopy_390](cases/Loopy_390.md) | l≥1 | i≥l；i=l 或 i≤n | H: i≥l；H: i=l ∨ i≤n；E: i=max(l,n)；E: i−l=max(n−l,0) |
| 18 | [linear_145](cases/linear_145.md) | lo=hi | hi−lo=2mid；lo+hi=2m₀；mid≥0 | H: hi−lo=2mid；H: lo+hi=2m₀；E: lo=m₀；E: hi=m₀；E: mid=0 |
| 19 | [linear_39](cases/linear_39.md) | c=n⇒c≤n（恒真） | 0≤c≤n | H: 0≤c≤n；E: c≥0；E: c≤n；E: c(n−c)≥0 |
| 20 | [linear_263](cases/linear_263.md) | n=k | 0≤i≤2k；n=⌈i/2⌉ | H: 2n−i∈{0,1}；H: 0≤i≤2k；E: i=2k；E: i=2n |
| 21 | [linear_78](cases/linear_78.md) | i<y⇒i≥0 | 0≤i≤y；入口 x≥y≥0 | H: 0≤i≤y；E: i≤x；E: y=0⇒i=0；E: i(i−y)≤0 |
| 22 | [Loopy_123](cases/Loopy_123.md) | x=y | x,y,w,z∈{0,1}；x=y；w+z=1；w=1⇔x=0 | H: x+w=1；H: y=z；H: w+z=1；E: x,y∈{0,1} |
| 23 | [linear_10](cases/linear_10.md) | y=0⇒x≠4 | x−y=x₀−y₀；x≥x₀、y≥y₀；保留奇偶性 | H: x−y=x₀−y₀；H: x mod 2=x₀ mod 2；H: x≥x₀ ∧ y≥y₀；E: −2≤x−y≤2 |
| 24 | [linear_35](cases/linear_35.md) | c≠40⇒c≥0 | 0≤c≤40 | H: 0≤c≤40；E: c≤40；E: c<40⇒c+1≤40；E: c=0⇔c≤0 |
| 25 | [NLA_lipus_43](cases/NLA_lipus_43.md) | (r+1)²≥a | a=2x+r(r−1)；r≥0；x≥0；r(r−1)<a | H: a=2x+r(r−1)；H: r(r−1)<a；E: a≤r(r+1)；E: 0≤x≤r |
| 26 | [linear_27](cases/linear_27.md) | n≥1⇒x=1 | x≤n；n≥1⇒x≥1；n−x>0⇒x>0 | H: x≤n；H: n≥1⇒x≥1；E: x=min(n,1)；E: n≤0⇒x=n |
| 27 | [linear_102](cases/linear_102.md) | n≥0⇒x=n | x≥0；n≥0⇒x≤n；x>0⇒x≤n | H: x≥0；H: x>0⇒x≤n；E: x=max(n,0)；E: n<0⇒x=0 |
| 28 | [linear_72](cases/linear_72.md) | c<36⇒z≥0 | 0≤c≤36；z=36y+c；入口 0≤y≤127 | H: z=36y+c；H: 0≤c≤36；E: 36y≤z≤36y+36；E: z≤4608 |
| 29 | [Loopy_185](cases/Loopy_185.md) | j≤100 | i=from+j；from≤i≤k | H: i=from+j；H: from≤i≤k；E: i=k；E: j=k−from |
| 30 | [linear_93](cases/linear_93.md) | x+y=3n | 0≤i≤n；x+y=3i；0≤x,y≤2i | H: x+y=3i；H: 0≤x,y≤2i；E: n≤x≤2n；E: n≤y≤2n；E: abs(x−y)≤n；E: i=n |

## 同一程序中互不蕴含的目标对

以下为人工给出的逻辑反例，未运行 SMT。A 真 B 假和 B 真 A 假均满足列出的公共上下文。它们可以是程序不可达的赋值：用途是说明旧目标不能排除这些状态、新目标之间也不相互蕴含；不是声称程序存在错误执行。比较时不把完整不变量或可达性集合放入前提，否则所有受支持目标都已成立，比较会失去区分意义。

| 程序 | A | B | A 真、B 假 | B 真、A 假 | 公共上下文 |
|---|---|---|---|---|---|
| Loopy_135 | i=1000 | c=499500 | i=1000,c=0 | i=1001,c=499500 | 退出 i≥1000，原目标 c≥0 |
| linear_209 | y=2x | z=3x | x=1,y=2,z=0 | x=1,y=0,z=3 | 原目标 x≥0；未知条件可结束循环 |
| linear_278 | i=j | k=k₀+j−i₀ | i₀=0,j=2,k₀=1；i=2,k=1 | i₀=0,j=2,k₀=1；i=3,k=3 | 入口约束、退出 i≥j、原目标 k>0 |
| linear_267 | j=n | k=k₀−n | n=2,k₀=3；j=2,k=2 | n=2,k₀=3；j=3,k=1 | 入口约束、退出 j≥n、原目标 k≥0 |
| Loopy_185 | i=k | j=k−from | from=1,k=3；i=3,j=0 | from=1,k=3；i=4,j=2 | 入口约束、退出 i≥k、原目标 j≤100 |
| NLA_lipus_32 | 0≤x≤10 | sn=a·x | a=2,x=1,sn=0 | a=2,x=11,sn=22 | 原目标 sn=a·x ∨ sn=0；未知条件可结束循环 |
| linear_72 | 0≤c≤36 | z=36y+c | y=1,c=0,z=1 | y=1,c=37,z=73 | 入口约束及原目标 c<36⇒z≥0 |
| linear_10 | x mod 2=x₀ mod 2 | x−y=x₀−y₀ | x₀=y₀=0；x=2,y=1 | x₀=y₀=0；x=1,y=1 | 入口约束及原目标 y=0⇒x≠4 |
| linear_93 | i=n | n≤x≤2n | n=2,i=2,x=0,y=6 | n=2,i=3,x=2,y=4 | 入口约束、退出 i≥n、原目标 x+y=3n |
| linear_287 | x≤100 | y≤100 | x₀=y₀=0；x=100,y=101 | x₀=y₀=0；x=101,y=100 | 入口约束、退出条件及原目标 x=100 ∨ y=100 |

## 建议优先选入正文的例子

优先采用 linear_209（单变量非负到多变量精确比例）、Loopy_135（非负到精确求和结果）、NLA_lipus_32（宽松析取到精确乘法关系与范围）、linear_72（条件非负到基址/增量关系）、Loopy_185（粗上界到精确区间长度）。Loopy_390 与 linear_39 的原目标分别重述入口条件和恒真式，适合作为极端弱目标补充例，不宜据此代表整个基准的难度。

表内新增目标不要求彼此全部独立；独立性仅对上面明确列出的 10 对作了人工反例说明。
