"""
Invariant filters — reduce a candidate invariant set to the ones that "survive".

  * HoudiniFilter  : real inductive filtering via src/houdini_pruner.HoudiniPruner
                     + src/output_verify.OutputVerifier (needs frama-c on PATH).
                     This is the authoritative inductive filter.
  * PreFramaFilter : syntax/scope filtering, conservative semantic de-duplication,
                     and sampled-positive checking before any Frama-C process.
  * PositiveFilter : the sampled-positive component, retained as a small public
                     filter for tests and callers that explicitly request it.

Both expose the same interface:
filter(prog, loop_idx, invariants, positives) -> List[str].
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from typing import List, Optional, Tuple

from ..common.program import Program
from ..common.acsl_parser import lightweight_syntax_filter
from ..common.state import (
    State,
    dedup_normalized,
    extract_invariants,
    first_falsifying_state,
    normalize_invariant,
)
from . import annotate

# ACSL / boolean tokens that are not program variables
_ACSL_STOPWORDS = {
    "at", "Pre", "Post", "LoopEntry", "LoopCurrent", "old", "result",
    "true", "false",
    "True", "False", "None", "and", "or", "not", "bool", "abs",
    "integer", "int", "long", "short", "unsigned", "signed", "real",
}

def frama_c_available() -> bool:
    return shutil.which("frama-c") is not None


def out_of_scope_ids(inv: str, allowed) -> List[str]:
    """Identifiers referenced by `inv` that are neither program vars nor ACSL tokens."""
    bound = set()
    for match in re.finditer(
        r"\\(?:forall|exists)\s+(?:integer|int|long|short|real)\s+([^;]+);",
        inv,
    ):
        bound.update(re.findall(r"[A-Za-z_]\w*", match.group(1)))
    stripped = re.sub(r"\\[A-Za-z_]+", " ", inv)      # drop \at, \old, \forall ...
    ids = re.findall(r"[A-Za-z_]\w*", stripped)
    allow = set(allowed) | _ACSL_STOPWORDS | bound
    return [i for i in ids if i not in allow]


class PositiveFilter:
    """Keep invariants that are (a) in scope and (b) not violated by any positive state."""

    name = "positive"

    def filter(self, prog: Program, loop_idx: int, invariants: List[str],
               positives: Optional[List[State]] = None) -> List[str]:
        # This is a soundness filter over the sampled reachable set.  Checking a
        # stride-based subset let predicates target a skipped reachable state.
        sample = positives or []
        kept: List[str] = []
        for inv in invariants:
            cond = normalize_invariant(inv)
            if not cond:
                continue
            # scope gate: reject invariants naming out-of-scope identifiers
            # (Frama-C would reject them, and an undeclared name can break parsing
            #  of the whole file).
            bad = out_of_scope_ids(cond, prog.pre_vars)
            if bad:
                continue
            witness = first_falsifying_state(cond, sample)
            if witness is not None:
                continue
            kept.append(cond)
        return kept


class PreFramaFilter:
    """Cheap, conservative candidate reduction before any Frama-C invocation.

    The order matters: malformed clauses are removed first, semantic duplicates
    are collapsed next, and only then are survivors evaluated on every sampled
    reachable state.  Unknown evaluator results are retained for Frama-C, so
    this stage cannot replace the authoritative kernel/WP checks.
    """

    name = "pre-frama"

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.log = logger or logging.getLogger("rl_pipeline.reward.pre_frama")
        self._positive = PositiveFilter()

    def filter(self, prog: Program, loop_idx: int, invariants: List[str],
               positives: Optional[List[State]] = None) -> List[str]:
        normalized = [
            condition
            for invariant in invariants
            if (condition := normalize_invariant(invariant))
        ]
        syntactic, syntax_dropped = lightweight_syntax_filter(prog, normalized)
        unique = dedup_normalized(syntactic)
        kept = self._positive.filter(prog, loop_idx, unique, positives)
        duplicate_count = len(syntactic) - len(unique)
        positive_count = len(unique) - len(kept)
        if syntax_dropped or duplicate_count or positive_count:
            self.log.debug(
                "pre-Frama filter: %d input, %d malformed, %d duplicate, "
                "%d positive-unsound, %d kept",
                len(normalized),
                len(syntax_dropped),
                duplicate_count,
                positive_count,
                len(kept),
            )
        return kept

class HoudiniFilter:
    """Inductive filtering with Frama-C/WP (reuses src/ HoudiniPruner + OutputVerifier)."""

    name = "houdini"

    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        prefilter_positives: bool = True,
        lightweight_prefilter: bool = True,
    ):
        from src.houdini_pruner import HoudiniPruner
        from src.output_verify import OutputVerifier

        self._HoudiniPruner = HoudiniPruner
        self._OutputVerifier = OutputVerifier
        self.log = logger or logging.getLogger("rl_pipeline.reward.houdini")
        self._positive = PositiveFilter()
        self._pre_frama = PreFramaFilter(logger=self.log)
        self.prefilter_positives = prefilter_positives
        self.lightweight_prefilter = lightweight_prefilter

    def filter(self, prog: Program, loop_idx: int, invariants: List[str],
               positives: Optional[List[State]] = None) -> List[str]:
        invs = [cond for i in invariants if (cond := normalize_invariant(i))]
        # Direct HoudiniFilter callers get the same cheap front-end as the
        # production cascade.  The cascade disables both switches because its
        # explicit PreFramaFilter stage has already done this work.
        if self.lightweight_prefilter:
            invs = self._pre_frama.filter(
                prog,
                loop_idx,
                invs,
                positives if self.prefilter_positives else None,
            )
        elif self.prefilter_positives and positives:
            invs = self._positive.filter(prog, loop_idx, invs, positives)
        if not invs:
            return []
        invs = self._syntax_scrub(
            prog,
            loop_idx,
            invs,
            lightweight_prefilter=False,
        )
        if not invs:
            return []
        code = annotate.build_annotated(prog, invs, loop_idx)
        tmpdir = tempfile.mkdtemp(prefix="rlreward_")
        cpath = os.path.join(tmpdir, "prog.c")
        try:
            with open(cpath, "w") as f:
                f.write(code)
            verifier = self._OutputVerifier(logger=self.log)
            pruner = self._HoudiniPruner(logger=self.log)
            pruned_code, valid = pruner.houdini(code, verifier, cpath)
            survivors = extract_invariants(pruned_code) if pruned_code and valid else []
        except Exception as e:  # frama-c hiccup -> conservative empty
            self.log.warning("Houdini filter failed: %s", e)
            return []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        return survivors

    def _syntax_scrub(self, prog: Program, loop_idx: int, invs: List[str],
                      dropped: Optional[List[Tuple[str, str]]] = None,
                      lightweight_prefilter: bool = True) -> List[str]:
        """Drop `loop invariant` entries FRAMA-C rejects (parse/typecheck): one
        kernel-only run per round; the error's line number maps back to the
        offending entry (each sits on its own line).  An unmappable error falls
        back to per-clause checks. `dropped`, if given, collects
        (entry, frama-c error text) pairs."""
        import subprocess

        def kernel_error(code: str):
            """None if clean, else (line or -1, first error message line)."""
            tmpdir = tempfile.mkdtemp(prefix="rlsyn_")
            cpath = os.path.join(tmpdir, "prog.c")
            try:
                with open(cpath, "w") as f:
                    f.write(code)
                res = subprocess.run(["frama-c", cpath], capture_output=True,
                                     text=True, timeout=30)
                err = res.stdout + res.stderr
                if res.returncode == 0 and "user error" not in err:
                    return None                       # parses clean
                # frama-c wraps long messages onto indented continuation lines
                m = re.search(rf"{re.escape(cpath)}:(\d+):\s*([^\n]*(?:\n[ \t]+[^\n]*)*)", err)
                if m:
                    msg = re.sub(r"\s+", " ", m.group(2)).strip()
                    msg = re.sub(r"^Warning:\s*", "", msg)
                    return int(m.group(1)), msg[:120]
                return -1, ""                         # error, no line info
            except Exception:
                return -1, ""
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

        def note(inv: str, msg: str):
            self.log.info("syntax scrub (frama-c): dropping %r (%s)", inv, msg)
            if dropped is not None:
                dropped.append((inv, msg or "parse/typecheck error"))

        # Keep the expensive kernel stage strictly last: reject malformed
        # clauses first, then collapse exact/conservatively equivalent forms.
        # The stable de-duplicator preserves the first spelling for diagnostics.
        invs = list(invs)
        if lightweight_prefilter:
            invs, light_dropped = lightweight_syntax_filter(prog, invs)
            for invariant, reason in light_dropped:
                self.log.info(
                    "syntax scrub (lightweight): dropping %r (%s)",
                    invariant,
                    reason,
                )
                if dropped is not None:
                    dropped.append((invariant, reason))
            invs = dedup_normalized(invs)
        for _ in range(len(invs) + 1):
            if not invs:
                return []
            code = annotate.build_annotated(prog, invs, loop_idx)
            hit_err = kernel_error(code)
            if hit_err is None:
                return invs
            line, msg = hit_err
            if line > 0:
                line_map = {
                    line_number: inv
                    for line_number, text in enumerate(code.splitlines(), 1)
                    for inv in invs
                    if text.strip() == f"loop invariant {inv};"
                }
                hit = line_map.get(line)
                if hit is not None:
                    note(hit, msg)
                    invs.remove(hit)
                    continue
            # unmappable: per-clause fallback
            self.log.info("syntax scrub: per-clause fallback over %d entries", len(invs))
            kept = []
            for i in invs:
                res = kernel_error(annotate.build_annotated(prog, [i], loop_idx))
                if res is None:
                    kept.append(i)
                else:
                    note(i, res[1])
            return kept
        return invs


class CascadeFilter:
    """Run cheap-to-expensive filters: pre-Frama first, then real Houdini.

    The lite stage drops out-of-scope or sampled-unsound invariants, so Frama-C
    sees fewer WP goals and usually needs fewer pruning iterations.
    """

    def __init__(self, stages):
        self.stages = stages
        self.name = "cascade(" + "->".join(getattr(s, "name", "?") for s in stages) + ")"

    def filter(self, prog: Program, loop_idx: int, invariants: List[str],
               positives: Optional[List[State]] = None) -> List[str]:
        invs = invariants
        for st in self.stages:
            if not invs:
                break
            invs = st.filter(prog, loop_idx, invs, positives)
        return invs

def auto_filter(logger: Optional[logging.Logger] = None):
    """Use the pre-Frama reducer before Houdini, or alone without Frama-C."""
    pre_frama = PreFramaFilter(logger=logger)
    if frama_c_available():
        # A broken Frama-C integration must fail loudly rather than silently
        # changing production rewards to the lite approximation.
        return CascadeFilter([
            pre_frama,
            HoudiniFilter(
                logger=logger,
                prefilter_positives=False,
                lightweight_prefilter=False,
            ),
        ])
    (logger or logging.getLogger("rl_pipeline.reward")).warning(
        "frama-c not available; using PreFramaFilter (Houdini-lite) only")
    return pre_frama
