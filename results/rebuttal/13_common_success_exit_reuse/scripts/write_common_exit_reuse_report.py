import json
from pathlib import Path
from collections import Counter
from compare_exit_reuse_goals import W

OUT=W/'outputs/common_success_exit_reuse_final_20261112'
def main():
    s=json.loads((OUT/'summary.json').read_text());v=json.loads((OUT/'validation.json').read_text())
    rows=[json.loads(p.read_text()) for p in sorted((OUT/'cases').glob('*.json'))]
    balanced=json.loads((OUT/'balanced_eight_goal_statistics.json').read_text())
    followup=json.loads((OUT/'replay_followup.json').read_text()) if (OUT/'replay_followup.json').exists() else {'checks':[]}
    def metrics(p):
        n=sum(p.get(k,0) for k in ['both_supported','hidden_only','visible_only','neither_supported'])
        h=p.get('both_supported',0)+p.get('hidden_only',0);vis=p.get('both_supported',0)+p.get('visible_only',0)
        return n,h,vis
    n,h,vis=metrics(s['paired_all']);nc,hc,vc=metrics(s['paired_non_contextual'])
    report=f'''# 全部共同验证成功程序上的退出目标复用分析

**样本为全部 578 个共同成功程序**：原始冻结 GPT-5-nano/minimal CRAFT@16 隐藏组验证成功 602 个，可见组成功 672 个，两者交集为 578。选样只使用原始 `verified=true` 标记，不使用 strict/exact 结果或本次两组胜负。

## 主要配对结果

以下百分比使用完全相同、且两组都已得到确定结论的目标作为分母。

| 配对统计口径 | 共同可判定目标数 | 目标隐藏摘要支持 | 目标可见摘要支持 |
|---|---:|---:|---:|
| 全部退出后目标 | {n} | {h}（{100*h/n:.2f}%） | {vis}（{100*vis/n:.2f}%） |
| 其中：需要不变量提供额外信息 | {nc} | {hc}（{100*hc/nc:.2f}%） | {vc}（{100*vc/nc:.2f}%） |
| 严格每程序 8 个、两组全部可判定的 {balanced['fully_resolved_eight_goal_programs']} 个程序 | {balanced['target_count']} | {balanced['supports']['hidden']}（{100*balanced['supports']['hidden']/balanced['target_count']:.2f}%） | {balanced['supports']['visible']}（{100*balanced['supports']['visible']/balanced['target_count']:.2f}%） |

在共同可判定目标中，两组共同支持 {s['paired_all']['both_supported']} 个，仅隐藏组支持 {s['paired_all']['hidden_only']} 个，仅可见组支持 {s['paired_all']['visible_only']} 个。两组总体接近；本模板探测中，隐藏组在共同可判定目标上多支持 {h-vis} 个。

在严格每程序 8 个且全部可判定的子集中，隐藏组支持更多目标的程序有 {balanced['program_signs']['hidden_more']} 个，可见组支持更多的有 {balanced['program_signs']['visible_more']} 个，另有 {balanced['program_signs']['tie']} 个持平。该子集只用于补充统一目标数量的比较；全部 578 个程序的覆盖记录仍保留。

## 全量覆盖与未确认项

每个程序预留 8 个目标名额，共 4,624 个名额。全部程序均保留在清单中，**未达到每个程序都获得 8 个已确认正确目标**：

- 477 个程序取得完整 8 个目标。
- 总共确认 3,863 个正确目标；两组逐项检查同一套目标。
- 其中 3,687 个不能仅由退出上下文推出，需要不变量信息；176 个仅由退出上下文即可推出，单独标注。
- 247 个已确认正确目标在至少一组摘要上尚未取得确定比较结论，未混入上表的共同可判定分母。
- 另有 761 个目标名额未填充，不当作证明失败，也不用恒真式补齐。

| 未填充原因 | 程序数 | 目标名额 |
|---|---:|---:|
| 当前摘要下无可满足的正常退出上下文 | 26 | 208 |
| 程序语义模型暂不支持 | 29 | 232 |
| 两组摘要均未完成完整表达式翻译 | 24 | 192 |
| 固定模板池未找到足够目标 | 22 | 129 |
| 合计 | 101 | 761 |

26 个退出上下文不可满足的程序中包括无退出边的 `while(1)` 程序。不能把这类程序对任意后置条件的空集蕴含算作复用成功。语义模型限制包括全局量、循环内部局部声明及未初始化变量等；摘要翻译限制主要涉及变量除法/移位。原始验证成功标记仍保留，未重新运行或修改原实验。

| 对全部 3,863 个已确认正确目标的记录 | 隐藏组 | 可见组 |
|---|---:|---:|
| 已确认支持 | {s['totals']['hidden']['supported']} | {s['totals']['visible']['supported']} |
| 已确认摘要不蕴含 | {s['totals']['hidden']['not_entailed']} | {s['totals']['visible']['not_entailed']} |
| 尚未确认 | {sum(val for k,val in s['totals']['hidden'].items() if k.startswith('unconfirmed'))} | {sum(val for k,val in s['totals']['visible'].items() if k.startswith('unconfirmed'))} |

两组未确认数量不同，所以不能直接用这一表的原始支持数判断胜负。前面的配对表仅比较两组都已判定的同一目标。

## 目标如何构造

1. 从源码读取循环变量、参数、常量及语法结构，使用统一的八类模板：精确值、范围、输出与初值关系、变量间关系、守恒关系、同余、多项式关系和条件性质。所有候选池在检查开始前生成并保存哈希；模板构造不读取任何生成摘要。
2. 对每个候选，两组冻结摘要对称参与正确性确认。至少一组在可满足的退出上下文中证明该目标，才将其作为已确认正确目标；原始断言不作为假设。
3. 按固定类别和候选顺序优先选取需要不变量信息的目标，每类先取一个，再按类别轮转填充到 8 个。不按哪一组获胜排序。若不足 8 个，从同一冻结候选池补充退出条件本身即可证明的目标，并单独标注；入口、类型、frame 条件本身已蕴含的性质不会被用来补充。
4. 目标全部在循环退出后的状态求值，包括 `break` 前的更新。表达式使用建模的有定义 32 位整数 C 执行语义和数学整数逻辑。不声称循环终止。

候选来自源码，但正确性筛选使用了两组摘要，因此这是**对称的模板性质探测**，不是独立人工制定的 held-out 目标评测。目标可能彼此蕴含，也不保证每条都比原始目标更强。该结果支持“冻结摘要可用于多种额外性质”的分析，不足以单独证明隐藏目标普遍提高未来目标的复用率。

## 检查与证据

支持要求冻结摘要和入口/frame/类型/退出路径条件蕴含目标，且摘要的退出上下文可满足。`not_entailed` 的 SMT 反例仅表示摘要信息不足，不表示存在真实可达的错误执行。未支持的摘要子句只有在抽象其不支持运算后，仍被其余子句证明冗余，才可等价省略；否则记录为未确认。

本次模型调用、训练、Houdini 和 Frama-C 调用均为 0。候选筛查每查询 250 ms；最终目标、非空性和冗余检查每查询 5 s；Z3 4.13.3，seed 0。两阶段求解主循环约 43.5 s 和 38.3 s，文件归档和独立重放时间另计。

已保存 {v['preserved_input_files']} 份 source/verification/audit 原始输入副本，哈希与原实验一致。独立加载并重放 {v['queries']} 个保存的 SMT 义务：{v['matches']} 个状态一致，SAT/UNSAT 矛盾数为 {len(v['contradictions'])}；完整状态变化见 `validation.json`。重放若将原先 unknown 判定为 SAT/UNSAT，不追溯改变本表的冻结比较口径。

一个原先 UNSAT 的非线性义务在直接重放时超时。随后分别证明冻结摘要蕴含 `0 <= j <= i`，以及这一条件蕴含 `j*j <= i*i`，两条独立 SMT 义务均为 UNSAT，补充确认了该目标。详见 `replay_followup.json` 和 `replay_lemma_followup.json`；原始比较预算和结果均未改变。

`inventory.json` 包含全部 578 个程序；`protocol.json` 说明口径；`candidate_pools/` 保存源码模板候选；`cases/` 保存逐目标结果；`proof_queries/`、`replay/` 保存 SMT 和重放记录；`frozen_inputs/` 保存原始输入。`history/` 归档两阶段的完整筛选日志，最终组合不重新筛选任何不变量。

## 每个程序的覆盖与结果

| 程序 | 已确认目标数 / 8 | 隐藏组支持 | 可见组支持 | 未填充 |
|---|---:|---:|---:|---:|
'''
    for r in rows:
        counts={a:sum(g['arms'][a]['classification']=='supported' for g in r['selected_goals']) for a in ['hidden','visible']}
        report+=f"| {r['key']} | {len(r['selected_goals'])} | {counts['hidden']} | {counts['visible']} | {r['unfilled_slots']} |\n"
    (OUT/'REPORT.md').write_text(report)
    (OUT/'README.md').write_text(f'''# Exit-goal reuse on every jointly verified program

All **578** programs verified in both original GPT-5-nano/minimal CRAFT@16 runs
are retained. Selection does not depend on the hidden arm's strict/exact audit.
Source-only template pools are frozen first; correctness is then curated
symmetrically with both frozen summaries. This is not an independent held-out
goal benchmark, and the goals need not be logically independent.

| Jointly resolved targets | Hidden supports | Visible supports |
|---|---:|---:|
| All {n} paired exit targets | {h} | {vis} |
| {nc} targets requiring invariant information | {hc} | {vc} |

Eight slots per program were planned. 477 programs have eight confirmed targets;
3,863 correct targets were obtained in total. Of these, 247 remain unconfirmed in
at least one comparison arm. Another 761 slots remain unavailable across 101
programs, including 26 with infeasible exit contexts. All such cases remain in
the inventory, and none are counted as proof failures or vacuous successes.

See [REPORT.md](REPORT.md) for the full coverage, per-arm unresolved counts,
selection rules, semantics, and per-program results. No model generation,
training, Houdini, or Frama-C runs were performed.

Replay archived SMT without the original CRAFT environment:

```sh
python3 scripts/replay_common_exit_reuse.py --root . --workers 4
```

Requires Python and z3-solver 4.13.3. Reconstructing C-to-SMT queries additionally
requires CRAFT's `rl_pipeline`, pycparser, and adapting the historical paths in
the preserved construction scripts. `history/` retains the exact two-stage
candidate-screening records. Original chapter-organized results are unchanged.
''')

if __name__=='__main__':main()
