import json
from pathlib import Path

W=Path(__file__).resolve().parent.parent
OUT=W/'outputs/reuse_goal_comparison_20261112'
s=json.loads((OUT/'summary.json').read_text())
p=json.loads((OUT/'protocol.json').read_text())
report='''# 冻结摘要对新增循环退出目标的复用

使用已有 GPT-5-nano/minimal CRAFT@16 的两组冻结摘要，在同一批 30 个程序上，每个程序检查相同的 8 个循环正常退出目标，共 240 个目标、480 次配对蕴含检查。所有目标均在循环退出后求值，包括 `break` 前已经执行的更新；不把循环头性质直接当作退出性质，也不假设原始断言成立。

| 统计口径 | 目标数 | 目标隐藏摘要支持 | 目标可见摘要支持 |
|---|---:|---:|---:|
| 全部新增退出目标 | 240 | 240（100.0%） | 209（87.1%） |
| 其中：仅入口、类型、frame 与退出路径条件即可推出 | 17 | 17 | 17 |
| 其中：需要不变量提供额外信息 | 223 | 223（100.0%） | 192（86.1%） |
| 8 个目标全部支持的程序 | 30 | 30 | 24 |

209 个目标得到两组摘要共同支持，31 个仅得到隐藏组摘要支持，差异集中在 6 个程序。所有 60 个摘要/程序的退出上下文均确认可满足；没有超时、未知或未支持语法导致的未确认目标。

这里的“支持”指冻结不变量与共同程序上下文蕴含该退出目标。31 个未蕴含结果都有 SMT 反例；它们说明摘要未提供足够信息，不表示程序真实执行会违反这些目标。

## 代表性案例

| 程序 | 原始目标 | 新的退出后信息示例 | 隐藏 / 可见支持数 |
|---|---|---|---:|
| linear_145 | `lo == hi` | `lo == mid₀`、`hi == mid₀`、`lo > 0`：恢复两个输出的精确值，而不只是二者相等 | 8 / 1 |
| linear_10 | `y == 0 ⇒ x != 4` | 保持 `x−y == x₀−y₀`、两个变量各自的初值奇偶性及单调性 | 8 / 1 |
| linear_102 | `n >= 0 ⇒ x == n` | 补全负输入分支：`n < 0 ⇒ x == 0`，以及 `x == 0 ⇔ n <= 0` | 8 / 3 |
| Loopy_58 | `x == 100` | 退出时 `y == -1`；该值来自 `break` 前的递减，两组均保留了所需信息 | 8 / 8 |

## 口径与复现

- 这 30 个程序来自先前隐藏组的 strict/exact 已认证案例集。目标设计也参考了这些已知性质，因此这是针对既有案例的配对复用分析，不是随机抽样或独立 held-out 目标评测，不能据此推断隐藏目标通常优于可见目标。
- 每个程序的 8 个目标完全相同地应用于两组；目标之间可以相互蕴含，240 是目标检查数，不是独立性质数。额外列出的 223 项排除了程序上下文本身即可证明的 17 项。
- 沿用已建模的 defined 32-bit scalar-integer 执行语义，目标中的算术为数学整数。检查正常退出后的部分正确性，不声称终止。
- 非空性检查：`context ∧ invariant ∧ exit_path` 可满足。蕴含检查：对所有建模的退出路径，`context ∧ invariant ∧ exit_path ∧ ¬goal(exit_state)` 不可满足。
- 两个可见组摘要各包含一条旧解析器不支持的变量除法/取模子句。将这些运算抽象为任意整数后，SMT 证明该整条子句由其余已保存子句蕴含，故使用等价的可解析合取式检查。相关冗余证明也已保存；未修改或重新筛选原始摘要。
- 新模型调用、训练、Houdini、Frama-C 调用均为 **0**。本次新增工作仅为离线 SMT 检查。原始 source/verification 文件哈希在检查前后保持一致，完整副本存于 `frozen_inputs/`。
- 480 个目标查询、60 个非空性查询、2 个冗余查询共 542 个原始义务已在独立加载保存的 SMT2 文件后全部重放一致。另保存 240 个不使用不变量的上下文基线查询。
- Z3 4.13.3；每个查询上限 5 秒，solver seed 0；8 个工作进程。配对主检查耗时约 6.8 秒（不含随后基线、重放、归档）。

`protocol.json` 保存固定目标及口径；`cases/` 保存逐目标结果和反例；`queries/` 与 `context_queries/` 保存 SMT2；`validation.json` 保存重放结果；`input_provenance.json` 保存输入来源和哈希。

## 全部程序

| 程序 | 隐藏组支持 / 8 | 可见组支持 / 8 |
|---|---:|---:|
'''
for r in s['programs']:report+=f"| {r['key']} | {r['hidden']} | {r['visible']} |\n"
report+='\n## 全部退出目标\n\n`\\at(v,Pre)` 表示函数入口参数值。以下均在循环退出后的状态求值。\n'
for key,gs in p['goals'].items():
    report+=f'\n### {key}\n\n'+''.join(f'{i}. `{g}`\n' for i,g in enumerate(gs,1))
(OUT/'REPORT.md').write_text(report)
(OUT/'README.md').write_text('''# Paired reuse of frozen summaries for loop-exit goals

This supplementary case study uses the existing GPT-5-nano/minimal CRAFT@16
target-hidden and target-visible summaries. Thirty previously selected hidden
strict/exact cases each receive the same eight post-loop goals in both arms.

| Scope | Hidden | Visible |
|---|---:|---:|
| All 240 exit goals | 240 | 209 |
| 223 goals not implied by program context alone | 223 | 192 |
| Programs supporting all eight goals | 30 | 24 |

No model calls, invariant regeneration, Houdini, or Frama-C runs were performed.
These are paired offline entailment checks on an outcome-selected illustrative
case set, not an independent held-out-goal benchmark. Goals may imply each other.
See [REPORT.md](REPORT.md) for scope, semantics, representative cases, and all goals.

All input summaries and sources are copied unchanged in `frozen_inputs/`.
All obligations and counterexamples are retained. With Python and z3-solver
4.13.3, replay the saved queries without the original CRAFT environment:

```sh
python3 scripts/replay_exit_reuse_queries.py --root .
```

The construction script and an unchanged copy of the existing semantic engine
are included under `scripts/`; reconstructing C-to-SMT obligations additionally
requires the CRAFT repository's `rl_pipeline` and pycparser. The construction
script accepts `--output`, `--hidden-root`, and `--visible-root`. Supply an empty
output directory to keep archived results unchanged.
''')
