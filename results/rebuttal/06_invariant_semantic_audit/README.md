# Invariant semantic audit

Frozen CRAFT@16 invariants, analyzed under explicitly modeled defined 32-bit scalar-integer semantics. Rebuttal statistics use only the 602 originally verified programs: 396 strictly stronger than the target obligation and 304 reachability-exact. Complete 832-program audit records are preserved for provenance. Saved SMT obligations were rechecked by a separate script using Z3; this is not an independent C semantic translation or proof-kernel certification. original_verification_diagnostics/ explains the additional seven strict/eight exact cases outside the original-success subset, without changing original outcomes.

Scripts are retained as run. They contain historical local paths; restore the evidence and adapt those path constants before reproduction. Archiving did not call a model or rerun training. For split evidence bundles, use `../restore_evidence.py` with the bundle index and an output directory.
