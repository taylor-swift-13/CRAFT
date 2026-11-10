"""Render a Chinese summary from the completed matched comparison; read-only inputs."""
import json
import shutil
from pathlib import Path


def main():
    workspace = Path(__file__).resolve().parent.parent
    out = workspace / 'outputs/craft_visibility_comparison_20261110'
    data = json.loads((out / 'result.json').read_text())
    assert data['full_benchmark_complete'] is True
    usage = json.loads((workspace / 'outputs/craft16_target_visible_20261110/result.json').read_text())
    assert usage['full_benchmark_complete'] is True
    arms = data['arms']
    lines = [
        '# CRAFT 目标可见性对照实验', '',
        '两组均使用 GPT-5-nano/minimal、相同 832 个程序及提示词模板，每个程序生成 16 条回答。'
        'compose@1/@4/@8/@16 分别取各组固定回答池的前 1/4/8/16 条。'
        '实验配置唯一有意改变的是生成输入中是否提供原始验证目标；Houdini 过滤仍隐藏目标，最终用原始目标评分。', '',
        '| 回答数 | 目标隐藏：成功数 / 832 | 成功率 | 目标可见：成功数 / 832 | 成功率 | 可见 − 隐藏（百分点） |',
        '|---:|---:|---:|---:|---:|---:|',
    ]
    for k in ('1', '4', '8', '16'):
        h, v = (arms[a]['by_prefix'][k] for a in ('hidden', 'visible'))
        delta = data['paired'][k]['verification']['visible_minus_hidden_pp']
        lines.append(f"| {k} | {h['verified_success']}/832 | {h['success_percent_full']:.2f}% | "
                     f"{v['verified_success']}/832 | {v['success_percent_full']:.2f}% | {delta:+.2f} |")
    lines += ['', '## 已成功验证程序中的语义审计', '',
              '下表分母为各组、各回答预算下成功验证的程序数。两组使用同一分析器和证明预算，'
              '所有计入的性质均通过保存的 SMT 义务复核；未确认的情况不计入阳性数量。', '',
              '| 设置 | 回答数 | 严格强于原目标对应的循环头证明义务 | 精确描述所建模循环头可达状态 |',
              '|---|---:|---:|---:|']
    for arm, label in (('hidden', '目标隐藏'), ('visible', '目标可见')):
        for k in ('1', '4', '8', '16'):
            r = arms[arm]['by_prefix'][k]
            n = r['semantic_denominator_verified']
            lines.append(f"| {label} | {k} | {r['certified_strict_among_verified']}/{n} "
                         f"({r['strict_percent_verified']:.2f}%) | {r['certified_exact_among_verified']}/{n} "
                         f"({r['exact_percent_verified']:.2f}%) |")
    lines += ['', '严格更强要求在同一可满足上下文中，不变量蕴含翻译到循环头的目标义务，'
              '且反向蕴含存在反例。Exact 要求初始化、保持性及良基前驱覆盖：'
              '不变量内的每个非初始状态均有更早的合法前驱，有限回溯到初始状态，'
              '从而证明不变量与可达状态集合相等。语义范围为所建模、行为有定义的 32 位标量整数程序，'
              '包含保留的入口、frame 和类型上下文。', '',
              '## 同一批成功程序的配对比较', '',
              '上表两组的成功程序集合不同；下表只比较两组都成功验证的相同程序。', '',
              '| 回答数 | 共同成功程序数 | 严格更强：隐藏 / 可见 | Exact：隐藏 / 可见 |',
              '|---:|---:|---:|---:|']
    for k in ('1', '4', '8', '16'):
        p = data['paired'][k]
        s, e = p['strict_on_common_verified'], p['exact_on_common_verified']
        lines.append(f"| {k} | {p['common_verified_audited_programs']} | "
                     f"{s['hidden_count']} / {s['visible_count']} | {e['hidden_count']} / {e['visible_count']} |")
    lines += ['', '## 成本与记录', '',
              f"目标可见组完成 {usage['accepted_calls']:,} 次 n=4 请求、13,312 条回答，"
              f"记录用量为 {usage['total_tokens']:,} tokens（输入 {usage['input_tokens']:,}，"
              f"输出 {usage['output_tokens']:,}）。实际货币账单未由接口返回。", '',
              '各预算 token 用量见 [完整对比](REPORT.md)；@1 使用完整首批输入加首批平均单条输出估计，'
              '@4/@8/@16 使用实际完整批次用量，不能把重叠前缀相加。'
              '墙钟和累计计算耗时见 [时间统计](timing/REPORT.md)。', '',
              '本次隐藏组语义结果由同一通用分析器、15 秒 exact 搜索与 8 秒重放预算重新统计，'
              '未混入历史审计的额外前驱证书。原实验结果保留。'
              '与历史计数的逐项差异见 [审计差异说明](historical_audit_differences.md)，其中包括有限预算下的 unknown 变化。'
              '逐程序结果、配对集合、证书与输入哈希随本实验归档。', '']
    timing = workspace / 'outputs/craft_visibility_timing_20261110'
    for name in ('REPORT.md', 'result.json'):
        assert (timing / name).is_file(), f'Missing timing report: {name}'
    shutil.copytree(timing, out / 'timing', dirs_exist_ok=True)
    (out / 'REPORT.zh-CN.md').write_text('\n'.join(lines), encoding='utf-8')
    print(out / 'REPORT.zh-CN.md')


if __name__ == '__main__':
    main()
