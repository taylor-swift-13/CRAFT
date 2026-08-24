from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper.scripts.curate_training_pool import (  # noqa: E402
    apply_shape_cap,
    assess_program,
    importance_weights,
)
from paper.scripts.program_fingerprint import (  # noqa: E402
    EvaluationIndex,
    fingerprint,
    structural_features,
)
from rl_pipeline.sampler.example_sampler import NEGATIVE_SCHEMA_VERSION  # noqa: E402

COUNTER = "void f(void) { int x = 0; while (x < 6) { x = x + 1; } }"
COUNTER_RENAMED = "void g(void) { int k = 0; while (k < 6) { k = k + 1; } }"
COUNTER_OTHER_CONST = "void g(void) { int k = 0; while (k < 9) { k = k + 1; } }"
COUNTER_OTHER_INIT = "/*@ requires v < 0; */ void h(int v) { while (v < 10) { v = v + 1; } }"
PRODUCT = "void f(int n) { int x = 0; int y = 1; while (x < n) { y = y * x; x++; } }"
NONDET = (
    "int unknown(void); void f(int n) { int x = 0; int y = 0; "
    "while (x < n) { if (unknown()) { x++; } else { y += 2; } } }"
)
SUM = "/*@ requires n >= 0; */ void f(int n) { int i = 0; int s = 0; while (i < n) { s = s + i; i = i + 1; } }"
NLA_WIDE = "void f(int a, int b) { int x = 0; int y = 1; int z = 1; while (x < a) { y = y * b; z = z * y; x = x + 1; } }"


def _ledger_row(traces: int, relation: int, *, scorable: bool = True,
                schema: int = NEGATIVE_SCHEMA_VERSION, error: str = "") -> dict:
    row = {
        "coverage_schema_version": schema,
        "scorable": scorable,
        "n_negative_traces": traces,
        "sampler_stats": {"relation": relation, "escape": 1, "range": max(0, traces - relation - 1)},
    }
    if not scorable:
        row["error"] = error
    return row


class FingerprintTests(unittest.TestCase):
    def test_alpha_levels_are_invariant_to_renaming_and_constants(self):
        base, renamed, other_const = (
            fingerprint(COUNTER), fingerprint(COUNTER_RENAMED), fingerprint(COUNTER_OTHER_CONST)
        )
        self.assertNotEqual(base.exact, renamed.exact)
        self.assertEqual(base.alpha, renamed.alpha)
        self.assertNotEqual(base.alpha, other_const.alpha)
        self.assertEqual(base.alpha_const, other_const.alpha_const)

    def test_loop_only_level_ignores_initializers(self):
        self.assertNotEqual(fingerprint(COUNTER).alpha_const, fingerprint(COUNTER_OTHER_INIT).alpha_const)
        self.assertEqual(
            fingerprint(COUNTER).alpha_const_loop, fingerprint(COUNTER_OTHER_INIT).alpha_const_loop
        )

    def test_structural_features(self):
        self.assertTrue(structural_features(PRODUCT)["nonlinear"])
        self.assertIn("product", structural_features(PRODUCT)["update_kinds"])
        self.assertFalse(structural_features(COUNTER)["nonlinear"])
        nondet = structural_features(NONDET)
        self.assertTrue(nondet["nondet"])
        self.assertEqual(nondet["n_if"], 1)
        self.assertEqual(structural_features(COUNTER)["guard_kind"], "single_var")
        self.assertEqual(structural_features(PRODUCT)["guard_kind"], "var_vs_var")

    def test_evaluation_program_is_a_copy_of_itself_but_a_related_variant_is_not(self):
        index = EvaluationIndex.from_sources([("linear", COUNTER)])
        self.assertEqual(index.assess(COUNTER)["duplicate_level"], "exact")
        self.assertEqual(index.assess(COUNTER_RENAMED)["duplicate_level"], "alpha")
        self.assertEqual(index.assess(COUNTER_OTHER_CONST)["duplicate_level"], "alpha_const")
        variant = index.assess(COUNTER_OTHER_INIT)
        self.assertIsNone(variant["duplicate_level"])
        self.assertEqual(variant["copy_levels"], ["alpha_const_loop"])
        self.assertEqual(variant["related_level"], "skeleton_ops")
        self.assertEqual(
            index.assess(COUNTER_OTHER_INIT, dedup_levels=("alpha_const_loop",))["duplicate_level"],
            "alpha_const_loop",
        )


class GateTests(unittest.TestCase):
    def setUp(self):
        self.index = EvaluationIndex.from_sources([("linear", COUNTER), ("NLA", PRODUCT)])
        self.kw = dict(dedup_levels=("exact", "alpha", "alpha_const"),
                       min_traces=8, min_relation=4, min_relation_share=0.1)

    def test_gates(self):
        ok = assess_program(SUM, _ledger_row(50, 20), self.index, **self.kw)
        self.assertEqual(ok["gates"], [])
        self.assertEqual(ok["relation_share"], 0.4)
        cases = {
            "duplicate_of_eval": (COUNTER_RENAMED, _ledger_row(50, 20)),
            "unscorable": (SUM, None),
            "too_few_traces": (SUM, _ledger_row(5, 4)),
            "no_relation_signal": (SUM, _ledger_row(50, 0)),
            "bounds_saturated": (SUM, _ledger_row(100, 5)),
            "too_easy": (COUNTER_OTHER_INIT, _ledger_row(50, 20)),
            "too_hard": (NLA_WIDE, _ledger_row(50, 20)),
        }
        for gate, (source, row) in cases.items():
            verdict = assess_program(source, row, self.index, **self.kw)
            self.assertIn(gate, verdict["gates"], gate)
        stale = assess_program(SUM, _ledger_row(50, 20, schema=NEGATIVE_SCHEMA_VERSION - 1),
                               self.index, **self.kw)
        self.assertIn("unscorable", stale["gates"])
        self.assertIn("ledger_stale", stale["tags"])
        memory = assess_program(SUM, _ledger_row(0, 0, scorable=False, error="MemoryError: "),
                                self.index, **self.kw)
        self.assertIn("sampler_memory", memory["tags"])

    def test_shape_cap_prefers_distinct_full_shapes(self):
        verdicts = {}
        for i in range(6):
            verdicts[f"a{i}"] = {"gates": [], "shape": "S", "full_shape": "A",
                                 "n_negative_traces": 100 - i, "relation": 10}
        for i in range(2):
            verdicts[f"b{i}"] = {"gates": [], "shape": "S", "full_shape": "B",
                                 "n_negative_traces": 10, "relation": 1}
        verdicts["other"] = {"gates": [], "shape": "T", "full_shape": "C",
                             "n_negative_traces": 10, "relation": 1}
        apply_shape_cap(verdicts, 4)
        kept = sorted(d for d, v in verdicts.items() if not v["gates"])
        self.assertEqual(len([d for d in kept if verdicts[d]["shape"] == "S"]), 4)
        self.assertIn("b0", kept)          # second full shape survives the cap
        self.assertIn("a0", kept)          # richest negative set survives
        self.assertIn("other", kept)       # other shapes untouched
        self.assertIn("shape_cap", verdicts["a5"]["gates"])

    def test_importance_weights_follow_evaluation_cells(self):
        index = EvaluationIndex.from_sources([("linear", COUNTER)] * 3 + [("NLA", PRODUCT)])
        verdicts = {
            "c1": {"gates": [], "cell": fingerprint(COUNTER).cell},
            "p1": {"gates": [], "cell": fingerprint(PRODUCT).cell},
            "p2": {"gates": [], "cell": fingerprint(PRODUCT).cell},
            "p3": {"gates": [], "cell": fingerprint(PRODUCT).cell},
        }
        weights = importance_weights(verdicts, index, floor=0.1, ceiling=10.0)
        # counter cell: eval 3/4 vs train 1/4 -> 3.0 ; product: 1/4 vs 3/4 -> 1/3
        self.assertAlmostEqual(weights["c1"], 3.0)
        self.assertAlmostEqual(weights["p1"], 1 / 3, places=3)


class EndToEndTests(unittest.TestCase):
    def test_sft_curation_cli(self):
        def record(source: str) -> dict:
            return {"conversations": [
                {"from": "system", "value": "s"},
                {"from": "human", "value": "task\nProgram:\n" + source},
                {"from": "gpt", "value": "loop invariant x >= 0;"},
            ]}
        sources = [COUNTER_OTHER_INIT, NONDET, COUNTER_RENAMED]
        ledger = []
        for source, (traces, relation) in zip(sources, ((60, 30), (60, 30), (60, 30))):
            row = _ledger_row(traces, relation)
            row["source_sha256"] = hashlib.sha256(source.encode("utf-8")).hexdigest()
            ledger.append(row)
        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory)
            (d / "sft.json").write_text(json.dumps([record(s) for s in sources]))
            (d / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in ledger))
            result = subprocess.run(
                [sys.executable, str(ROOT / "paper/scripts/curate_training_pool.py"), "sft",
                 "--input", str(d / "sft.json"), "--ledger", str(d / "ledger.jsonl"),
                 "--output", str(d / "out.json"), "--report", str(d / "report.json")],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((d / "report.json").read_text())
            out = json.loads((d / "out.json").read_text())
        # COUNTER_RENAMED is an alpha-copy of evaluation program linear/208-style
        # counters only if the corpus has one; assert the report structure instead.
        self.assertEqual(report["input_rows"], 3)
        self.assertEqual(report["output_rows"], len(out))
        self.assertIn("gate_failures", report)
        self.assertIn("evaluation_cells", report)
        self.assertLessEqual(len(out), 3)


if __name__ == "__main__":
    unittest.main()


class SynthesisPruningTests(unittest.TestCase):
    def test_remove_implied_keeps_strongest_and_drops_consequences(self):
        from experiments.synthesize_sft_from_rollouts import remove_implied
        from paper.scripts.audit_sft_invariant_quality import _clause_features
        from rl_pipeline.common.program import parse_program
        from rl_pipeline.sampler.example_sampler import ExampleSampler
        program = parse_program("void f(int n) { int i = 0; int s = 0; while (i < n) { s = s + i; i = i + 1; } }")
        modified = set(ExampleSampler._modified_vars(program))
        clauses = ["0 <= i", "i <= n", "0 <= i && i <= n", "2 * s == i * (i - 1)",
                   "2 * s <= n * (n - 1)", "(n >= 1 ==> i <= n)", "(n > 1 ==> i <= n)"]
        features = {c: _clause_features(c, program, modified) for c in clauses}
        kept, dropped = remove_implied(clauses, features)
        self.assertIn("2 * s == i * (i - 1)", kept)
        self.assertIn("0 <= i && i <= n", kept)
        for redundant in ("0 <= i", "i <= n", "2 * s <= n * (n - 1)", "(n >= 1 ==> i <= n)", "(n > 1 ==> i <= n)"):
            self.assertIn(redundant, dropped, redundant)

    def test_remove_implied_keeps_untrusted_division_clauses(self):
        from experiments.synthesize_sft_from_rollouts import remove_implied
        from paper.scripts.audit_sft_invariant_quality import _clause_features
        from rl_pipeline.common.program import parse_program
        program = parse_program("void f(int n) { int i = 0; while (i < n) { i = i + 2; } }")
        clauses = ["i % 2 == 0", "i >= 0"]
        features = {c: _clause_features(c, program, {"i"}) for c in clauses}
        kept, dropped = remove_implied(clauses, features)
        self.assertEqual(kept, clauses)
        self.assertEqual(dropped, [])

    def test_frontier_verdicts(self):
        from experiments.measure_policy_frontier import frontier_verdict
        kw = dict(min_std=0.05, min_mean=0.05, max_mean=0.95)
        self.assertEqual(frontier_verdict({"status": "ok", "scorable": True, "mean": 0.5, "std": 0.2}, **kw), "frontier")
        self.assertEqual(frontier_verdict({"status": "ok", "scorable": True, "mean": 0.99, "std": 0.0}, **kw), "saturated")
        self.assertEqual(frontier_verdict({"status": "ok", "scorable": True, "mean": 0.0, "std": 0.0}, **kw), "hopeless")
        self.assertEqual(frontier_verdict({"status": "ok", "scorable": True, "mean": 0.5, "std": 0.0}, **kw), "flat")
        self.assertEqual(frontier_verdict({"status": "ok", "scorable": False, "mean": 0.0, "std": 0.0}, **kw), "unscorable")
        self.assertEqual(frontier_verdict({"status": "error"}, **kw), "error")

    def test_break_idiom_canonicalization(self):
        from paper.scripts.program_fingerprint import canonicalize_break_idiom
        rewritten, changed = canonicalize_break_idiom(
            "void f(int n) { int x = 0; while (1) { if (!(x < n)) break; x = x + 1; } }")
        self.assertTrue(changed)
        self.assertIn("while (x < n) {", rewritten)
        self.assertNotIn("break", rewritten)
        for untouched in (
            "void f(int n) { int x = 0; while (1) { if (!(x < n)) break; x++; if (x == 3) break; } }",
            "void f(int n) { int x = 0; while (1) { if (x < n) break; else { x++; } } }",
            "void f(int n) { int x = 0; while (x < n) { x++; } }",
        ):
            self.assertEqual(canonicalize_break_idiom(untouched), (untouched, False))


class SelectSftProgramsTests(unittest.TestCase):
    def test_select_merges_sft_rows_and_related_rl_extras(self):
        import pyarrow as pa
        import pyarrow.parquet as pq

        def sft_record(source: str, answer: str = "loop invariant x >= 0;") -> dict:
            return {"conversations": [
                {"from": "system", "value": "s"},
                {"from": "human", "value": "task\nProgram:\n" + source},
                {"from": "gpt", "value": answer},
            ]}

        def rl_record(source: str, relatedness: float, relation: int, traces: int) -> dict:
            return {
                "data_source": "loopgym",
                "prompt": [{"content": "s", "role": "system"},
                           {"content": "task\nProgram:\n" + source, "role": "user"}],
                "ability": "loop_invariant",
                "reward_model": {"ground_truth": {"raw_code": source}, "style": "frama-c"},
                "extra_info": {"file_id": "x", "curation": json.dumps({
                    "relatedness": relatedness, "relation": relation, "n_negative_traces": traces})},
            }

        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory)
            (d / "sft.json").write_text(json.dumps([sft_record(COUNTER_OTHER_INIT)]))
            rl = [
                rl_record(COUNTER_OTHER_INIT, 0.9, 10, 50),   # already in SFT -> skipped
                rl_record(PRODUCT, 0.95, 40, 100),             # related extra
                rl_record(NONDET, 0.2, 40, 100),               # below min relatedness
            ]
            pq.write_table(pa.Table.from_pylist(rl), d / "rl.parquet")
            result = subprocess.run(
                [sys.executable, str(ROOT / "paper/scripts/select_sft_programs.py"),
                 "--sft", str(d / "sft.json"), "--rl", str(d / "rl.parquet"),
                 "--output", str(d / "out.json"), "--report", str(d / "report.json"),
                 "--target", "10", "--min-relatedness", "0.5"],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            out = json.loads((d / "out.json").read_text())
            report = json.loads((d / "report.json").read_text())
        self.assertEqual(report["from_sft"], 1)
        self.assertEqual(report["from_rl"], 1)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["conversations"][2]["value"], "loop invariant x >= 0;")
        self.assertEqual(out[1]["conversations"][2]["value"], "")
        self.assertIn("y = y * x", out[1]["conversations"][1]["value"])
