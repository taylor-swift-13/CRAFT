"""Insert an ACSL loop-invariant block into a program (shared by reward + inference)."""
from __future__ import annotations

import re
from typing import List

from ..common.program import Program
from ..common.state import normalize_invariant


def modified_vars(body: str) -> List[str]:
    """Variables assigned in the loop body (for the `loop assigns` frame clause)."""
    declared = set(re.findall(
        r"\b(?:int|long|short|unsigned|char|_Bool)\s+(\w+)\s*[=;,]", body
    ))
    names: List[str] = []
    pattern = re.compile(
        r"\b(\w+)\s*(?:[-+*/%]?=)(?!=)"
        r"|\b(\w+)\s*(?:\+\+|--)"
        r"|(?:\+\+|--)\s*\b(\w+)"
    )
    for match in pattern.finditer(body):
        v = next(group for group in match.groups() if group)
        if v not in names and v not in declared:
            names.append(v)
    return names


def build_annotated(prog: Program, invariants: List[str], loop_idx: int = 0) -> str:
    """Return the program source with only candidate invariants before the loop.

    ``loop assigns`` used to be synthesized here.  Besides being a separate
    frame specification that the model never proposed, its WP obligations were
    accidentally mixed with benchmark assertion results.  Omitting it gives
    Frama-C the conservative default (the loop may assign everything) and keeps
    both filtering and final judgment about the invariant clauses themselves.
    """
    loop = prog.loops[loop_idx]
    invs = [normalized for invariant in invariants
            if (normalized := normalize_invariant(invariant))]
    lines = [f"      loop invariant {i};" for i in invs]
    block = "/*@\n" + "\n".join(lines) + "\n    */\n    "
    src = prog.source
    return src[:loop.kw_start] + block + src[loop.kw_start:]
