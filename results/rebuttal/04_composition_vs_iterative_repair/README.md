# Controlled composition versus iterative repair

466 Loopy programs, GPT-5-nano/minimal, hidden/visible targets, and 1/4/8 calls. Both methods share the initial response within each visibility setting. Composition pools independent responses; repair revises sequentially and applies Houdini to the final candidate. Calls are matched; tokens/time are measured. Final-output and any-checkpoint results are kept separate. The bundle contains model replies, usage, feedback, scores, verification logs, and sources.

Scripts are retained as run. They contain historical local paths; restore the evidence and adapt those path constants before reproduction. Archiving did not call a model or rerun training. For split evidence bundles, use `../restore_evidence.py` with the bundle index and an output directory.
