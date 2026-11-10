# Historical and common-protocol hidden @16 semantic audit

This note explains the certified-count changes for the **same 602 verified hidden CRAFT@16 programs** using saved records only. No generation, verification, SMT search, or replay was rerun. Original experiment and audit files were not changed.

| Audit | Certified strict | Certified exact |
|---|---:|---:|
| Historical audit, including explicit-predecessor addenda | 396/602 (65.78%) | 304/602 (50.50%) |
| Current common protocol used for both visibility arms | 395/602 (65.61%) | 301/602 (50.00%) |

The current hidden-visible comparison uses a common semantic engine, a 15-second exactness search budget, and an 8-second timeout per replay obligation. Historical explicit-predecessor addenda are not substituted into the new comparison.

## Exactness: five losses and two gains

The change is **304 - 5 + 2 = 301**, not merely removal of the two historical explicit predecessor certificates.

| Program | Historical result | Current result | Relevant query field |
|---|---|---|---|
| Loopy_129 | Certified exact | unknown_reachability | exact.attempts: variable=b, direction=1, status=unknown, reason=timeout |
| Loopy_397 | Certified exact | unknown_reachability | exact.attempts: variable=c and variable=i, direction=1, status=unknown, reason=timeout |
| Loopy_50 | Certified exact, with historical explicit-predecessor replay addendum | unknown_reachability | exact.attempts: variable=y, direction=1, status=unknown, reason=timeout |
| NLA_lipus_29 | Certified exact | unknown_reachability | All eight exact.attempts for n/p/w/z and both directions: status=unknown, reason=timeout |
| linear_296 | Certified exact, with historical explicit-predecessor replay addendum | unknown_reachability | exact.attempts: variable=x, direction=1, status=unknown, reason=timeout |
| Loopy_338 | unknown_reachability; y/direction=1 timed out | Certified exact | exact.rank_variable=y, rank_direction=1; required obligations replay successfully, properties.exact=true |
| NLA_lipus_28 | unknown_reachability; w/direction=1 timed out | Certified exact | exact.rank_variable=w, rank_direction=1; required obligations replay successfully, properties.exact=true |

All five current losses are **analysis-stage unknowns**, not positive exactness results rejected during the current replay. Initialization and preservation remain unsat. The two gains were unresolved under the old bounded search and are now confirmed by search and replay.

The historical explicit predecessors were:

- Loopy_50: x_previous = x-y+1; y_previous = y-1.
- linear_296: x_previous = x-1; y_previous = If(x <= 500000, y-1, y+1).

The historical artifacts remain preserved; neither addendum is used in the current matched comparison.

## Strictness: one analysis-stage unknown

The only change from **396 to 395** is Loopy_50. Its current strength.I_not_target.status is unsat, but strength.target_not_I.status is unknown, with the saved reason:

> smt tactic failed to show goal to be sat/unsat (incomplete (theory arithmetic))

The converse counterexample required for strictness was not established. The current strength.classification is unknown_implication. This is an arithmetic incompleteness result, **not a reported timeout**.

## Record locations

Historical root:

`C:/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20/outputs/craft16_invariant_audit_20261109`

Current hidden root:

`/home/yangfp/craft_empirical_runs/craft16_hidden_prefix_semantics_control_20261110`

For each of Loopy_129, Loopy_397, Loopy_50, NLA_lipus_29, linear_296, Loopy_338, and NLA_lipus_28:

- Old case: `{historical root}/cases/{key}.json`.
- New case: `{current hidden root}/k16/cases/{key}.json`.
- New replay: `{current hidden root}/k16/replay/{key}.json`.
- New saved obligations: `{current hidden root}/k16/proof_queries/{key}.json`.

Historical counts: `{historical root}/final_summary.json`.
Historical addenda: `{historical root}/explicit_predecessor_certificates.json` and `proof_queries/{key}.explicit_predecessor.smt2`.
Current counts: `{current hidden root}/summary.json`.
Final matched comparison: `outputs/craft_visibility_comparison_20261110/result.json`.

## Interpretation

Finite proof budgets can produce different certified counts on the same frozen invariants: some former successes become unresolved while some former timeouts become certified. Unknown does not show that a property is false or invalidate a historical certificate. The new matched comparison must use its common-protocol **395 strict / 301 exact** hidden baseline, rather than combining historical 396/304 counts with the newly audited visible arm.