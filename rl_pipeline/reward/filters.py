"""
Invariant filters — reduce a candidate invariant set to the ones that "survive".

  * HoudiniFilter  : real inductive filtering via src/houdini_pruner.HoudiniPruner
                     + src/output_verify.OutputVerifier (needs frama-c on PATH).
                     This is the authoritative inductive filter.
  * PositiveFilter : drop invariants that are unsound on the sampled positive
                     (reachable) states.  A cheap proxy used (a) as the fast
                     pre-filter in the inference framework, and (b) as a fallback
                     when frama-c is unavailable.

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
from ..common.state import (
    State,
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
    "power", "factorial",
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

class HoudiniFilter:
    """Inductive filtering with Frama-C/WP (reuses src/ HoudiniPruner + OutputVerifier)."""

    name = "houdini"

    def __init__(self, logger: Optional[logging.Logger] = None, prefilter_positives: bool = True):
        from src.houdini_pruner import HoudiniPruner
        from src.output_verify import OutputVerifier

        self._HoudiniPruner = HoudiniPruner
        self._OutputVerifier = OutputVerifier
        self.log = logger or logging.getLogger("rl_pipeline.reward.houdini")
        self._positive = PositiveFilter()
        self.prefilter_positives = prefilter_positives

    def filter(self, prog: Program, loop_idx: int, invariants: List[str],
               positives: Optional[List[State]] = None) -> List[str]:
        invs = [cond for i in invariants if (cond := normalize_invariant(i))]
        # cheap positive pre-filter first (mirrors the inference pipeline)
        if self.prefilter_positives and positives:
            invs = self._positive.filter(prog, loop_idx, invs, positives)
        if not invs:
            return []
        invs = self._syntax_scrub(prog, loop_idx, invs)
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
                      dropped: Optional[List[Tuple[str, str]]] = None) -> List[str]:
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

        invs = list(invs)
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
    """Run cheap-to-expensive filters: positive first, then real Houdini.

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
    """Cascade (Houdini-lite → real Houdini) if frama-c is available, else lite only."""
    if frama_c_available():
        # A broken Frama-C integration must fail loudly rather than silently
        # changing production rewards to the lite approximation.
        return CascadeFilter([
            PositiveFilter(),
            HoudiniFilter(logger=logger, prefilter_positives=False),
        ])
    (logger or logging.getLogger("rl_pipeline.reward")).warning(
        "frama-c not available; using PositiveFilter (Houdini-lite) only")
    return PositiveFilter()
