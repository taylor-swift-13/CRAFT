"""Export ongoing/final cached local verification; never write archived inputs."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import time


def write(path, text):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(text, encoding='utf-8')
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--runner-pid', type=int, required=True)
    args = parser.parse_args()
    allowed = Path('/mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20/outputs').resolve()
    if not args.out.resolve().is_relative_to(allowed):
        raise ValueError('Exports must stay in this task outputs directory')
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((args.run / 'manifest.json').read_text())
    write(args.out / 'protocol.json', json.dumps(manifest, indent=2) + '\n')
    write(args.out / 'verification_driver.py', (args.run / 'driver.py').read_text())
    last = None
    while True:
        rows = []
        for key in manifest['input_fingerprints']:
            path = args.run / 'tasks' / (key + '.json')
            error = args.run / 'errors' / (key + '.json')
            if path.exists():
                row = json.loads(path.read_text())
            elif error.exists():
                row = json.loads(error.read_text())
            else:
                suite, case_id = key.rsplit('_', 1)
                row = dict(suite=suite, case_id=case_id, status='pending')
            rows.append(row)
        groups = {}
        for suite in ['linear', 'NLA_lipus', 'Loopy', 'all']:
            group = [r for r in rows if suite == 'all' or r['suite'] == suite]
            complete = [r for r in group if r['status'] == 'complete']
            counts = dict(expected=len(group), completed=len(complete),
                          errors=sum(r['status'] == 'error' for r in group),
                          running=sum(r['status'] == 'running' for r in group),
                          pending=sum(r['status'] == 'pending' for r in group))
            for arm in ['native', 'fixed']:
                counts[arm] = sum(r['variants'][arm]['verified'] is True for r in complete)
                counts[arm + '_initial'] = sum(r['variants'][arm]['initial_verified'] is True for r in complete)
                counts[arm + '_initial_unknown'] = sum(r['variants'][arm]['initial_verified'] is None for r in complete)
            counts['gains'] = sum(r['variants']['native']['verified'] is False and r['variants']['fixed']['verified'] is True for r in complete)
            counts['losses'] = sum(r['variants']['native']['verified'] is True and r['variants']['fixed']['verified'] is False for r in complete)
            counts['native_archive_disagreements'] = sum(not r['native_archive_agreement'] for r in complete)
            groups[suite] = counts
        total = groups['all']
        done = total['completed'] == 832 and total['errors'] == 0
        alive = Path('/proc') .joinpath(str(args.runner_pid)).exists()
        state = 'complete' if done else ('running' if alive else 'stopped_incomplete')
        result = dict(state=state, updated_utc=datetime.now(timezone.utc).isoformat(),
                      scope=manifest['scope'], run=str(args.run), by_suite=groups,
                      protocol_fingerprint=manifest['protocol_fingerprint'],
                      extraction_counts=manifest['extraction_counts'])
        if done:
            changed = [p for p, expected in manifest['input_files'].items()
                       if hashlib.sha256(Path(p).read_bytes()).hexdigest() != expected]
            result['original_input_integrity'] = dict(checked=len(manifest['input_files']), changed=changed)
            if changed:
                result['state'] = 'invalid_input_changed'
        write(args.out / 'result.json', json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        buffer = io.StringIO()
        fields = ['suite', 'case_id', 'status', 'native_initial', 'fixed_initial', 'native', 'fixed',
                  'native_success_phase', 'fixed_success_phase', 'native_archive_agreement']
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            flat = {k: row.get(k, '') for k in ['suite', 'case_id', 'status', 'native_archive_agreement']}
            for arm in ['native', 'fixed']:
                variant = row.get('variants', {}).get(arm, {})
                flat[arm] = variant.get('verified', '')
                flat[arm + '_initial'] = variant.get('initial_verified', '')
                flat[arm + '_success_phase'] = variant.get('success_phase', '')
            writer.writerow(flat)
        write(args.out / 'per_program.csv', buffer.getvalue())
        lines = [f"状态：{result['state']}", f"更新（UTC）：{result['updated_utc']}",
                 f"已完成：{total['completed']}/832；运行中：{total['running']}；错误：{total['errors']}",
                 '结果定义：固定已缓存的 initial 和 repair 回答，配对重验原提取器与修复提取器。',
                 '未重新生成自适应 repair；原实验文件保留原样。', '']
        for suite, counts in groups.items():
            lines.append(f"{suite}: 完成 {counts['completed']}/{counts['expected']}; native={counts['native']}; fixed={counts['fixed']}; 新增={counts['gains']}; 损失={counts['losses']}")
        if not done:
            lines += ['', '上述成功数仅对应已完成任务，不能当成832程序的最终成功率。']
        else:
            lines += ['', f"全量 native={total['native']}/832 ({total['native']/832:.2%})；fixed={total['fixed']}/832 ({total['fixed']/832:.2%})。",
                      f"提升：{(total['fixed']-total['native'])/832*100:.2f} 个百分点。",
                      f"原输入完整性检查：{result['original_input_integrity']}"]
        write(args.out / 'report.txt', '\n'.join(lines) + '\n')
        signature = (state, total['completed'], total['errors'])
        if signature != last:
            print(json.dumps(dict(state=state, completed=total['completed'], errors=total['errors']), ensure_ascii=False), flush=True)
            last = signature
        if done or not alive:
            return
        time.sleep(30)


if __name__ == '__main__':
    main()
