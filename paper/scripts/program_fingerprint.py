"""Target-independent program fingerprints for train/test curation.

Three uses, all computed from the target-hidden source only:

* **near-duplicate detection** against the evaluation corpus (exact tokens,
  alpha-renamed, alpha-renamed with abstracted constants);
* **structural relatedness** to the evaluation corpus (ordered control-flow
  skeleton, coarse control profile, and a bag of static loop features);
* **stratum assignment** (``linear`` / ``NLA`` / ``Loopy``) by nearest
  evaluation programs, so a curated pool can be re-weighted toward the
  evaluation mix without copying any evaluation program.

The normalizations are the ones audited in
``paper/scripts/audit_train_test_overlap.py``; they are imported rather than
re-implemented so the curation ledger and the overlap audit agree.
"""

from __future__ import annotations

import hashlib
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper.scripts.audit_train_test_overlap import (  # noqa: E402
    EVAL_DIRS,
    IDENT_RE,
    NONDET_RE,
    C_KEYWORDS,
    alpha_normalized,
    coarse_control_profile,
    first_loop,
    matching_delimiter,
    near_exact_tokens,
    ordered_control_skeleton,
    tokens,
)
from rl_pipeline.common.program import parse_program, strip_postcondition  # noqa: E402
from rl_pipeline.reward.annotate import modified_vars  # noqa: E402

STRATA = ("linear", "NLA", "Loopy")
# Evaluation directory name -> stratum label used throughout the paper.
STRATUM_BY_DIR = {"linear": "linear", "NLA_lipus": "NLA", "Loopy": "Loopy"}

# Near-duplicate levels, strongest first.  A training program matching an
# evaluation program at ANY of these is a copy and must not be trained on.
# ``alpha_const_loop`` ignores the pre-loop initializer block as well; it is
# reported separately so the caller can decide whether it counts as a copy.
DUPLICATE_LEVELS = ("exact", "alpha", "alpha_const", "alpha_const_loop")
DEFAULT_DEDUP_LEVELS = ("exact", "alpha", "alpha_const")
# Structural relatedness levels, strongest first.
RELATED_LEVELS = ("skeleton_ops", "skeleton", "coarse")

_PRODUCT_RE = re.compile(r"([A-Za-z_]\w*)\s*\*\s*([A-Za-z_]\w*)")
_DIV_MOD_RE = re.compile(r"(?<![/*])[/%](?![/*=])")
_INCREMENT_RE = re.compile(r"\b\w+\s*(?:\+\+|--)|(?:\+\+|--)\s*\b\w+|\b\w+\s*[-+]=\s*\d+\s*;")
_COMPOUND_RE = re.compile(r"\b\w+\s*[-+*/%]=\s*[A-Za-z_(]")
_ASSIGN_RE = re.compile(r"\b(\w+)\s*=(?!=)\s*([^;]+);")


def _sha(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


_WHILE_TRUE_RE = re.compile(r"\bwhile\s*\(\s*(?:1|true)\s*\)\s*\{")


def canonicalize_break_idiom(source: str) -> Tuple[str, bool]:
    """Rewrite ``while (1) { if (COND) break; REST }`` as ``while (!COND) { REST }``.

    The evaluation corpus never uses the ``break`` idiom while ~40% of the
    training pool does.  The rewrite is semantics-preserving (the guard test
    happens at the same program point) and restores a real loop guard, which
    the negative sampler relies on for guard-preserving relation witnesses.
    Returns the (possibly unchanged) source and whether it changed.  Loops
    with any other ``break``, or an ``else`` attached to the exit test, are
    left alone.
    """
    match = _WHILE_TRUE_RE.search(source)
    if match is None:
        return source, False
    body_open = match.end() - 1
    body_close = matching_delimiter(source, body_open, "{", "}")
    if body_close <= body_open:
        return source, False
    body = source[body_open + 1:body_close]
    head = re.match(r"\s*if\s*\(", body)
    if head is None:
        return source, False
    cond_open = body.find("(", head.start())
    cond_close = matching_delimiter(body, cond_open, "(", ")")
    if cond_close <= cond_open:
        return source, False
    condition = body[cond_open + 1:cond_close].strip()
    tail = body[cond_close + 1:]
    exit_stmt = re.match(r"\s*(?:break\s*;|\{\s*break\s*;\s*\})", tail)
    if exit_stmt is None:
        return source, False
    rest = tail[exit_stmt.end():]
    if re.match(r"\s*else\b", rest) or re.search(r"\bbreak\b", rest):
        return source, False
    negated = re.fullmatch(r"!\s*\((.*)\)", condition, re.DOTALL)
    if negated and matching_delimiter(condition, condition.find("("), "(", ")") == len(condition) - 1:
        guard = negated.group(1).strip()
    else:
        guard = f"!({condition})"
    rewritten = source[:match.start()] + f"while ({guard}) {{" + rest + source[body_close:]
    return rewritten, True


def loop_only_source(source: str) -> str:
    """Guard + body of the first loop, target-hidden (drops initializers)."""
    guard, body = first_loop(strip_postcondition(source))
    return f"while ({guard}) {{{body}}}"


def _bucket(count: int, cap: int = 3) -> int:
    return min(int(count), cap)


def structural_features(source: str) -> Dict[str, object]:
    """Static, target-independent loop features used for relatedness/stratum."""
    hidden = strip_postcondition(source)
    guard, body = first_loop(hidden)
    body_tokens = tokens(body)
    guard_tokens = tokens(guard)
    identifiers = lambda values: {  # noqa: E731
        v for v in values if IDENT_RE.fullmatch(v) and v not in C_KEYWORDS
        and not NONDET_RE.fullmatch(v)
    }
    try:
        program = parse_program(hidden)
        pre_vars = list(program.pre_vars)
        params = list(program.params)
        requires = bool(program.requires.strip())
        n_loops = len(program.loops)
    except Exception:  # unparsable for the sampler: keep token-level features
        pre_vars = sorted(identifiers(tokens(hidden)))
        params = []
        requires = "requires" in hidden
        n_loops = len(re.findall(r"\b(?:while|for)\s*\(", hidden))
    modified = [v for v in modified_vars(body) if v in set(pre_vars)] if pre_vars else modified_vars(body)
    guard_ids = identifiers(guard_tokens)
    products = [
        (a, b) for a, b in _PRODUCT_RE.findall(body + ";" + guard)
        if a not in C_KEYWORDS and b not in C_KEYWORDS
    ]
    nonlinear = bool(products)
    modified_set = set(modified)
    # A product of two loop-modified variables is a genuinely nonlinear
    # recurrence; ``x = x * z`` with a fixed parameter z is affine in x.
    product_of_modified = any(a in modified_set and b in modified_set for a, b in products)
    if re.fullmatch(r"\s*[01]\s*", guard or ""):
        guard_kind = "constant"
    elif len(guard_ids) == 0:
        guard_kind = "constant"
    elif len(guard_ids) == 1 and not re.search(r"&&|\|\|", guard):
        guard_kind = "single_var"
    elif not re.search(r"&&|\|\|", guard):
        guard_kind = "var_vs_var"
    else:
        guard_kind = "compound"
    update_kinds = set()
    if _INCREMENT_RE.search(body):
        update_kinds.add("increment")
    if _COMPOUND_RE.search(body):
        update_kinds.add("compound")
    for name, rhs in _ASSIGN_RE.findall(body):
        rhs_ids = identifiers(tokens(rhs))
        if any(
            a not in C_KEYWORDS and b not in C_KEYWORDS
            for a, b in _PRODUCT_RE.findall(rhs)
        ):
            update_kinds.add("product")
        elif rhs_ids - {name}:
            update_kinds.add("linear_mix")
        else:
            update_kinds.add("increment")
    return {
        "n_pre_vars": _bucket(len(pre_vars), 6),
        "n_params": _bucket(len(params), 4),
        "n_modified": _bucket(len(modified), 4),
        "n_guard_vars": _bucket(len(guard_ids), 3),
        "guard_kind": guard_kind,
        "nonlinear": bool(nonlinear),
        "product_of_modified": bool(product_of_modified),
        "div_mod": bool(_DIV_MOD_RE.search(body) or _DIV_MOD_RE.search(guard)),
        "nondet": any(NONDET_RE.fullmatch(v) for v in body_tokens + guard_tokens),
        "n_if": _bucket(len(re.findall(r"\bif\b", body))),
        "n_else": _bucket(len(re.findall(r"\belse\b", body))),
        "has_break": bool(re.search(r"\b(?:break|return|goto)\b", body)),
        "nested_loop": n_loops > 1 or bool(re.search(r"\b(?:while|for)\s*\(", body)),
        "requires": bool(requires),
        "update_kinds": tuple(sorted(update_kinds)),
        "body_stmts": _bucket(body.count(";"), 8),
    }


def feature_bag(features: Dict[str, object]) -> frozenset:
    """Flatten a feature dict into a set of ``key=value`` atoms for Jaccard."""
    atoms = set()
    for key, value in features.items():
        if isinstance(value, tuple):
            atoms.update(f"{key}={item}" for item in value)
            if not value:
                atoms.add(f"{key}=none")
        else:
            atoms.add(f"{key}={value}")
    return frozenset(atoms)


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def structural_cell(features: Dict[str, object], coarse: str) -> str:
    """Coarse structural partition used to re-weight a pool toward the
    evaluation distribution.  Deliberately coarse: the evaluation corpus has
    832 programs, so a cell must hold several of them to carry a weight."""
    n_vars = int(features.get("n_pre_vars", 0))
    var_band = "v<=2" if n_vars <= 2 else ("v3-4" if n_vars <= 4 else "v5+")
    return _sha((
        coarse,
        features.get("guard_kind"),
        bool(features.get("nonlinear")),
        bool(features.get("nondet")),
        var_band,
    ))


@dataclass(frozen=True)
class Fingerprint:
    exact: str
    alpha: str
    alpha_const: str
    alpha_const_loop: str
    skeleton_ops: str
    skeleton: str
    coarse: str
    cell: str
    features: Dict[str, object] = field(default_factory=dict)

    def level_keys(self) -> Dict[str, str]:
        return {
            "exact": self.exact,
            "alpha": self.alpha,
            "alpha_const": self.alpha_const,
            "alpha_const_loop": self.alpha_const_loop,
            "skeleton_ops": self.skeleton_ops,
            "skeleton": self.skeleton,
            "coarse": self.coarse,
        }

    def to_dict(self) -> dict:
        return asdict(self)


def fingerprint(source: str) -> Fingerprint:
    """Fingerprint of the target-hidden, break-idiom-canonicalized source."""
    source, _ = canonicalize_break_idiom(source)
    features = structural_features(source)
    coarse = _sha(coarse_control_profile(source))
    return Fingerprint(
        exact=_sha(near_exact_tokens(source)),
        alpha=_sha(alpha_normalized(source, abstract_constants=False)),
        alpha_const=_sha(alpha_normalized(source, abstract_constants=True)),
        alpha_const_loop=_sha(
            alpha_normalized(loop_only_source(source), abstract_constants=True)
        ),
        skeleton_ops=_sha(ordered_control_skeleton(source, keep_condition_ops=True)),
        skeleton=_sha(ordered_control_skeleton(source, keep_condition_ops=False)),
        coarse=coarse,
        cell=structural_cell(features, coarse),
        features=features,
    )


@dataclass
class EvaluationIndex:
    """Fingerprints of the evaluation corpus, indexed for dedup and relatedness."""

    by_level: Dict[str, Dict[str, List[str]]]   # level -> key -> [stratum, ...]
    bags: List[Tuple[frozenset, str]]            # (feature bag, stratum)
    stratum_counts: Counter
    cell_counts: Counter                         # structural cell -> #eval programs
    n_programs: int

    @classmethod
    def from_sources(cls, items: Iterable[Tuple[str, str]]) -> "EvaluationIndex":
        by_level: Dict[str, Dict[str, List[str]]] = {
            level: {} for level in DUPLICATE_LEVELS + RELATED_LEVELS
        }
        bags: List[Tuple[frozenset, str]] = []
        counts: Counter = Counter()
        cells: Counter = Counter()
        total = 0
        for stratum, source in items:
            fp = fingerprint(source)
            for level, key in fp.level_keys().items():
                by_level[level].setdefault(key, []).append(stratum)
            bags.append((feature_bag(fp.features), stratum))
            counts[stratum] += 1
            cells[fp.cell] += 1
            total += 1
        return cls(
            by_level=by_level, bags=bags, stratum_counts=counts,
            cell_counts=cells, n_programs=total,
        )

    @classmethod
    def from_evaluation_dirs(cls, directories: Sequence[Path] = EVAL_DIRS) -> "EvaluationIndex":
        items = []
        for directory in directories:
            stratum = STRATUM_BY_DIR.get(directory.name, directory.name)
            for path in sorted(directory.glob("*.c")):
                items.append((stratum, path.read_text(encoding="utf-8")))
        return cls.from_sources(items)

    def stratum_mix(self) -> Dict[str, float]:
        return {
            stratum: self.stratum_counts.get(stratum, 0) / self.n_programs
            for stratum in STRATA
        }

    def assess(
        self,
        source: str,
        neighbours: int = 5,
        dedup_levels: Sequence[str] = DEFAULT_DEDUP_LEVELS,
    ) -> Dict[str, object]:
        """Dedup + relatedness verdict for one training program.

        ``duplicate_level`` is the strongest level in ``dedup_levels`` that
        matches an evaluation program (None = not a copy); ``copy_levels``
        lists every duplicate level that matches, for reporting.
        """
        fp = fingerprint(source)
        keys = fp.level_keys()
        copy_levels = [
            level for level in DUPLICATE_LEVELS if keys[level] in self.by_level[level]
        ]
        duplicate_level = next(
            (level for level in copy_levels if level in dedup_levels), None
        )
        related_level: Optional[str] = None
        for level in RELATED_LEVELS:
            if keys[level] in self.by_level[level]:
                related_level = level
                break
        if neighbours <= 0:
            # Callers that only need dedup/cell verdicts skip the 832-bag scan.
            return {
                "fingerprint": fp.to_dict(),
                "copy_levels": copy_levels,
                "duplicate_level": duplicate_level,
                "related_level": related_level,
                "similarity": 0.0,
                "cell": fp.cell,
                "cell_eval_count": self.cell_counts.get(fp.cell, 0),
                "stratum_guess": None,
            }
        import heapq
        bag = feature_bag(fp.features)
        scored = heapq.nlargest(
            neighbours,
            ((jaccard(bag, other), stratum) for other, stratum in self.bags),
            key=lambda item: item[0],
        )
        similarity = scored[0][0] if scored else 0.0
        # Informational only: features separate NLA well but linear/Loopy
        # poorly (leave-one-out kNN accuracy ~0.59 on the evaluation corpus).
        votes: Counter = Counter()
        for weight, stratum in scored:
            votes[stratum] += weight / max(1, self.stratum_counts.get(stratum, 1))
        stratum = votes.most_common(1)[0][0] if votes else "linear"
        return {
            "fingerprint": fp.to_dict(),
            "copy_levels": copy_levels,
            "duplicate_level": duplicate_level,
            "related_level": related_level,
            "similarity": round(similarity, 4),
            "cell": fp.cell,
            "cell_eval_count": self.cell_counts.get(fp.cell, 0),
            "stratum_guess": stratum,
        }


def relatedness_score(verdict: Dict[str, object]) -> float:
    """Scalar in [0, 1]: skeleton match dominates, feature similarity refines."""
    level = verdict.get("related_level")
    base = {"skeleton_ops": 0.9, "skeleton": 0.75, "coarse": 0.5}.get(level, 0.0)
    return round(base + (1.0 - base) * float(verdict.get("similarity", 0.0)), 4)


def tv_distance(counts_a: Counter, counts_b: Counter) -> float:
    """Total-variation distance between two distributions given as counts."""
    total_a = sum(counts_a.values()) or 1
    total_b = sum(counts_b.values()) or 1
    keys = set(counts_a) | set(counts_b)
    return round(0.5 * sum(abs(counts_a.get(k, 0) / total_a - counts_b.get(k, 0) / total_b) for k in keys), 4)


def quota_select(
    candidates: Dict[str, Tuple[str, str, float]],
    eval_cells: Counter,
    target: int,
    per_shape_cap: int,
) -> List[str]:
    """Pick up to ``target`` candidates so the selected cell distribution
    tracks ``eval_cells``.

    ``candidates`` maps id -> (cell, shape, priority).  Each cell receives a
    quota proportional to its evaluation share; within a cell candidates are
    taken round-robin over loop shapes (highest priority first) so no shape
    dominates, with at most ``per_shape_cap`` per shape overall.  Budget that
    cells cannot fill (too few candidates) is redistributed to the cells that
    still have candidates, proportionally to their evaluation share, until the
    target is met or every candidate cell is exhausted.
    """
    by_cell: Dict[str, Dict[str, List[str]]] = {}
    for cid, (cell, shape, priority) in candidates.items():
        by_cell.setdefault(cell, {}).setdefault(shape, []).append(cid)
    for shapes in by_cell.values():
        for ids in shapes.values():
            ids.sort(key=lambda cid: (-candidates[cid][2], cid))
    shape_used: Counter = Counter()
    taken: Dict[str, List[str]] = {cell: [] for cell in by_cell}
    cursor: Dict[str, Dict[str, int]] = {cell: {shape: 0 for shape in shapes} for cell, shapes in by_cell.items()}

    # Shape order within a cell never changes; compute it once.
    shape_order = {
        cell: sorted(shapes, key=lambda sh: (-max(candidates[c][2] for c in shapes[sh]), sh))
        for cell, shapes in by_cell.items()
    }

    def draw(cell: str, want: int) -> int:
        got = 0
        shapes = shape_order[cell]
        while got < want:
            progressed = False
            for shape in shapes:
                if got >= want:
                    break
                ids = by_cell[cell][shape]
                while cursor[cell][shape] < len(ids):
                    cid = ids[cursor[cell][shape]]
                    cursor[cell][shape] += 1
                    if shape_used[shape] < per_shape_cap:
                        taken[cell].append(cid)
                        shape_used[shape] += 1
                        got += 1
                        progressed = True
                        break
            if not progressed:
                break
        return got

    # Quota only over cells that exist in both the pool and the evaluation set;
    # pool cells absent from evaluation get nothing (they are "unrelated").
    eligible = {cell: eval_cells[cell] for cell in by_cell if eval_cells.get(cell, 0) > 0}
    remaining = target
    for _ in range(8):  # redistribution rounds
        share_total = sum(eligible.values())
        if not eligible or share_total == 0 or remaining <= 0:
            break
        planned = {cell: max(1, round(remaining * count / share_total)) for cell, count in eligible.items()}
        drawn_total = 0
        exhausted = []
        for cell, want in planned.items():
            got = draw(cell, want)
            drawn_total += got
            if got < want:
                exhausted.append(cell)
        remaining -= drawn_total
        for cell in exhausted:
            eligible.pop(cell, None)
        if drawn_total == 0:
            break
    selected = [cid for ids in taken.values() for cid in ids]
    return selected[:target]


def _trivial_counter(features: Dict[str, object]) -> bool:
    """A single-variable straight-line counter: the answer is one bound."""
    kinds = set(features.get("update_kinds", ()))
    return (
        int(features.get("n_modified", 0)) <= 1
        and int(features.get("n_pre_vars", 0)) <= 2
        and not features.get("nondet")
        and not features.get("nonlinear")
        and int(features.get("n_if", 0)) == 0
        and kinds <= {"increment"}
    )


def _nonlinear_recurrence(features: Dict[str, object]) -> bool:
    """Products of loop-modified variables over many variables (NLA stratum)."""
    return bool(features.get("product_of_modified")) and int(features.get("n_modified", 0)) >= 3


def _wide_and_long(features: Dict[str, object]) -> bool:
    return int(features.get("n_modified", 0)) >= 5 and int(features.get("body_stmts", 0)) >= 8


def difficulty_verdict(features: Dict[str, object]) -> Optional[str]:
    """Static difficulty screen shared by RL and SFT curation.

    ``too_easy``: a trivial counter.  ``too_hard``: a nonlinear recurrence
    over many variables (the benchmark's NLA stratum, where compose@k stays at
    0-4%), or a very wide and long body.  Returns None in between.
    """
    if _trivial_counter(features):
        return "too_easy"
    if _nonlinear_recurrence(features) or _wide_and_long(features):
        return "too_hard"
    return None


def nla_admissible(features: Dict[str, object]) -> bool:
    """SFT NLA-boost exception to the difficulty screen: nonlinear programs
    are admitted even when ``difficulty_verdict`` says too-hard, except the
    trivial-counter and wide-and-long cases (composed from the same
    predicates, so the screen and the exception cannot drift apart)."""
    return (
        bool(features.get("nonlinear"))
        and not _trivial_counter(features)
        and not _wide_and_long(features)
    )
