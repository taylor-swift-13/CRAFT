# CRAFT visibility timing evidence

All timestamps below are UTC. This report reads existing records only; it performs no generation or verification.

Visible generation: 832/832 programs, 3,328 accepted batches (13,312 responses).
Generation calendar span: 2026-11-10T04:10:58.912177+00:00 to 2026-11-10T04:26:16.878131+00:00 = 15.30 minutes.
Successful API requests: 15,659.10 cumulative seconds (4.35 request-hours). Accepted batch spans, including retries within completed batches: 15,807.04 seconds. These are not concurrent wall times; 163 failed-request events have no individual duration.

| Arm | Prefix | Verified | Success | Local verification worker-hours | Mean seconds/program |
|---|---:|---:|---:|---:|---:|
| visible | 1 | 832/832 | 459 | 10.624 | 45.97 |
| visible | 4 | 832/832 | 585 | 22.913 | 99.14 |
| visible | 8 | 832/832 | 633 | 35.832 | 155.04 |
| visible | 16 | 832/832 | 672 | 47.047 | 203.57 |
| hidden (historical) | 1 | 832/832 | 386 | 6.681 | 28.91 |
| hidden (historical) | 4 | 832/832 | 513 | 17.433 | 75.43 |
| hidden (historical) | 8 | 832/832 | 555 | 25.952 | 112.29 |
| hidden (historical) | 16 | 832/832 | 602 | 43.005 | 186.08 |

Visible @16 local verification totals 47.05 worker-hours. The three additional prefix evaluations total 69.37 worker-hours. These jobs ran concurrently and these totals are not summed as wall time.

Actual prefix evaluator invocation span: 2026-11-10T06:20:11.922156+00:00 to 2026-11-10T09:16:14.437399+00:00 = 176.04 minutes. This includes the late-case helper and cache-only resume; overlapping intervals are counted once (176.04 minutes active union). The final exported 0.367669 seconds is cache/export time only.

| Visible end-to-end milestone | Elapsed minutes from first generation invocation |
|---|---:|
| Generation plus @16 proof | 133.43 |
| Generation plus all prefix proofs | 305.20 |
| Generation, all prefix proofs, semantic audit and export | 312.27 |

Recorded pipeline stages (not additive when they overlap other invocations):

| Stage | Start UTC | End UTC | Process wall minutes |
|---|---|---|---:|
| compose16 | 2026-11-10T04:17:53.282279+00:00 | 2026-11-10T06:24:32.936160+00:00 | 126.661 |
| prefixes | 2026-11-10T06:24:33.192476+00:00 | 2026-11-10T09:16:14.513043+00:00 | 171.688 |
| semantics | 2026-11-10T09:16:14.540541+00:00 | 2026-11-10T09:23:13.664794+00:00 | 6.985 |
| accounting | 2026-11-10T09:23:13.682174+00:00 | 2026-11-10T09:23:14.303952+00:00 | 0.010 |

Visible semantic audit complete: True; analysis 0.851 worker-hours, independent replay 0.098 worker-hours. Hidden control audit complete at snapshot: True.

Interpretation limits:

- Concurrent wall time is never the sum of request or worker elapsed seconds.
- The pipeline prefix stage waited for an existing prefix evaluator lock; its duration overlaps the primary invocation and is not additional proof work.
- The final prefix export wall_seconds is a cached resume/export and is not the original proof duration.
- Final per-case worker seconds cover the retained completed attempts. Durations of overwritten/interrupted attempts are not recoverable from these records.
- Successful API latency is measured separately only in the new visible run. Failed requests lack individual duration; accepted batch spans do not capture every abandoned attempt.
- Historical hidden calendar spans include recovery delays, imported pilot responses, and different concurrency. They are not a controlled wall-time speed comparison.
- Each prefix is evaluated locally from the same 16-response pool. @1 request latency cannot be obtained by dividing a four-response batch latency by four.
- Semantic audit cumulative seconds and wall times are separate from invariant generation and Frama-C verification. A partial hidden audit snapshot is explicitly marked.

Recompute with WSL Python:

```text
python3 -B /mnt/c/Users/50635/Documents/Codex/2026-11-06/rebuttal-x20/work/summarize_visibility_timing.py
```

Every consumed JSON input and its SHA-256 is recorded in result.json, together with invocation/resume times and per-prefix task timing boundaries.
