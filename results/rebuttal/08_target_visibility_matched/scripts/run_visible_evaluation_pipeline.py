"""Local-only supervised evaluation after the target-visible generation pilot."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
RUN = Path('/home/yangfp/craft_empirical_runs/craft16_target_visible_20261110')
AUDIT = Path('/home/yangfp/craft_empirical_runs/craft16_target_visible_semantics_20261110')
EXPORT = HERE.parent / 'outputs' / 'craft16_target_visible_20261110'


def now():
    return datetime.now(timezone.utc).isoformat()


def save(data):
    target = EXPORT / 'pipeline_runtime.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.replace(target)


def main():
    state = dict(pid=os.getpid(), started_utc=now(), stages=[], state='waiting_for_pilot_verification')
    save(state)
    # The pilot remains in its existing process; this stage excludes those keys.
    commands = [
        ('compose16', [str(HERE/'verify_visible_nonpilot.py'), 'verify', '--workers', '24', '--watch']),
        ('prefixes', [str(HERE/'craft_visible_prefixes.py'), '--workers', '24', '--allow-partial']),
        ('semantics', [str(HERE/'audit_visible_prefix_semantics.py'), '--out', str(AUDIT), '--jobs', '8', '--replay-jobs', '12', '--allow-partial']),
        ('accounting', [str(HERE/'export_craft_visible.py')]),
    ]
    for label, arguments in commands:
        if label == 'prefixes':
            while any(json.loads(p.read_text()).get('status') == 'running' for p in (RUN/'verification').glob('*.json')):
                time.sleep(5)
        stage = dict(name=label, started_utc=now(), argv=[sys.executable, '-u', '-B', *arguments])
        state['stages'].append(stage); state['state']=label; save(state)
        print(json.dumps(dict(stage=label, started_utc=stage['started_utc'])), flush=True)
        start = time.monotonic()
        with (RUN / (label + '.pipeline.log')).open('a') as log:
            process = subprocess.Popen(stage['argv'], stdout=log, stderr=subprocess.STDOUT)
            stage['pid'] = process.pid; save(state)
            stage['returncode'] = process.wait()
        stage.update(finished_utc=now(), wall_seconds=time.monotonic()-start)
        save(state)
        print(json.dumps(stage), flush=True)
        if stage['returncode']:
            state['state']='stage_error'; save(state); return stage['returncode']
    state.update(state='available_results_evaluated', finished_utc=now()); save(state)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
