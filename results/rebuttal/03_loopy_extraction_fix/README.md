# Loopy invariant-extraction correction

Local re-verification of cached initial and repair responses, not regeneration of adaptive repair trajectories. Corrected initial composition verifies 553/832; including existing repairs verifies 566/832. cached_usage.json attributes historical usage to accessed responses; re-verification made no new model calls. historical_api_usage/ contains all 832 original usage files, each checked against the previously recorded hash. See protocol_limitations.txt for the cached-replay interpretation.

Scripts are retained as run. They contain historical local paths; restore the evidence and adapt those path constants before reproduction. Archiving did not call a model or rerun training. For split evidence bundles, use `../restore_evidence.py` with the bundle index and an output directory.
