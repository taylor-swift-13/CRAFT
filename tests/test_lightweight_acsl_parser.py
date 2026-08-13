from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from rl_pipeline.common.acsl_parser import parse_scalar_invariant
from rl_pipeline.common.program import parse_program
from rl_pipeline.common.state import State, first_falsifying_state
from rl_pipeline.reward.filters import HoudiniFilter, PreFramaFilter, auto_filter


class LightweightAcslParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.program = parse_program(
            "int f(int n) { int a = unknown(); int i = 0; "
            "while (i < n) { i++; } }"
        )

    def test_accepts_complete_prompt_grammar(self):
        valid = [
            "0 <= i",
            "2 * n == i * (i - 1)",
            "i > 0 ==> n >= i",
            "i % 5 == 0",
            "n == \\at(n,Pre)",
            "a >= \\at(a,LoopEntry)",
            "n == 1 << i",
            "i >= 0 && i <= n",
        ]
        for expression in valid:
            with self.subTest(expression=expression):
                self.assertTrue(
                    parse_scalar_invariant(expression, self.program).valid
                )

    def test_rejects_common_model_errors(self):
        invalid = [
            "i > 0 ? i : n",
            "i == power(2, n)",
            "i == \\old(i)",
            "i == (int)n",
            "i == n + (i > 0)",
            "ghost >= 0",
            "i >= 0 /* placeholder */",
            "i >= 0 +",
        ]
        for expression in invalid:
            with self.subTest(expression=expression):
                self.assertFalse(
                    parse_scalar_invariant(expression, self.program).valid
                )

    def test_at_labels_follow_the_model_facing_contract(self):
        valid = [r"n == \at(n,Pre)", r"a == \at(a,LoopEntry)"]
        invalid = [r"i == \at(i,Pre)", r"n == \at(n,LoopEntry)"]

        for expression in valid:
            with self.subTest(expression=expression):
                self.assertTrue(parse_scalar_invariant(expression, self.program).valid)
        for expression in invalid:
            with self.subTest(expression=expression):
                self.assertFalse(parse_scalar_invariant(expression, self.program).valid)

    def test_pre_frama_filter_deduplicates_before_positive_evaluation(self):
        positives = [State(vars={"n": 2, "a": 7, "i": 0})]
        with mock.patch(
            "rl_pipeline.reward.filters.first_falsifying_state",
            wraps=first_falsifying_state,
        ) as evaluate:
            survivors = PreFramaFilter().filter(
                self.program,
                0,
                ["i >= 0", "0 <= i", "i <= n", "i > n", "i >= 0 +"],
                positives,
            )

        self.assertEqual(survivors, ["i >= 0", "i <= n"])
        self.assertEqual(evaluate.call_count, 3)

    def test_auto_filter_places_one_lightweight_stage_before_houdini(self):
        with mock.patch(
            "rl_pipeline.reward.filters.frama_c_available", return_value=True
        ):
            selected = auto_filter()

        self.assertEqual(
            [stage.name for stage in selected.stages],
            ["pre-frama", "houdini"],
        )
        self.assertFalse(selected.stages[1].prefilter_positives)
        self.assertFalse(selected.stages[1].lightweight_prefilter)

    def test_syntax_scrub_filters_and_deduplicates_before_frama_c(self):
        calls = []

        def fake_frama(command, **_kwargs):
            calls.append(command)
            with open(command[-1], encoding="utf-8") as source_file:
                annotated = source_file.read()
            self.assertEqual(annotated.count("loop invariant"), 2)
            self.assertIn("loop invariant i >= 0;", annotated)
            self.assertIn("loop invariant i <= n;", annotated)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_frama):
            survivors = HoudiniFilter()._syntax_scrub(
                self.program,
                0,
                ["i >= 0", "0 <= i", "i <= n", "i >= 0 +"],
            )

        self.assertEqual(survivors, ["i >= 0", "i <= n"])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
