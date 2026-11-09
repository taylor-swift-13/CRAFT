Loopy empirical study: independent composition vs continuous refinement
====================================================================

Population: the 466 Loopy C programs in the frozen CRAFT benchmark.
The original benchmark, historical results, prompts, and repository are read-only.
New WSL run: /home/yangfp/craft_empirical_runs/loopy_compose_refine_20261107
Driver: work/loopy_empirical.py (also snapshotted as driver.py in the run).

DESIGN
Four groups = {targets hidden, targets visible} x {compose, refine}.
Each setting uses the same naive prompt and the same initial sampled response.
Each model request returns exactly one answer. All trajectories have 8 nominal
generation calls, irrespective of intermediate target success. Checkpoints are
1, 4, and 8 calls, using prefixes of the same trajectories.

Compose: n independent responses to the naive prompt -> union of parsed clauses
-> shared target-free syntax reducer and Houdini -> judge original targets.
Refine: one initial response -> n-1 revisions using raw verifier feedback on
the immediately preceding complete candidate -> Houdini on candidate n only
-> judge original targets. Earlier candidates are not automatically unioned.

Houdini/checkpoint scores never enter refinement prompts or stopping decisions.
The raw feedback includes syntax and WP stdout/stderr, preserving unproved and
timed-out results rather than describing them as concrete counterexamples.
Visible feedback is computed from the original program, including its goals.
Hidden feedback is computed exclusively from the target-stripped program; an
audit rejects any benchmark target proof obligation in hidden WP feedback.
The original goals are restored for final scoring in both visibility settings.
No model-produced program bodies or target edits are applied; only parsed loop
invariant expressions are inserted into the frozen program.

BACKEND
GPT-5-nano, reasoning_effort=minimal, Openlux, n=1, max_completion_tokens=8192.
No temperature/top_p. At most 20 invariant lines are parsed per response.
Frama-C 27.1; Alt-Ergo and Z3; 5 seconds per goal; WP parallelism 2.
The same pre-Frama syntax reduction/Houdini implementation serves all groups.
An empty candidate inserts no empty ACSL block, and original targets are still
checked. Final success requires syntax, all retained invariant obligations,
and all benchmark target obligations to be proved by the common verifier.

COST ACCOUNTING
One shared first answer per program/visibility gives a physical maximum of
466 * 2 * (8 independent + 7 additional refinement) = 13,980 successful requests.
Nominal per-method cost still includes its initial call. Never sum all four
group totals as actual spending, since the initial response is shared. Likewise,
do not sum n=1/4/8 costs as independent runs: they overlap in their prefixes.
Transport retries are recorded separately from the nominal model-call budget.

For a method at budget n:
  API tokens/time = first n calls of its trajectory (including shared initial).
  Refine feedback cost = raw verification of candidates 1 through n-1.
  Final proof cost = only the designated checkpoint's Houdini and target judge.
Earlier offline checkpoint evaluations are excluded from that method's n-cost.
Physical experiment totals count every actually executed feedback/score task,
deduplicate shared first calls and shared n=1 scores, and include all checkpoints.
CPU seconds include the worker and waited-for child/prover processes.
Service wall seconds measure actual API/proof work under this run's concurrency.
Serial service time is their sum for one program, not whole-run wall time.
Whole-run elapsed time is measured separately; parallel task times do not add up
to the whole-run elapsed wall time. Queue time can be recovered from timestamps.

Money fields are explicit public price scenarios, NOT account billing receipts.
The OpenAI-reference scenario separates cached input tokens. The Openlux public
lowest-group scenario uses its published ordinary-input and output rates and
does not assume an unverified cached-input discount. The actual account group
rate may differ. Failed-request billing and missing provider usage are unknown.
Reasoning tokens, when reported, are already included in completion tokens and
are NOT charged or added a second time.

MISSING DATA
Persistent API errors and verifier infrastructure errors are unresolved, not
verification failures. Results show scored counts, successful counts, conditional
rates, and full-population bounds. A full-population point success rate is emitted
only if all 466 programs are scored for that group/checkpoint. Do not compare
conditional rates on different available subsets as full-benchmark rates.
Paired comparisons use programs scored by both methods. At n=1 the methods share
the exact same answer and score, so their paired results must be identical.

OUTPUT FILES
protocol.json: frozen prompts, generation/proof settings, fingerprints and hashes.
naive_prompt.txt / refine_prompt.txt: actual templates used.
selfcheck.json: local positive/negative proof and hidden-feedback checks.
result.json: current/final grouped metrics, paired case IDs, physical totals.
summary.csv: 12 rows = four groups x three budgets, plus cost/time means.
per_program.csv: 5,592 rows = 466 programs x four groups x three budgets.
pricing_reference.json: source URLs and units for money estimates.
input_integrity.json: original input and verification-source hash audit.
evidence.tar.gz: generated at the end; raw requests/responses, feedback, proof
logs (including exact verifier inputs), snapshots, and experiment lifecycle.
evidence.sha256.json: archive digest and size.

The initial pilot uses two program IDs, with both visibility settings, and its
paid calls and verification records are reused by the full run without sampling
or selecting pilot programs based on performance.
