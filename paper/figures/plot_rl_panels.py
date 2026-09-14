#!/usr/bin/env python3
"""Generate the RQ3 four-stage Qwen3-8B figure from the current appendix data.

Refresh data with paper/scripts/prepare_experiment_results.py first.
The shared implementation also supports the independent Bare-model probe.
"""
import json
from pathlib import Path

from plot_qwen_probes import training_stages


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    data = json.loads((root / "artifacts/experiment_results_current.json").read_text())
    training_stages(data)
