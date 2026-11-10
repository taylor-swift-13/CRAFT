"""Wait for local visible evaluation, audit the frozen control, then compare."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent
WORK=HERE.parent
OUT=WORK/'outputs/craft_visibility_orchestration_20261110'
PIPE=WORK/'outputs/craft16_target_visible_20261110/pipeline_runtime.json'


def now(): return datetime.now(timezone.utc).isoformat()


def save(state):
    tmp=OUT/'runtime.tmp'
    tmp.write_text(json.dumps(state,indent=2)+'\n')
    tmp.replace(OUT/'runtime.json')


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    state=dict(pid=os.getpid(),started_utc=now(),state='waiting_for_visible_pipeline',stages=[])
    save(state)
    while True:
        prior=json.loads(PIPE.read_text())
        if prior['state']=='available_results_evaluated': break
        if prior['state']=='stage_error':
            state.update(state='upstream_error',upstream=prior);save(state);return 1
        time.sleep(10)
    commands=[
        ('hidden_semantics',[str(HERE/'audit_visible_prefix_semantics.py'),
            '--run','/tmp/craft16_minimal_20261107_v2',
            '--prefix-run','/home/yangfp/craft_empirical_runs/craft16_prefix_trend_20261108',
            '--out','/home/yangfp/craft_empirical_runs/craft16_hidden_prefix_semantics_control_20261110',
            '--jobs','8','--replay-jobs','12']),
        ('comparison',[str(HERE/'compare_visible_hidden.py'),'--require-complete']),
    ]
    for label,args in commands:
        stage=dict(name=label,started_utc=now(),argv=[sys.executable,'-u','-B',*args])
        state['stages'].append(stage);state['state']=label;save(state)
        print(json.dumps(dict(stage=label,started_utc=stage['started_utc'])),flush=True)
        start=time.monotonic()
        with (OUT/(label+'.log')).open('a') as log:
            process=subprocess.Popen(stage['argv'],stdout=log,stderr=subprocess.STDOUT)
            stage['pid']=process.pid;save(state)
            stage['returncode']=process.wait()
        stage.update(finished_utc=now(),wall_seconds=time.monotonic()-start);save(state)
        print(json.dumps(stage),flush=True)
        if stage['returncode']:
            state['state']='stage_error';save(state);return stage['returncode']
    state.update(state='complete',finished_utc=now());save(state)
    return 0


if __name__=='__main__':raise SystemExit(main())
