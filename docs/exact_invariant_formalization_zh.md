# 整数循环的 Exact 不变式：定义、良基前驱覆盖定理与证明

本文统一定义整数循环、循环头可达状态、循环不变式、归纳不变式和 exact 不变式，并给出通过良基前驱覆盖认证 exact 的定理、证明及 SMT 检查形式。

## 1. 状态空间与符号约定

固定变量数 $n\ge 1$。一个程序状态是长度为 $n$ 的整数数组，也视为整数向量：

$$
x=(x_1,\ldots,x_n)\in S,\qquad S=\mathbb Z^n.
$$

所有整数均为数学整数，本节形式化不包含机器整数溢出。状态应包含决定循环后续行为所需的全部变量。

| 符号 | 含义 |
|---|---|
| $x,x',p$ | 当前状态、下一状态、前驱状态 |
| $Init\subseteq S$ | 初始循环头状态集合 |
| $G:S\to\{\mathrm{true},\mathrm{false}\}$ | 循环条件 |
| $B\subseteq S\times S$ | 执行一次完整循环体的状态关系 |
| $T_L\subseteq S\times S$ | 一步循环头转移关系 |
| $Reach_L\subseteq S$ | 全部循环头可达状态 |
| $I\subseteq S$ | 候选不变量描述的状态集合 |
| $(W,\prec)$ | 良基排序的值域及关系 |
| $\rho$ | 状态到排序值的映射 |

集合和谓词采用统一约定：$I(x)$ 表示 $x\in I$，$Init(x)$ 表示 $x\in Init$；$B(x,x')$ 表示 $(x,x')\in B$。

## 2. 循环的定义

**定义 1（循环）。** 一个带初始条件的循环是三元组：

$$
L=(Init,G,B).
$$

其程序形式为：

```text
从 Init 中取得初始状态 x
while G(x):
    取得满足 B(x, x') 的下一状态 x'
    x := x'
```

$Init$ 可以包含多个状态，因此不要求初始值是已知常数。$B$ 使用关系表示，允许同一状态具有多个可能的下一状态；确定性循环体是特殊情况，此时可写为 $B(x,x')\iff x'=f(x)$。

这里先讨论循环体正常完成后返回循环头的 `while` 循环。`break`、`return` 等额外控制出口可以通过额外的出口关系扩展。若 $G(x)$ 成立但不存在满足 $B(x,x')$ 的后继，则当前模型在该状态没有返回循环头的转移；不能将这种情况自动等同于正常退出。

**定义 2（一步转移）。** 从一个循环头到下一个循环头的合法转移为：

$$
T_L(x,x')\iff G(x)\land B(x,x').
$$

循环头位于检查条件之前。因此，执行循环体后得到的 $x'$ 即使不满足 $G(x')$，仍然是循环头状态；程序在该处检查条件后退出。

$T_L$ 由程序语义确定，不以候选不变量或待证明目标为额外假设。

## 3. 可达状态集合

**定义 3（有限步可达性）。** 定义至多执行 $k$ 次循环体后可到达的状态集合：

$$
\begin{aligned}
R_0&=Init,\\
R_{k+1}&=R_k\cup\{x'\in S\mid\exists x\in R_k,\ T_L(x,x')\}.
\end{aligned}
$$

全部循环头可达状态组成：

$$
Reach_L=\bigcup_{k\in\mathbb N}R_k,
\qquad\mathbb N=\{0,1,2,\ldots\}.
$$

等价地：

$$
\begin{aligned}
x\in Reach_L\iff\exists k\in\mathbb N,\ \exists x_0,\ldots,x_k\in S:\quad
&x_0\in Init\ \land\ x_k=x\\
&{}\land\bigwedge_{j=0}^{k-1}T_L(x_j,x_{j+1}).
\end{aligned}
$$

当 $k=0$ 时，转移条件的空合取为真，所以初始状态本身可达。定义包括所有有限执行前缀到达的状态，不要求程序最终终止。

## 4. 不变式、归纳不变式与 Exact

**定义 4（循环不变式）。** $I\subseteq S$ 是循环 $L$ 的不变式，当且仅当：

$$
Reach_L\subseteq I.
$$

即：

$$
\forall x\in S,\qquad x\in Reach_L\Rightarrow I(x).
$$

不变式可以包含不可达状态。

**定义 5（归纳循环不变式）。** $I\subseteq S$ 是归纳循环不变式，当且仅当同时满足：

$$
Init\subseteq I
$$

以及：

$$
\forall x,x'\in S,\qquad
I(x)\land T_L(x,x')\Rightarrow I(x').
$$

前者是初始化，后者是保持性。通过对执行路径长度归纳，可得每个归纳不变式都是循环不变式。反方向不一定成立：普通不变式只要求覆盖可达状态，归纳性还约束从 $I$ 中不可达状态出发的转移。

**定义 6（Exact 不变式）。** $I\subseteq S$ 是循环 $L$ 的 exact 不变式，当且仅当：

$$
\boxed{I=Reach_L.}
$$

等价地：

$$
\forall x\in S,\qquad I(x)\iff x\in Reach_L.
$$

这要求同时证明两个方向：

$$
\underbrace{Reach_L\subseteq I}_{\text{不遗漏可达状态}}
\qquad\text{以及}\qquad
\underbrace{I\subseteq Reach_L}_{\text{不包含不可达状态}}.
$$

Exact 不变式必然是归纳不变式，因为可达集合包含所有初始状态，并对合法转移封闭。在逻辑蕴含意义下，它也是最强的循环不变式：对于任意循环不变式 $J$，都有 $I\subseteq J$。这是语义层面的结论，不要求该集合一定能在某个预先限定的公式语言中表达。

例如，对 `x = 0; while (x < 10) x = x + 1;`：

$$
Reach_L=\{0,1,\ldots,10\}.
$$

| 候选谓词 | 循环不变式 | 归纳不变式 | Exact |
|---|---|---|---|
| $x\ge0$ | 是 | 是 | 否 |
| $x\le10$ | 是 | 是 | 否 |
| $0\le x\le10$ | 是 | 是 | 是 |
| $x\in\{0,\ldots,10\}\cup\{-2\}$ | 是 | 否：$-2$ 可以转移到集合外的 $-1$ | 否 |

## 5. 良基关系与前驱覆盖

**定义 7（良基关系）。** 设 $\prec$ 是集合 $W$ 上的二元关系。如果每个非空子集 $U\subseteq W$ 都包含一个关于 $\prec$ 的极小元素，则称 $\prec$ 良基：

$$
\forall U\subseteq W,\quad U\ne\varnothing
\Rightarrow
\exists w\in U,\ \neg\exists v\in U:\ v\prec w.
$$

例如，$(\mathbb N,<)$ 良基，$(\mathbb Z,<)$ 不良基。使用整数值排序时，需要提供相关状态上的下界；仅有严格下降不足以保证良基性。

给定映射 $\rho:I\to W$。**良基前驱覆盖**要求：

$$
\forall x\in S,\quad
I(x)\land\neg Init(x)
\Rightarrow
\exists p\in S:
I(p)\land T_L(p,x)\land\rho(p)\prec\rho(x).
$$

也就是：每个非初始不变量状态都有一个仍在 $I$ 中、能够合法执行一步到达它、且排序值更小的前驱。

其中要求 $G(p)$ 成立，不要求 $G(x)$ 成立。当前状态 $x$ 可以是检查条件为假的退出状态。

## 6. Exact 认证定理

**定理 1（良基前驱覆盖蕴含 Exact）。** 给定循环 $L=(Init,G,B)$ 和集合 $I\subseteq S$。若存在良基关系 $(W,\prec)$ 及映射 $\rho:I\to W$，使以下三个条件成立，则 $I=Reach_L$。

**INIT：初始化。**

$$
\forall x\in S,\qquad Init(x)\Rightarrow I(x).
$$

**STEP：保持性。**

$$
\forall x,x'\in S,\qquad
I(x)\land T_L(x,x')\Rightarrow I(x').
$$

**PRED：良基前驱覆盖。**

$$
\forall x\in S,\quad
I(x)\land\neg Init(x)
\Rightarrow
\exists p\in S:
I(p)\land T_L(p,x)\land\rho(p)\prec\rho(x).
$$

**证明。** 分别证明两个集合包含关系。

首先，证明 $Reach_L\subseteq I$。对执行路径长度归纳：长度为零的路径终止于初始状态，由 INIT，该状态属于 $I$。假设路径已经到达 $I$ 中的状态，执行下一步合法转移后，由 STEP，后继仍属于 $I$。因此，所有有限步可达状态均属于 $I$。

其次，证明 $I\subseteq Reach_L$。反设存在不可达的不变量状态，令：

$$
U=I\setminus Reach_L\ne\varnothing.
$$

其排序值集合为：

$$
V=\{\rho(x)\mid x\in U\}.
$$

由于 $V$ 非空且 $\prec$ 良基，存在 $x\in U$，使 $\rho(x)$ 是 $V$ 中的极小元素。

所有初始状态都可达，而 $x$ 不可达，所以 $x\notin Init$。由 PRED，存在 $p$，满足：

$$
I(p),\qquad T_L(p,x),\qquad\rho(p)\prec\rho(x).
$$

如果 $p$ 可达，将一步转移 $T_L(p,x)$ 接在到达 $p$ 的有限路径后，就得到到达 $x$ 的有限路径，与 $x$ 不可达矛盾。因此 $p$ 不可达。

于是 $p\in U$，并且 $\rho(p)\in V$ 严格小于 $\rho(x)$，与 $\rho(x)$ 的极小性矛盾。因此 $U=\varnothing$，即 $I\subseteq Reach_L$。

结合两个方向，得到 $I=Reach_L$。证毕。

该定理不要求 $Init$ 是单点，也不要求循环确定性或终止。

## 7. 整数排序函数与 SMT 检查

**推论 1（非负整数排序证书）。** 选择整数值函数 $\rho:S\to\mathbb Z$。若 INIT、STEP 以及以下两个条件成立，则 $I=Reach_L$。

**RANK：排序下界。**

$$
\forall x\in S,\qquad I(x)\Rightarrow\rho(x)\ge0.
$$

**PRED-Z：严格下降的前驱覆盖。**

$$
\forall x\in S,\quad
I(x)\land\neg Init(x)
\Rightarrow
\exists p\in S:
I(p)\land T_L(p,x)\land\rho(p)<\rho(x).
$$

**证明。** RANK 保证 $\rho$ 在 $I$ 上取值于 $\mathbb N$。以 $(W,\prec)=(\mathbb N,<)$ 应用定理 1 即得。证毕。

检查时，分别向 SMT 求解器提交以下反例公式。公式中的自由变量由求解器寻找赋值，四项均为 `unsat` 即建立上述四个条件。

| 义务 | 反例公式 |
|---|---|
| INIT | $Init(x)\land\neg I(x)$ |
| STEP | $I(x)\land G(x)\land B(x,x')\land\neg I(x')$ |
| RANK | $I(x)\land\rho(x)<0$ |
| PRED-Z | $I(x)\land\neg Init(x)\land\neg\exists p\,[I(p)\land G(p)\land B(p,x)\land\rho(p)<\rho(x)]$ |

若循环体存在非确定性选择 $\eta$，可以写成 $B(p,x)\iff\exists\eta\,C(p,\eta,x)$；前驱查询中的见证因此包括前驱状态和该步选择。不同执行步骤可以有不同选择。

排序值按数学整数计算，不必能存入程序自身的整数变量中。若已知 $I$ 蕴含某个变量的下界 $\ell$，可尝试 $\rho(x)=x_j-\ell$；若已知上界 $u$，可尝试 $\rho(x)=u-x_j$。这些界也可以依赖整条路径中保持不变的入口参数。

在本文的 $\mathbb Z^n$ 模型中，变量没有默认的机器类型上下界，因此必须显式建立所需边界。不能仅凭“变量是整数”就认定严格大小关系良基。

`unknown` 或超时不建立证明。某个具体排序函数的前驱查询为 `sat`，只说明该排序证书失败，不直接说明 $I$ 不是 exact。

## 8. 符号初始值与入口参数

初始状态为符号值时，可以直接使用包含多个状态的 $Init$。只要固定参数被保留在状态中，并由 $B$ 保持其值，上述定义和定理无需改变。

若要显式区分每个入口上下文，令符号参数为 $\theta\in\Theta\subseteq\mathbb Z^m$，合法性条件为 $A(\theta)$，并定义循环族：

$$
L_\theta=(Init_\theta,G_\theta,B_\theta).
$$

对每个满足 $A(\theta)$ 的固定参数，应用定理 1，得到：

$$
\forall\theta,\qquad
A(\theta)\Rightarrow I_\theta=Reach_{L_\theta}.
$$

前驱条件的量词为：

$$
\forall\theta\,\forall x,\quad
A(\theta)\land I_\theta(x)\land\neg Init_\theta(x)
\Rightarrow
\exists p:\ I_\theta(p)\land T_{L_\theta}(p,x)
\land\rho_\theta(p)\prec\rho_\theta(x).
$$

寻找前驱时，$\theta$ 保持不变。若入口快照为 $e$，可以将它保存在参数中，并定义 $Init_e(x)\iff x=e$。$e$ 可以是任意合法符号向量，不需要是已知数值。

入口参数化的 exact 保留每个入口与后续状态之间的关系；仅对所有入口的可达状态取并集，会丢失这种关联，不能直接替代入口参数化的结论。

## 9. 完整例子：符号输入的递增循环

考虑数学整数程序：

```text
输入 a, b，满足 a <= b
v := a
while v < b:
    v := v + 1
```

为保留所有输入信息，取状态 $x=(a,b,v)\in\mathbb Z^3$。定义：

$$
Init=\{(a,b,v)\mid a\le b\land v=a\},
$$

$$
G(a,b,v)\iff v<b,
$$

$$
B\bigl((a,b,v),(a',b',v')\bigr)
\iff a'=a\land b'=b\land v'=v+1.
$$

选择候选集合和排序函数：

$$
I=\{(a,b,v)\mid a\le v\le b\},
\qquad\rho(a,b,v)=v-a.
$$

四个证明义务如下。

1. **INIT：** 初始时 $v=a$ 且 $a\le b$，所以 $a\le v\le b$。
2. **STEP：** 若 $a\le v\le b$ 且 $v<b$，由于变量为整数，得到 $a\le v+1\le b$；同时 $a,b$ 保持不变。
3. **RANK：** 由 $v\ge a$，得到 $v-a\ge0$。
4. **PRED-Z：** 任取 $x=(a,b,v)\in I\setminus Init$，可知 $v>a$。取 $p=(a,b,v-1)$，则 $a\le v-1<b$，所以 $I(p)$ 和 $G(p)$ 成立；执行循环体得到 $x$，且：

$$
\rho(p)=v-1-a=\rho(x)-1<\rho(x).
$$

由推论 1：

$$
\boxed{
Reach_L=\{(a,b,v)\in\mathbb Z^3\mid a\le v\le b\}.
}
$$

整个证明对所有满足 $a\le b$ 的符号输入成立，没有枚举具体输入值。对每个固定的 $a,b$，可达的 $v$ 恰好为 $a,a+1,\ldots,b$。

## 10. 良基条件为何必要，以及结论的范围

如果删去前驱条件中的排序要求，仅要求每个非初始状态具有前驱，不足以证明 exact。例如：

```text
x := 0
while true:
    x := x
```

取 $I=\{0,1\}$。初始化和保持性均成立；不可达状态 $1$ 也可以用自身作为前驱。因此无排序的前驱覆盖成立，但 $Reach_L=\{0\}\ne I$。良基条件排除了这种不可达状态之间相互提供前驱的情况。

本定理证明的是可达集合等价性，不是程序终止性。PRED 只要求每个非初始状态存在一个适当的前驱，不要求所有转移服从排序。即使程序有无限执行，它的所有有限步可达状态仍然可以具有 exact 描述。

Exact 针对固定状态表示、入口条件和转移语义成立。对简单 `while` 循环，循环退出时成立的状态性质 $Q$ 可以通过以下条件验证：

$$
\forall x,\qquad I(x)\land\neg G(x)\Rightarrow Q(x).
$$

若退出后还执行代码，需要继续沿相应出口路径传播状态，而不能直接把循环头性质等同于最终程序输出性质。

## 11. 与现有语义审计实现的对应

现有审计使用入口快照表示符号初始状态，将保留子句与入口、未修改变量及类型上下文结合，再检查初始化、保持性和前驱覆盖。其排序模板尝试单个被修改变量的两个方向；在有类型上下界时，分别对应减去下界或上界减去变量。

本文的定理以数学整数状态和给定的 $T_L$ 为基础。现有 C 审计另外建模支持的、具有定义行为的 32 位标量整数运算；将定理用于原始 C 程序，依赖相应入口和转移编码符合所声明的语义。定理本身不验证翻译器。

已有记录保存 SMT 义务，并在单独进程中重新读取和使用 Z3 求解。该重放检查保存义务的结果，不是另一个独立 C 翻译器或证明对象检查器。

相关仓库文件：

- [语义审计算法](../results/rebuttal/08_target_visibility_matched/scripts/analyze_craft16_semantics.py)：入口和域构建，以及 `Analysis.exact`。
- [义务重放与结果归集](../results/rebuttal/08_target_visibility_matched/scripts/audit_visible_prefix_semantics.py)：`required_queries`、`replay_worker` 和 `gated_classification`。
- [已有复用案例](../results/rebuttal/11_reuse_examples_readonly/REPORT.md)：基于冻结结果整理的程序和证据。
