# Full-832 efficiency re-audit

| Method | Mean generation time | Mean API-reported tokens | Mean estimated tokens | Rows T/X/E/U/NC |
|---|---:|---:|---:|---:|
| AutoSpec | 54.7713 s / 832 rows | 25154.5385 tokens / 832 rows | -- | 832/832/0/0/0 |
| Clause2Inv | 29.0382 s / 366 rows | -- | 385.7978 tokens / 366 rows | 366/0/366/0/466 |
| SESpec | 117.3075 s / 832 rows | 44807.0470 tokens / 830 rows | -- | 832/830/0/2/0 |
| Naive | 28.8666 s / 832 rows | 4493.3714 tokens / 832 rows | -- | 832/832/0/0/0 |
| R1-NoH | 53.5198 s / 466 rows | 5849.6352 tokens / 466 rows | 630.1393 tokens / 366 rows | 466/466/366/0/0 |
| R1-H | 66.6021 s / 832 rows | 6928.2993 tokens / 832 rows | -- | 832/832/0/0/0 |
| R4-H | 293.7223 s / 466 rows | 29789.5708 tokens / 466 rows | 3114.0055 tokens / 366 rows | 466/466/366/0/0 |

T/X/E/U/NC means rows with per-task time, exact tokens, estimated tokens, unavailable tokens, and no model call. Averages use the row count printed with the value; API-reported and estimated tokens are never combined.

Active wall time is the sum of reconstructed generation sessions, not the sum of parallel task durations. A new session is inferred after 300 seconds with no active task. Reused Core-366 batches use their saved launch/completion timestamps. Clause2Inv uses its runner/results file timestamps.

Mean generation time is the arithmetic mean of per-task `generation_seconds`. It is unavailable for the reused Core-366 R1-NoH and R4-H rows, and excludes Clause2Inv inputs on which the tool was not called. Full-precision values and active batch wall time remain available in the CSV evidence files.
