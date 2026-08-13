from __future__ import annotations

import gzip
import json
from pathlib import Path
import tempfile
import unittest

from experiments.gpt5nano_full832.daikon_adapter import (
    parse_daikon_invariants,
    raw_negative_fields,
    stream_selected_positives,
    translate_daikon_invariant,
)
from rl_pipeline.common.program import parse_program
from rl_pipeline.common.state import State
from rl_pipeline.sampler import ExampleSet


SOURCE = """void f(int n) {
  int x = 0;
  while (x < n) { x++; }
  /*@ assert x == n; */
}
"""


class DaikonAdapterTests(unittest.TestCase):
    def test_positive_payload_is_streamed_with_deterministic_stratification(self):
        payload = {
            "schema_version": 2,
            "positives": [
                {
                    "vars": {"x": index, "n": 10},
                    "pre": {"n": 10},
                    "loop_entry": {"x": 0, "n": 10},
                    "run": 0,
                    "it": index,
                }
                for index in range(10)
            ],
            "negatives": [],
        }
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "sample.json.gz"
            path.write_bytes(
                gzip.compress(
                    json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
                    mtime=0,
                )
            )
            states = stream_selected_positives(path, positive_count=10, limit=4)
        self.assertEqual([state.vars["x"] for state in states], [0, 3, 6, 9])

    def test_scalar_output_translation_rejects_daikon_prose(self):
        self.assertEqual(
            translate_daikon_invariant("x <= pre__n", SOURCE),
            "x <= \\at(n, Pre)",
        )
        self.assertIsNone(translate_daikon_invariant("x one of { 0, 1 }", SOURCE))
        self.assertIsNone(translate_daikon_invariant("unknown == 0", SOURCE))

    def test_parser_reads_only_requested_program_point(self):
        output = """Daikon version 5.8.24
===========================================================================
craft_linear_1:::POINT
x >= 0
x <= pre__n
x one of { 0, 1 }
Exiting Daikon.
"""
        self.assertEqual(
            parse_daikon_invariants(output, "craft_linear_1:::POINT", SOURCE),
            ["x >= 0", "x <= \\at(n, Pre)"],
        )

    def test_raw_negative_score_does_not_filter_candidates(self):
        examples = ExampleSet(
            program=parse_program(SOURCE),
            positives={0: [State(vars={"x": 0, "n": 10}, pre={"n": 10})]},
            negatives={
                0: [
                    State(vars={"x": -1, "n": 10}, pre={"n": 10}),
                    State(vars={"x": 3, "n": 10}, pre={"n": 10}),
                ]
            },
            neg_groups={0: [[0], [1]]},
        )
        fields = raw_negative_fields(examples, ["x >= 0"], verified=False)
        self.assertEqual(fields["rejected_negative_count"], 1)
        self.assertEqual(fields["negative_rejection_score"], 0.5)
        self.assertEqual(fields["score_surviving_invariants"], ["x >= 0"])


if __name__ == "__main__":
    unittest.main()
