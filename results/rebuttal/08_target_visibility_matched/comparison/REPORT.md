# Matched target-hidden / target-visible CRAFT comparison

Status: **complete**. Expected: 832 programs per arm and prefix.

| Arm | k | Verification complete / 832 | Verified | Certified strict / verified | Certified exact / verified | Audit / replay complete | Mean tokens (n) |
|---|---:|---:|---:|---:|---:|---:|---:|
| hidden | 1 | 832/832 | 386 | 224/386 (58.03%) | 175/386 (45.34%) | 832/832 | 1267.8 est. (832) |
| hidden | 4 | 832/832 | 513 | 318/513 (61.99%) | 243/513 (47.37%) | 832/832 | 1703.4 (832) |
| hidden | 8 | 832/832 | 555 | 352/555 (63.42%) | 263/555 (47.39%) | 832/832 | 3421.5 (832) |
| hidden | 16 | 832/832 | 602 | 395/602 (65.61%) | 301/602 (50.00%) | 832/832 | 6840.2 (832) |
| visible | 1 | 832/832 | 459 | 260/459 (56.64%) | 178/459 (38.78%) | 832/832 | 1298.1 est. (832) |
| visible | 4 | 832/832 | 585 | 370/585 (63.25%) | 238/585 (40.68%) | 832/832 | 1753.5 (832) |
| visible | 8 | 832/832 | 633 | 409/633 (64.61%) | 261/633 (41.23%) | 832/832 | 3494.2 (832) |
| visible | 16 | 832/832 | 672 | 444/672 (66.07%) | 288/672 (42.86%) | 832/832 | 6972.5 (832) |

Arm-specific verified subsets are descriptive, not paired. The following comparisons use identical keys in both arms.

| k | Paired metric | Common programs | Hidden | Visible | Visible − hidden (pp) |
|---:|---|---:|---:|---:|---:|
| 1 | verification | 832 | 386 | 459 | +8.77 |
| 1 | verified_and_strict | 832 | 224 | 260 | +4.33 |
| 1 | verified_and_exact | 832 | 175 | 178 | +0.36 |
| 1 | strict_on_common_verified | 335 | 181 | 181 | +0.00 |
| 1 | exact_on_common_verified | 335 | 158 | 153 | -1.49 |
| 4 | verification | 832 | 513 | 585 | +8.65 |
| 4 | verified_and_strict | 832 | 318 | 370 | +6.25 |
| 4 | verified_and_exact | 832 | 243 | 238 | -0.60 |
| 4 | strict_on_common_verified | 476 | 285 | 298 | +2.73 |
| 4 | exact_on_common_verified | 476 | 228 | 224 | -0.84 |
| 8 | verification | 832 | 555 | 633 | +9.38 |
| 8 | verified_and_strict | 832 | 352 | 409 | +6.85 |
| 8 | verified_and_exact | 832 | 263 | 261 | -0.24 |
| 8 | strict_on_common_verified | 527 | 327 | 335 | +1.52 |
| 8 | exact_on_common_verified | 527 | 251 | 250 | -0.19 |
| 16 | verification | 832 | 602 | 672 | +8.41 |
| 16 | verified_and_strict | 832 | 395 | 444 | +5.89 |
| 16 | verified_and_exact | 832 | 301 | 288 | -1.56 |
| 16 | strict_on_common_verified | 578 | 374 | 380 | +1.04 |
| 16 | exact_on_common_verified | 578 | 290 | 279 | -1.90 |

Verification uses the common completed set. Joint verified-and-property metrics use the common audit/replay-completed set; property-only metrics use the common verified-and-audited set. Missing programs and unresolved proofs are not silently treated as negative semantic findings.

Generation settings, source/template/code hashes, actual saved messages, response order, and proof provenance are checked. The only intended algorithmic difference is target visibility in generation; filtering remains target-free. Independent service executions remain stochastic.

The replay gate requires the same semantic engine, audit driver, 15-second exactness search budget, and 8-second replay timeout. No longer-timeout or explicit-predecessor addenda from the previous audit are substituted.

Costs use accepted batch usage; @1 is estimated as full input plus output/4. Prefix costs overlap and must not be summed. See result.json and per_program.csv for missing usage and partial results.
