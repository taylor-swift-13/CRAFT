# loopGym Patches

## 2026-07-23: ExampleSampler — relax negative generation for unknown() programs

**File**: `rl_pipeline/sampler/example_sampler.py`

### 3a. Body-only taint: allow perturbation when body transition is deterministic

**Lines ~135-177** (`_nondet_tainted`): Rewrote taint tracking to only mark variables whose **loop-body transition** depends on unknown(), not those whose taint comes purely from pre-loop initialization. A variable `x` with `x = unknown()` before the loop but `x = x + 1` in the body has a deterministic transition — perturbing observed values of `x` is safe.

```diff
-        # Taint = assigned from unknown() (incl. pre-loop entry values),
-        # assigned under a nondeterministic condition, propagated to fixpoint.
-        tainted: Set[str] = set()
-        for name, expr in prog.local_inits:
-            if expr and re.search(r"\bunknown\w*\s*\(", expr):
-                tainted.add(name)
-        # ... propagate through init expressions ...
+        # Body-taint only: variables assigned from unknown() INSIDE the loop,
+        # or under a nondeterministic condition in the body.
+        body_tainted: Set[str] = set()
+        for m in re.finditer(r"\b(\w+)\s*=[^=][^;]*\bunknown\w*\s*\(", body):
+            body_tainted.add(m.group(1))
+        # ... propagate only within body ...
```

### 3b. Relax uncontrolled check: nondet only blocks when no movable vars remain

**Line ~411**: Changed `uncontrolled` condition so nondet guard/body alone doesn't kill all negatives — only when it taints ALL movable variables.

```diff
-        uncontrolled = (
-            nondet or nondet_body or bool(untracked) or body_call or unsupported_state
-        )
+        uncontrolled = (
+            bool(untracked) or body_call or unsupported_state
+            or ((nondet or nondet_body) and not movable)
+        )
```

### 3c. Exclude `unknown` from `_body_calls_function`

**Lines ~280-286**: `unknown()` and `nondet()` are nondeterministic oracles (already handled by taint), not opaque side-effecting functions. Excluding them from the "body calls function" check allows perturbation when the only "function call" in the body is `unknown()`.

```diff
-        calls = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", body))
-        return bool(calls - {"if", "while", "for", "switch", "sizeof"})
+        calls = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", body))
+        return bool(calls - {
+            "if", "while", "for", "switch", "sizeof",
+            "unknown", "unknown1", "unknown2", "unknown3",
+            "nondet", "__VERIFIER_nondet_int", "__VERIFIER_nondet_uint",
+        })
```

### 3d. Determinize sampling: replace unknown() with params at execution time

**Lines ~468-510** (new `_determinize_source` method): Before executing the program for trace collection, all `unknown()` / `unknownN()` calls are replaced with fresh `int` parameters (`_nd0`, `_nd1`, ...) added to the function signature. This makes the execution fully deterministic (given sampled inputs), enabling exhaustive state enumeration and safe negative generation.

The replacement is **sampling-only** — the LLM prompt and Houdini verification still use the original source with `unknown()`.

```python
def _determinize_source(source: str) -> str:
    # "int x = unknown();" → "int x = _nd0;" + add "int _nd0" to params
    new_source = re.sub(r"\bunknown\w*\s*\(\s*\)", _replace, source)
    # Add _nd0, _nd1, ... to function signature
    ...
```

**Effect**: Programs with synthetic negatives increased from 33% → 78% of training data.

---

## 2026-07-22: GRPO reward service integration — prover fix + fallback reward

### 2a. Fix prover: z3 → alt-ergo

**File**: `src/output_verify.py` line 167

```diff
-            "-wp-prover", "z3", "-wp-model", "Typed",
+            "-wp-prover", "alt-ergo", "-wp-model", "Typed",
```

**Reason**: z3 is not installed on the training cluster; alt-ergo (v2.6.3) is available at `/home/yangfanpeng.yfp/.opam/sam2inv/bin/alt-ergo`. Without this fix, all Houdini filter calls fail with "Prover 'z3' not found in why3.conf".

### 2b. Fallback reward when n_neg=0

**File**: `rl_pipeline/reward/reward_calculator.py` lines 228-231 and 210-213

When the ExampleSampler produces zero synthetic negatives (nondeterministic programs with `unknown()`, body function calls, etc.), all rewards would be 0 regardless of invariant quality. Added a fallback using Houdini survival fraction:

```diff
 # Per-rollout reward (line ~228):
-            reward = self.w_base * base + self.w_marg * marginal
+            if n_neg:
+                reward = self.w_base * base + self.w_marg * marginal
+            else:
+                # Fallback: survival fraction when no negatives available.
+                # Measures invariant precision (inductive ones / proposed ones).
+                reward = (len(surv) / len(invs)) if invs else 0.0

 # Batch score (line ~210):
-        batch_score = (len(union_rej) / n_neg) if n_neg else 0.0
+        if n_neg:
+            batch_score = len(union_rej) / n_neg
+        else:
+            # Fallback: fraction of union invariants surviving Houdini
+            batch_score = (len(union_surv) / len(union)) if union else 0.0
```

**Reason**: ~65% of RL training samples produce 0 negatives (due to `unknown()` in loop body/guard, function calls, etc.). Without this fallback, those samples give zero gradient signal in GRPO. The survival fraction is bounded [0,1] and distinguishes inductive invariants from non-inductive ones.

**Coverage**: Effective reward signal coverage improved from 13% → ~69% of training samples.

---

## 2026-07-20: Fix terminates goal causing false verification failures

**File**: `src/output_verify.py` line 166

**Problem**: Frama-C 32.1 (Germanium) generates `terminates` proof obligations by default. These goals often timeout (especially for loops without explicit `decreases` clauses), and OutputVerifier counted them as assertion failures in `verify_result`. This caused programs with correct invariants and proven postconditions to report `verified=False`.

**Symptom**: Near-zero verification rates (~3%) despite models producing correct inductive invariants that Houdini validates. Manual `frama-c -wp` showed 11/12 goals proved (only `terminates` timed out).

**Root cause**: `filter_goal_assertion()` returns all non-invariant WP goals, including termination goals. `check_verify_target()` then marks the timeout as a False entry in `verify_result`.

**Fix**: Add `-wp-prop=-@terminates` to the frama-c command to exclude termination proof obligations (we only need partial correctness — invariant inductiveness + postcondition implication).

```diff
-        command = [
-            "frama-c", "-wp", "-wp-print", "-wp-timeout", "30", "-wp-par", "8",
-            "-wp-prover", "z3", "-wp-model", "Typed", file_path,
-        ]
+        command = [
+            "frama-c", "-wp", "-wp-print", "-wp-timeout", "30", "-wp-par", "8",
+            "-wp-prover", "z3", "-wp-model", "Typed",
+            "-wp-prop=-@terminates", file_path,
+        ]
```

**Verification**:
```
Before fix: verify_result = [False, True, True]  (terminates timeout counted)
After fix:  verify_result = [True, True]          (only assertion goals)
```

## 2026-07-20: Houdini 每轮删除所有失败不变式

**File**: `src/houdini_pruner.py` line 88, line 24-39

**设计**: Houdini 算法每轮调用 Frama-C/WP 验证当前所有候选不变式，然后**一次性删除本轮所有验证失败的不变式**，保留通过的。迭代直到全部通过或无不变式剩余。

**关键实现**:

```python
# houdini_pruner.py:88 — 每轮用 prune_annotations 删除所有 False 项
next_code = self.prune_annotations(results, current_code)

# houdini_pruner.py:24-39 — prune_annotations 通过 re.sub 遍历所有不变式，
# 对每个不变式检查对应的 validate_result，False 则替换为空串（删除）
def prune_annotations(self, validate_result, annotations):
    matches = list(_INVARIANT_RE.finditer(annotations))
    result_iter = iter(validate_result)
    return _INVARIANT_RE.sub(
        lambda match: match.group(0) if next(result_iter) else "",
        annotations,
    )
```

**行为**: 典型场景中 100+ 候选不变式，每轮通常只有 1-2 个失败（因为不变式之间有归纳依赖），因此需要 30-100 轮迭代。但如果有多个独立失败的不变式，也会在同一轮中全部删除。

## 2026-07-20: vLLM v1 引擎多线程安全修复

**File**: `rl_pipeline/inference/inference.py` line 100-108, line 127

**Problem**: vLLM 0.11.1rc2 的 v1 引擎使用 ZMQ socket 与 EngineCore 子进程通信。`run_concurrent.py` 的 `ThreadPoolExecutor` 多线程并发调用 `LLM.chat()` 时，多个线程同时写入同一个 ZMQ socket 导致消息损坏。

**Symptom**: `ValueError: b'\x00\x00' is not a valid EngineCoreRequestType` — 引擎初始化成功但第一次收到请求时崩溃。

**Fix**: 在 `VLLMRolloutProvider` 中加 `threading.Lock()`，序列化 LLM 调用。Houdini/Frama-C 验证（CPU 密集）仍然并行。

```diff
 class VLLMRolloutProvider:
     def __init__(self, ...):
+        import threading
+        self._lock = threading.Lock()
         ...

     def _chat_n(self, prompt, n):
-        outs = self.llm.chat(messages, sp, use_tqdm=False)
+        with self._lock:
+            outs = self.llm.chat(messages, sp, use_tqdm=False)
```

## 2026-07-26: Reward 公式改造 — 去掉 marginal + precision 乘子

**动机**: v6 RL 训练后端到端验证率反而下降 (77.4% → 74.4%)。分析发现:
1. `marginal` 依赖组内其他 rollout,方差大且惩罚"和别人想到一起"的独立好 rollout
2. 当前 reward 不区分"少而精"和"多而杂"—— 生成 15 条精准的 vs 50 条含冗余的可能得同样的 base

**修改 1**: 关掉 marginal 权重

**File**: `verl/verl/experimental/reward_loop/reward_manager/loopgym_service.py:132-137`

```diff
 payload = {
     "program": program,
     "rollouts": rollouts,
-    "w_base": 0.5,
-    "w_marg": 0.5,
+    "w_base": 1.0,
+    "w_marg": 0.0,
 }
```

**修改 2**: reward 乘上 precision = essential_survivors / generated_invariants

**File**: `rl_pipeline/reward/reward_calculator.py`

- 加 `_essential_count` staticmethod (顺序贪心覆盖: 只有能扩展累积 reject 集的 survivor 才算 essential;等价对里第一条算 essential,后续算 trivial)
- `_compute_one` 主循环里改 reward:
  ```python
  raw_reward = self.w_base * base + self.w_marg * marginal
  n_gen = len(roll_invs_raw[idx])          # 模型实际生成的条数
  n_ess = self._essential_count(surv, negatives, groups)
  precision = (n_ess / n_gen) if n_gen else 0.0
  reward = raw_reward * precision
  ```
- 分母用 `roll_invs_raw[idx]`(过滤前的原始生成条数)而非 `invs`(过 tautology filter 后),这样**永真式和被 Houdini 淘汰的错误不变式也计入分母**,即"废输出稀释 reward"
- `RolloutScore` 加 `precision` 字段, `BatchReward.to_dict` 输出 precision 供观测

**效果**:
- 生成 5 条精准 (5/5 essential) → precision=1.0, reward=base
- 生成 5 条精准 + 15 条 trivial (5/20 essential) → precision=0.25, reward=0.25*base
- 惩罚灌水,鼓励最小归纳集

**边界处理**: 无负例时 (n_neg=0) precision=1.0, 走原 fallback (survival 比例),不受影响。

## 2026-07-26: v8 — 用 essential-count bonus 替代 precision multiplier

**动机**: v7 训练观测到 precision 乘子诱导模型走极端保守 (response_len 40 → 20, 平均生成 2-3 条不变式)。原因: `precision = n_ess/n_gen` 对"少而精"给满分,对"多而精"打折 → 模型学会只生成 3 条稳定不变式。

Reward 卡在 0.25-0.33 不涨,step 50+ 无进展。

**修改**: `reward_calculator.py`, `_compute_one` 主循环

```diff
-reward = raw_reward * precision   # 乘法压制多输出
+target_ess = 8.0
+essential_bonus = min(n_ess / target_ess, 1.0)
+reward = raw_reward + 0.3 * essential_bonus   # 加法奖励绝对数量
```

**行为**:
- 空输出: base=0, bonus=0 → reward=0
- 3 essential: bonus=0.375 → +0.11
- 8 essential (target): bonus=1.0 → +0.30
- 20 essential: bonus 依然 1.0 (不无限奖励)

precision 字段保留在 RolloutScore 里,仅供观测。

**保持不变**: essential_count 算法 (顺序贪心)、w_base=1.0、w_marg=0.0。

## 2026-07-31: Reward v3 — essential-survivor bonus

The default reward combines standalone coverage, hard-negative credit, and
clause-level useful coverage. Cross-rollout marginal reward has been removed
from the implementation and public interfaces.

```text
raw_reward = 1.0 * base
           + 0.3 * hard_bonus
           + 0.1 * essential_survivors
reward = raw_reward
       - 0.02 * redundant_clauses
       - 0.05 * overflow
```

`essential_survivors` is computed greedily in model-output order. A
Houdini-surviving clause is essential exactly when adding it increases the
fraction of rejected negative groups. A surviving clause with zero incremental
coverage is redundant. Non-survivors count as neither essential nor redundant.
Zero-negative programs retain the binary Frama-C/WP fallback.

## 2026-08-07: Reward v4 — coverage-game Shapley credit

The group term now allocates standalone negative-set union coverage with the
closed-form Shapley value. If negative trace `n` is rejected by `f(n)`
rollouts, each rejecting rollout receives `1/f(n)` credit for that trace.
Therefore the rollout credits sum exactly to the standalone union coverage.

```text
shapley_credit[i] = sum(1 / f(n) for n in R[i]) / len(N)
reward[i] = 1.0 * base[i]
          + 0.3 * shapley_credit[i]
          - 0.02 * redundant_clauses[i]
          - 0.05 * overflow[i]
```

The calculation reuses rejection frequencies already required by the old
rarity bonus and adds no Houdini or verifier calls. The public response now
exposes `shapley_credit`; `hard_bonus` and `w_hard` remain compatibility
aliases.
