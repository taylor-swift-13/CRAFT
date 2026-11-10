# Matched target-visibility comparison

GPT-5-nano/minimal, the same 832 source programs, four n=4 requests per program,
and compose@1/@4/@8/@16 from fixed response prefixes. Generation sees the original
targets in the new visible arm; clause filtering stays target-free in both arms,
and the original targets are restored for final scoring. No adaptive repair is used.

`comparison/` contains final tables, paired cases, costs, and input provenance.
`completion_gate.json` records full verification/audit/replay completion for both
arms and all four prefixes. Semantic unknowns remain unknown and are not certified.
Both arms use the same semantic engine and budgets; the historical exact count in
sibling 06 is not silently substituted for the common-engine hidden control.

`evidence/` preserves generation, original/hidden sources, verifier records/logs,
manifests, invocations/resume histories, audit cases, saved SMT queries, and replay
records. Scratch and Python cache directories are excluded. Bundles use the same
24 MiB part/index format as the other rebuttal archives. Restore with the parent
`restore_evidence.py`; every index includes whole-archive, part, and member hashes.
Historical hidden model replies and prefix proofs are already archived in sibling
01/02. Scripts retain historical paths and need path adaptation after restoration.

@1 usage is an estimate (full first-batch input plus one quarter of its output);
@4/@8/@16 use recorded complete-batch usage. Prefix costs overlap and must not be
summed. Reasoning tokens are included in output tokens. Failed-request billing may
be unavailable. Archiving performs no API calls, training, proof search, or Git writes.
