from __future__ import annotations

import copy
import unittest

from paper.scripts.sanitize_training_prompts import (
    _derive_power_free_relations,
    _minimize_loop_entry,
    _remove_guarded_copies,
    _remove_subsumed_constant_bounds,
    _rewrite_fixed_powers,
    _sanitize_visible_source,
    _unnecessary_loop_entry_references,
    _universally_true,
    sanitize_rl_rows,
    sanitize_sft_records,
)
from rl_pipeline.common import prompts
from rl_pipeline.common.program import parse_program, strip_postcondition
from rl_pipeline.common.state import eval_predicate, invariant_dedup_key, State


FULL_SOURCE = """int f(int n) {
  int x = 0;
  // x tracks completed iterations
  while (x < n) { x++; }
  /*@ assert x == n; */
}"""


def _legacy_visible(source: str) -> str:
    comment = "// x tracks completed iterations"
    visible = strip_postcondition(source)
    start = source.index(comment)
    stop = start + len(comment)
    return visible[:start] + comment + visible[stop:]


def _sft_record(source: str, answer: str, system: str = "legacy system"):
    return {
        "conversations": [
            {"from": "system", "value": system},
            {"from": "human", "value": "Legacy task\nProgram:\n" + source},
            {"from": "gpt", "value": answer},
        ]
    }


class TrainingPromptSanitizerTests(unittest.TestCase):
    def test_universal_tautologies_are_proved_conservatively(self):
        self.assertTrue(_universally_true("x <= y || x >= y"))
        self.assertTrue(_universally_true("(x > 0) ==> (x >= 1)"))
        self.assertTrue(_universally_true("q <= v ==> q < v + 1"))
        self.assertTrue(_universally_true("x * (z - 1) == x * (z - 1)"))
        self.assertFalse(_universally_true("x <= y"))
        self.assertFalse(_universally_true("x / 2 <= x"))

    def test_weaker_constant_integer_bounds_are_removed_conservatively(self):
        cleaned, removed = _remove_subsumed_constant_bounds(
            ["x >= 0", "x > 0", "x >= 1", "x <= 10", "x < 10", "x <= n"]
        )

        self.assertEqual(cleaned, ["x > 0", "x < 10", "x <= n"])
        self.assertEqual(removed, 3)

    def test_shared_symbolic_power_is_eliminated_into_polynomial_relation(self):
        derived = _derive_power_free_relations(
            [
                "y == power(z, c)",
                "(z - 1) * x + 1 == power(z, c)",
            ]
        )

        self.assertEqual(len(derived), 1)
        clause = derived[0]["clause"]
        self.assertNotIn("power", clause)
        self.assertIn("x", clause)
        self.assertIn("y", clause)
        self.assertIn("z", clause)
        self.assertEqual(
            derived[0]["sources"],
            [
                "y == power(z, c)",
                "(z - 1) * x + 1 == power(z, c)",
            ],
        )

    def test_reducible_product_from_symbolic_power_is_not_kept(self):
        derived = _derive_power_free_relations(
            [
                "y == power(z, c)",
                "y == \\at(y,LoopEntry) * power(z, c)",
            ]
        )
        self.assertEqual(derived, [])

    def test_guarded_copy_is_removed_when_unconditional_clause_exists(self):
        clauses, removed = _remove_guarded_copies(
            ["x*z - x - y + 1 == 0", "c < k ==> (x*z - x - y + 1 == 0)"]
        )
        self.assertEqual(clauses, ["x*z - x - y + 1 == 0"])
        self.assertEqual(removed, 1)

    def test_fixed_power_is_expanded_but_symbolic_power_is_not_guessed(self):
        rewritten, count = _rewrite_fixed_powers(
            "z == power(x + 1, 3) + power(y, 2)"
        )

        self.assertEqual(count, 2)
        self.assertNotIn("power", rewritten)
        self.assertEqual(rewritten.count("(x + 1)"), 3)
        self.assertEqual(rewritten.count("(y)"), 2)
        self.assertEqual(
            _rewrite_fixed_powers("z == power(x, i)"),
            ("z == power(x, i)", 0),
        )

    def test_loopentry_is_kept_only_for_unknown_roots(self):
        source = """int f(int n) {
          int fixed = 3;
          int seed = unknown();
          int derived = seed + n;
          int i = 0;
          while (i < n) { i++; }
        }"""
        program = parse_program(source)
        invariant = (
            "i >= \\at(i,LoopEntry) && fixed == \\at(fixed,LoopEntry) && "
            "seed >= \\at(seed,LoopEntry) && derived >= \\at(derived,LoopEntry) && "
            "n == \\at(n,LoopEntry)"
        )

        rewritten, changes = _minimize_loop_entry(invariant, program)

        self.assertIn("(0)", rewritten)
        self.assertIn("(3)", rewritten)
        self.assertIn("\\at(seed,LoopEntry)", rewritten)
        self.assertNotIn("\\at(i,LoopEntry)", rewritten)
        self.assertNotIn("\\at(fixed,LoopEntry)", rewritten)
        self.assertNotIn("\\at(derived,LoopEntry)", rewritten)
        self.assertIn("\\at(n,Pre)", rewritten)
        self.assertEqual(changes["loopentry_required_retained"], 1)
        self.assertEqual(changes["loopentry_rewritten"], 4)
        self.assertEqual(_unnecessary_loop_entry_references(rewritten, program), [])

    def test_parameter_overwritten_by_unknown_cannot_use_loopentry(self):
        source = """int f(int n) {
          n = unknown1();
          int i = 0;
          while (i < n) { i++; }
        }"""
        program = parse_program(source)

        rewritten, _ = _minimize_loop_entry("i <= \\at(n,LoopEntry)", program)

        self.assertEqual(rewritten, "i <= \\at(n,LoopEntry)")
        self.assertEqual(_unnecessary_loop_entry_references(rewritten, program), ["n"])

    def test_atomic_loopentry_identity_is_a_tautology(self):
        self.assertTrue(
            _universally_true(
                "x == \\at(x,LoopEntry) + (x - \\at(x,LoopEntry))"
            )
        )

    def test_bitshift_is_supported_by_runtime_and_conservative_dedup(self):
        expression = "p == 1 << i"

        self.assertTrue(eval_predicate(expression, State({"p": 8, "i": 3})))
        self.assertNotEqual(invariant_dedup_key(expression)[0], "raw")
        self.assertFalse(_universally_true(expression))

    def test_clean_rl_source_is_byte_stable_despite_terminal_newline(self):
        full = FULL_SOURCE.replace("  // x tracks completed iterations\n", "")
        visible = strip_postcondition(full) + "\n"

        clean, reconstructed = _sanitize_visible_source(visible, full)

        self.assertEqual(clean, visible)
        self.assertFalse(reconstructed)

    def test_rl_rebuilds_both_prompts_and_hides_target(self):
        visible = _legacy_visible(FULL_SOURCE)
        row = {
            "prompt": [
                {"role": "system", "content": "legacy system"},
                {"role": "user", "content": "Legacy task\nProgram:\n" + visible},
            ],
            "reward_model": {"ground_truth": {"raw_code": FULL_SOURCE}},
        }

        sanitized, stats = sanitize_rl_rows([row])
        turns = sanitized[0]["prompt"]

        self.assertEqual(turns[0]["content"], prompts.system_prompt())
        self.assertIn("can ONLY be used with local variables", turns[0]["content"])
        self.assertEqual(
            turns[1]["content"],
            prompts.GENERATE_PROMPT.format(
                program=strip_postcondition(FULL_SOURCE).rstrip() + "\n"
            ),
        )
        self.assertNotIn("tracks completed iterations", turns[1]["content"])
        self.assertNotIn("assert x == n", turns[1]["content"])
        self.assertEqual(stats["modified_prompts"], 1)
        self.assertEqual(row["prompt"][0]["content"], "legacy system")

    def test_rl_mismatch_falls_back_to_full_archival_source(self):
        visible = "int f(int n) { while (n > 0) { n--; } }\n"

        clean, reconstructed = _sanitize_visible_source(visible, FULL_SOURCE)

        self.assertTrue(reconstructed)
        self.assertTrue(clean.endswith("\n"))
        self.assertIn("while (x < n)", clean)
        self.assertNotIn("assert x == n", clean)
        self.assertNotIn("tracks completed iterations", clean)

    def test_sft_canonicalizes_prompt_and_scrubs_answer(self):
        answer = "\n".join(
            [
                "loop invariant x >= 0;",
                "loop invariant x >= 0;",
                "loop invariant x == power(2, n);",
                "loop invariant x == factorial(n);",
                "loop invariant x == x;",
                "loop invariant ghost >= 0;",
                "loop invariant x > 0 ? x : n;",
            ]
        )
        record = _sft_record(_legacy_visible(FULL_SOURCE), answer)
        original = copy.deepcopy(record)

        sanitized, stats = sanitize_sft_records([record])
        turns = sanitized[0]["conversations"]

        self.assertEqual(turns[0]["value"], prompts.system_prompt())
        self.assertIn("can ONLY be used with local variables", turns[0]["value"])
        self.assertNotIn("tracks completed iterations", turns[1]["value"])
        self.assertEqual(turns[2]["value"], "loop invariant x >= 0;")
        self.assertEqual(stats["removed_clauses"]["duplicate"], 1)
        self.assertEqual(stats["removed_clauses"]["helper_function"], 2)
        self.assertEqual(stats["removed_clauses"]["tautology"], 1)
        self.assertEqual(stats["removed_clauses"]["out_of_scope"], 1)
        self.assertEqual(stats["removed_clauses"]["ternary"], 1)
        self.assertEqual(record, original)

    def test_sft_drops_unsupported_programs_and_empty_answers(self):
        unsupported = _sft_record(
            "void f(void) { float x = 0; while (x < 1) { x++; } }",
            "loop invariant x >= 0;",
        )
        empty = _sft_record(
            strip_postcondition(FULL_SOURCE),
            "loop invariant x == power(2, n);",
        )

        sanitized, stats = sanitize_sft_records([unsupported, empty])

        self.assertEqual(sanitized, [])
        self.assertEqual(stats["dropped_empty_answers"], 1)
        self.assertEqual(sum(stats["dropped_programs"].values()), 1)

    def test_sft_cleaning_is_idempotent(self):
        source = strip_postcondition(FULL_SOURCE)
        records = [_sft_record(source, "loop invariant x >= 0;", prompts.system_prompt())]

        first, _ = sanitize_sft_records(records)
        second, stats = sanitize_sft_records(first)

        self.assertEqual(second, first)
        self.assertEqual(stats["modified_prompts"], 0)
        self.assertEqual(stats["modified_answers"], 0)
        self.assertEqual(stats["removed_clauses"], {})


if __name__ == "__main__":
    unittest.main()
