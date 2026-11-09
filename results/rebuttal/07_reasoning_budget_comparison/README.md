# Reasoning-budget accounting

Offline accounting of the historical medium pool and comparison with the new minimal pool. All evaluations cover 832 programs. Medium compose@1/@4/@8: 483/640/669 verified. Tokens include input and output for every call; cumulative API time excludes verifier time and is not parallel wall-clock latency. minimal@16 versus medium@1 is a supplementary cross-batch, cross-prompt comparison, not an isolated reasoning ablation. Historical event/API records and grid results are copied here for auditability; no new generation was performed.

Scripts are retained as run. They contain historical local paths; restore the evidence and adapt those path constants before reproduction. Archiving did not call a model or rerun training. For split evidence bundles, use `../restore_evidence.py` with the bundle index and an output directory.
