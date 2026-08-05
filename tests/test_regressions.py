from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from rl_pipeline.common import prompts
from rl_pipeline.common.program import parse_program, strip_postcondition
from rl_pipeline.common.state import (
    MAX_INVARIANTS_PER_RESPONSE,
    State,
    dedup_normalized,
    eval_predicate,
    extract_invariants,
    first_falsifying_state,
    invariant_dedup_key,
)
from rl_pipeline.eval.mislabel_audit import discover_programs
from rl_pipeline.inference import InferenceFramework, MockRolloutProvider
from rl_pipeline.inference import inference as inference_module
from rl_pipeline.reward import annotate
from rl_pipeline.reward.filters import HoudiniFilter, PositiveFilter
from rl_pipeline.reward.reward_calculator import RewardCalculator
from rl_pipeline.reward.refine import refine_group_delta_base
from rl_pipeline.reward import service
from rl_pipeline.reward import io as reward_io
from rl_pipeline.reward.score_file import score_file
from rl_pipeline.sampler import ExampleSampler, ExampleSet
from rl_pipeline.sampler import cexec
from experiments.gpt5nano_full832 import common as full832_common
from experiments.gpt5nano_full832 import native as full832_native
from experiments.gpt5nano_full832 import run as full832_run
from experiments.gpt5nano_full832 import samples as full832_samples
from src.config import LLMConfig
from src.llm import OpenAILLM


ROOT = Path(__file__).resolve().parents[1]


class LLMRegressionTests(unittest.TestCase):
    @mock.patch("src.llm.openai.OpenAI")
    def test_qwen3_api_disables_thinking_for_non_streaming_calls(
        self, openai_cls
    ):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="loop invariant v <= 30;",
                        refusal=None,
                    ),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
        create = openai_cls.return_value.chat.completions.create
        create.return_value = response
        model = OpenAILLM(LLMConfig(api_model="qwen3-8b", api_key="test"))

        self.assertEqual(
            model.generate_response("program"),
            "loop invariant v <= 30;",
        )
        self.assertEqual(
            create.call_args.kwargs["extra_body"],
            {"enable_thinking": False},
        )


class PredicateRegressionTests(unittest.TestCase):
    def test_model_response_invariant_parser_can_enforce_twenty_line_cap(self):
        response = "\n".join(
            f"loop invariant x >= {-index};"
            for index in range(25)
        )

        self.assertEqual(len(extract_invariants(response)), 25)
        self.assertEqual(
            len(extract_invariants(
                response, max_invariants=MAX_INVARIANTS_PER_RESPONSE
            )),
            20,
        )

    def test_positive_filter_checks_every_reachable_state(self):
        program = parse_program(
            "void f(void) { int x = 0; while (x < 10000) { x++; } }"
        )
        positives = [State(vars={"x": value}) for value in range(10000)]

        witness = first_falsifying_state("x != 4501", positives)

        self.assertIsNotNone(witness)
        self.assertEqual(witness.vars["x"], 4501)
        self.assertEqual(
            PositiveFilter().filter(program, 0, ["x != 4501"], positives),
            [],
        )

    def test_nested_implication_and_equivalence_work_scalar_and_vector(self):
        expression = (
            "(x > 0 ==> y > 0) && "
            "((x == 1) <==> (y == 1)) && z == 0"
        )
        states = [
            State(vars={"x": 0, "y": 0, "z": 0}),
            State(vars={"x": 1, "y": 1, "z": 0}),
            State(vars={"x": 1, "y": 0, "z": 0}),
        ]

        self.assertEqual(
            [eval_predicate(expression, state) for state in states],
            [True, True, False],
        )
        self.assertIs(first_falsifying_state(expression, states), states[2])

    def test_power_and_factorial_work_for_scalar_vector_and_positive_filter(self):
        expression = "p == power(k, i) && f == factorial(i)"
        states = [
            State(vars={"p": 1, "k": 3, "i": 0, "f": 1}),
            State(vars={"p": 27, "k": 3, "i": 3, "f": 6}),
            State(vars={"p": 27, "k": 3, "i": 3, "f": 5}),
        ]

        self.assertEqual(
            [eval_predicate(expression, state) for state in states],
            [True, True, False],
        )
        self.assertIs(first_falsifying_state(expression, states), states[2])
        program = parse_program(
            "void f(int k) { int p = 1, i = 0, f = 1; "
            "while (i < 3) { p *= k; i++; f *= i; } }"
        )
        self.assertEqual(
            PositiveFilter().filter(
                program, 0, [expression], states[:2]
            ),
            [expression],
        )

    def test_pre_and_loop_entry_labels_have_distinct_state_snapshots(self):
        expression = (
            r"\at(n,Pre) == 10 && \at(v,LoopEntry) == 3 && v == 5"
        )
        states = [
            State(
                vars={"n": 8, "v": 5},
                pre={"n": 10},
                loop_entry={"n": 8, "v": 3},
            ),
            State(
                vars={"n": 8, "v": 6},
                pre={"n": 10},
                loop_entry={"n": 8, "v": 3},
            ),
        ]

        self.assertIs(eval_predicate(expression, states[0]), True)
        self.assertIs(eval_predicate(expression, states[1]), False)
        self.assertIs(first_falsifying_state(expression, states), states[1])

    def test_positive_dedup_preserves_distinct_pre_values(self):
        positives = [
            State(vars={"n": 0}, pre={"n": 65}),
            State(vars={"n": 0}, pre={"n": 0}),
        ]

        deduplicated = ExampleSampler._dedup(positives)

        self.assertEqual(deduplicated, positives)
        program = parse_program("void f(int n) { while (n > 0) { n--; } }")
        invariant = r"n == 0 ==> \at(n,Pre) == 65"
        self.assertEqual(
            PositiveFilter().filter(program, 0, [invariant], deduplicated),
            [],
        )


class ParserAndAnnotationRegressionTests(unittest.TestCase):
    def test_strip_postcondition_keeps_requires_in_shared_block(self):
        source = (
            "/*@ requires n >= 0; ensures \\result == 0; */\n"
            "int f(int n) { while (n > 0) { n--; } return n; }"
        )

        stripped = strip_postcondition(source)

        self.assertIn("requires n >= 0;", stripped)
        self.assertNotIn("ensures", stripped)
        self.assertNotIn(r"\result", stripped)

        line_source = (
            "//@ requires n >= 0; ensures \\result == 0;\n"
            "int g(int n) { while (n > 0) { n--; } return n; }"
        )
        line_stripped = strip_postcondition(line_source)
        self.assertIn("requires n >= 0;", line_stripped)
        self.assertNotIn("ensures", line_stripped)
        line_program = parse_program(line_source)
        self.assertEqual(line_program.requires, "n >= 0")
        self.assertEqual(line_program.post, r"\result == 0")

    def test_strip_postcondition_removes_complete_quantified_targets(self):
        source = (
            "/*@\n"
            "  requires \\forall integer k; k >= 0 ==> n >= 0;\n"
            "  ensures \\forall integer k; k >= 0 ==> \\result <= k;\n"
            "  assigns \\nothing;\n"
            "*/\n"
            "int f(int n) {\n"
            "  int x = 0;\n"
            "  while (x < n) { x++; }\n"
            "  /*@ assert \\let limit = n; \\forall integer i; "
            "0 <= i < limit ==> x >= i; */\n"
            "  return x;\n"
            "}\n"
        )

        stripped = strip_postcondition(source)
        original = parse_program(source)
        masked = parse_program(stripped)

        self.assertIn(r"requires \forall integer k; k >= 0 ==> n >= 0;", stripped)
        self.assertIn(r"assigns \nothing;", stripped)
        self.assertNotIn("ensures", stripped)
        self.assertNotIn("assert", stripped)
        self.assertNotIn(r"\result <= k", stripped)
        self.assertNotIn("x >= i", stripped)
        self.assertEqual(
            original.requires,
            r"\forall integer k; k >= 0 ==> n >= 0",
        )
        self.assertEqual(
            original.post,
            r"\let limit = n; \forall integer i; 0 <= i < limit ==> x >= i",
        )
        self.assertEqual(masked.post, "")

    def test_strip_postcondition_removes_executable_assertions(self):
        source = (
            "void f(int x) {\n"
            "  while (x > 0) { x--; }\n"
            "  if (x == 0) assert(x == 7); else __VERIFIER_assert(x < 0);\n"
            "}\n"
        )

        stripped = strip_postcondition(source)

        self.assertNotIn("assert(", stripped)
        self.assertNotIn("__VERIFIER_assert", stripped)
        self.assertEqual(stripped.count("((void)0);"), 2)
        self.assertEqual(stripped.count("\n"), source.count("\n"))

    def test_strip_postcondition_neutralizes_error_target_label(self):
        source = (
            "void f(int x) {\n"
            "  while (x > 0) { if (x == 2) goto ERROR; x--; }\n"
            "  return;\n"
            "ERROR:\n"
            "  //@ assert \\false;\n"
            "}\n"
        )

        stripped = strip_postcondition(source)

        self.assertNotIn("ERROR", stripped)
        self.assertNotIn(r"\false", stripped)
        self.assertEqual(stripped.count("__loopgym_label_0"), 2)

    def test_parser_skips_helper_before_loop_function(self):
        source = (
            "int unknown(void) { return 0; }\n"
            "void target(void) { int x = 0; while (x < 1) { x++; } }"
        )

        program = parse_program(source)

        self.assertEqual(program.func_name, "target")
        self.assertEqual(program.loop.guard, "x < 1")

    def test_prefix_and_postfix_updates_are_loop_assigns(self):
        source = (
            "void f(void) { int x = 0; int y = 3; "
            "while (x < y) { x++; --y; } }"
        )
        program = parse_program(source)

        annotated = annotate.build_annotated(program, ["x <= y + 1"])

        self.assertIn("loop assigns x, y;", annotated)

    def test_loop_assigns_excludes_block_scoped_integer_locals(self):
        source = (
            "void f(int n) { while (n > 0) { "
            "int __n = n; n--; __n++; } }"
        )
        program = parse_program(source)

        annotated = annotate.build_annotated(program, ["n >= 0"])

        self.assertIn("loop assigns n;", annotated)
        self.assertNotIn("loop assigns __n", annotated)

    def test_logic_function_definitions_are_injected_only_when_referenced(self):
        source = (
            "int unknown(void); "
            "void f(int k) { int p = 1, i = 0, fact = 1; "
            "while (unknown()) { p *= k; i++; fact *= i; } }"
        )
        program = parse_program(source)

        plain = annotate.build_annotated(program, ["i >= 0"])
        powered = annotate.build_annotated(
            program, ["p == power(k, i)"]
        )
        both = annotate.build_annotated(
            program,
            ["p == power(k, i)", "fact == factorial(i)"],
        )

        self.assertNotIn("logic integer power", plain)
        self.assertNotIn("logic integer factorial", plain)
        self.assertIn("logic integer power", powered)
        self.assertNotIn("logic integer factorial", powered)
        self.assertIn("logic integer power", both)
        self.assertIn("logic integer factorial", both)
        self.assertEqual(both.count("int unknown(void);"), 1)
        self.assertLess(
            both.index("logic integer power"),
            both.index("int unknown(void);"),
        )

    def test_parser_accepts_scalar_integer_type_combinations(self):
        source = (
            "static unsigned long global_count; "
            "void f(const unsigned long long limit, signed char step, _Bool enabled) { "
            "long long index = 0; unsigned short delta = 1; "
            "while (index < limit) { index += delta; } }"
        )

        program = parse_program(source)

        self.assertEqual(
            program.pre_vars,
            ["global_count", "limit", "step", "enabled", "index", "delta"],
        )
        self.assertEqual(
            program.unsigned_vars,
            ["global_count", "limit", "delta"],
        )
        self.assertEqual(dict(program.local_inits)["index"], "0")
        self.assertEqual(dict(program.local_inits)["delta"], "1")

    def test_parenthesized_initializers_and_globals_are_tracked(self):
        source = (
            "unsigned int g; void f(int n) { "
            "int k = n % (g + 1); int q = 4 * (n - g); "
            "while (g < n) { g++; k = q; } }"
        )

        program = parse_program(source)

        self.assertEqual(program.pre_vars, ["g", "n", "k", "q"])
        self.assertIn("g", program.unsigned_vars)
        self.assertEqual(dict(program.local_inits)["k"], "n % (g + 1)")
        self.assertEqual(dict(program.local_inits)["q"], "4 * (n - g)")

    def test_unsupported_loop_shapes_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError, "for loops are not supported"):
            parse_program("void f(void) { for (int i = 0; i < 3; i++) {} }")
        with self.assertRaisesRegex(ValueError, "multiple loops are not supported"):
            parse_program(
                "void f(void) { int x = 0; while (x < 1) { x++; } "
                "while (x < 2) { x++; } }"
            )
        with self.assertRaisesRegex(ValueError, "scalar integer parameters"):
            parse_program("void f(int *p) { while (*p) { (*p)--; } }")

    def test_state_render_includes_pre_values(self):
        rendered = State(vars={"n": 0}, pre={"n": 65}).render()

        self.assertEqual(rendered, "n == 0; Pre: n == 65")


class Full832ExperimentRegressionTests(unittest.TestCase):
    def test_fixed_sample_encoding_is_deterministic_and_round_trips(self):
        source = "void f(int x) { while (x > 0) { x--; } //@ assert x == 0;\n}"
        hidden = strip_postcondition(source)
        task = full832_common.Task(
            suite="linear",
            case_id="fixture",
            source_path=Path("/fixture.c"),
            source_sha256=hashlib.sha256(source.encode()).hexdigest(),
            hidden_source=hidden,
            hidden_source_sha256=hashlib.sha256(hidden.encode()).hexdigest(),
        )
        examples = ExampleSet(
            program=parse_program(hidden),
            positives={0: [State(vars={"x": 1}, pre={"x": 1}, run=0, it=0)]},
            negatives={0: [State(vars={"x": -1}, pre={"x": 1})]},
            neg_groups={0: [[0]]},
            stats={0: {"n_pos": 1, "n_neg": 1}},
        )
        payload = full832_samples._payload(task, examples)
        compressed_a, content_hash_a = full832_samples._encode_payload(payload)
        compressed_b, content_hash_b = full832_samples._encode_payload(payload)

        self.assertEqual(compressed_a, compressed_b)
        self.assertEqual(content_hash_a, content_hash_b)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json.gz"
            path.write_bytes(compressed_a)
            manifest_row = {
                "sample_artifact": str(path),
                "sample_content_sha256": content_hash_a,
            }
            restored = full832_samples.load_sample(task, manifest_row)

        self.assertEqual(restored.pos(0), examples.pos(0))
        self.assertEqual(restored.neg(0), examples.neg(0))
        self.assertEqual(restored.groups(0), [[0]])

    def test_archived_v1_fixed_sample_round_trips(self):
        source = "void f(int x) { while (x > 0) { x--; } //@ assert x == 0;\n}"
        hidden = strip_postcondition(source)
        task = full832_common.Task(
            suite="linear",
            case_id="archived-fixture",
            source_path=Path("/archived-fixture.c"),
            source_sha256=hashlib.sha256(source.encode()).hexdigest(),
            hidden_source=hidden,
            hidden_source_sha256=hashlib.sha256(hidden.encode()).hexdigest(),
        )
        examples = ExampleSet(
            program=parse_program(hidden),
            positives={0: [State(vars={"x": 1}, pre={"x": 1}, run=0, it=0)]},
            negatives={0: [State(vars={"x": -1}, pre={"x": 1})]},
            neg_groups={0: [[0]]},
            stats={0: {"n_pos": 1, "n_neg": 1}},
        )
        payload = full832_samples._payload(task, examples)
        payload["schema_version"] = 1
        compressed, content_hash = full832_samples._encode_payload(payload)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json.gz"
            path.write_bytes(compressed)
            restored = full832_samples.load_sample(task, {
                "schema_version": 1,
                "sample_artifact": str(path),
                "sample_content_sha256": content_hash,
            })

        self.assertEqual(restored.pos(0), examples.pos(0))
        self.assertEqual(restored.neg(0), examples.neg(0))
        self.assertEqual(restored.groups(0), [[0]])

    def test_manifest_is_complete_and_every_model_source_hides_target(self):
        tasks = full832_common.discover_tasks()

        self.assertEqual(len(tasks), 832)
        self.assertEqual(
            {suite: sum(task.suite == suite for task in tasks) for suite in {
                "linear", "NLA_lipus", "Loopy"
            }},
            {"linear": 316, "NLA_lipus": 50, "Loopy": 466},
        )
        for task in tasks:
            full832_common.assert_target_hidden(
                task.source_path.read_text(errors="ignore"),
                task.hidden_source,
            )

    def test_loopgym_batch_runner_hides_target_before_model_call(self):
        source = (
            "void f(int x) {\n"
            "  while (x > 0) { x--; }\n"
            "  //@ assert x == 0;\n"
            "}\n"
        )
        hidden = strip_postcondition(source)
        observed = {}

        class FakeFramework:
            def __init__(self, framework_source, rollout_provider, **_kwargs):
                observed["source"] = framework_source
                self.provider = rollout_provider

            def run(self):
                self.provider(parse_program(observed["source"]), 1)
                return SimpleNamespace(
                    rollouts=[[]],
                    final_invariants=[],
                    verified=False,
                    reroll_count=0,
                )

        class FakeRecorder:
            def __init__(self):
                self.records = []

            def chat(self, prompt):
                observed["prompt"] = prompt
                return ""

            @staticmethod
            def usage():
                return {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "api_call_count": 0,
                    "token_accounting": "exact",
                }

        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "fixture.c"
            source_path.write_text(source)
            task = full832_common.Task(
                suite="linear",
                case_id="fixture",
                source_path=source_path,
                source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                hidden_source=hidden,
                hidden_source_sha256=hashlib.sha256(hidden.encode()).hexdigest(),
            )
            with mock.patch.object(
                full832_run, "RecordingChat", return_value=FakeRecorder()
            ), mock.patch.object(full832_run, "InferenceFramework", FakeFramework):
                full832_run._run_loopgym(task, Path(directory))

        self.assertEqual(observed["source"], source)
        self.assertNotIn("assert", observed["prompt"])
        self.assertNotIn("x == 0", observed["prompt"])


class SyntaxScrubRegressionTests(unittest.TestCase):
    def test_bad_superstring_does_not_remove_valid_invariant(self):
        source = "void f(void) { int x = 0; while (x < 2) { x++; } }"
        program = parse_program(source)

        def fake_frama(command, **_kwargs):
            path = Path(command[-1])
            lines = path.read_text(encoding="utf-8").splitlines()
            bad_line = next(
                (index for index, line in enumerate(lines, 1)
                 if line.strip() == "loop invariant x >= 0 +;"),
                None,
            )
            if bad_line is not None:
                return SimpleNamespace(
                    returncode=1,
                    stdout=f"{path}:{bad_line}: user error: invalid expression\n",
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_frama):
            survivors = HoudiniFilter()._syntax_scrub(
                program, 0, ["x >= 0", "x >= 0 +"]
            )

        self.assertEqual(survivors, ["x >= 0"])


class RewardPatchRegressionTests(unittest.TestCase):
    class _IdentityFilter:
        name = "identity"

        @staticmethod
        def filter(_program, _loop_idx, invariants, _positives=None):
            return list(invariants)

    def test_system_prompt_uses_canonical_flat_rule_list(self):
        canonical = prompts.system_prompt()
        self.assertIn("## LOOP INVARIANT DEFINITION", canonical)
        self.assertIn("## RULES", canonical)
        self.assertNotIn("### UNKNOWN", canonical)
        self.assertNotIn("### Invariant content", canonical)
        self.assertNotIn("### ACSL syntax and scope", canonical)
        self.assertIn("## OUTPUT", canonical)
        self.assertEqual(canonical, prompts.system_prompt())

    def test_conservative_semantic_dedup_merges_only_whitelisted_forms(self):
        equivalent_pairs = [
            ("x >= 0", "0 <= x"),
            ("x >= 0", "!(x < 0)"),
            ("x == y", "y == x"),
            ("x + y == n", "n == y + x"),
            ("x + 0 == n", "x == n"),
            ("a && (b && a)", "(a && b) && a"),
            ("a ==> b", "!a || b"),
            (r"\at(j,LoopEntry) <= j", r"j >= \at(j,LoopEntry)"),
        ]
        for left, right in equivalent_pairs:
            with self.subTest(left=left, right=right):
                self.assertEqual(
                    invariant_dedup_key(left),
                    invariant_dedup_key(right),
                )

        distinct_pairs = [
            ("x < 0", "x <= 0"),
            ("x + y == n", "x == n - y"),
            ("(x + y) + z == n", "x + (y + z) == n"),
            ("2 * x == 2 * y", "x == y"),
            ("a && b", "b && a"),
            ("x / x == 1", "x != 0"),
        ]
        for left, right in distinct_pairs:
            with self.subTest(left=left, right=right):
                self.assertNotEqual(
                    invariant_dedup_key(left),
                    invariant_dedup_key(right),
                )

        self.assertEqual(
            dedup_normalized(["x >= 0", "0 <= x", "y == y"]),
            ["x >= 0", "y == y"],
        )

    def test_reward_public_api_contains_no_removed_credit_fields(self):
        if hasattr(service.RewardRequest, "model_fields"):
            request_fields = service.RewardRequest.model_fields
        else:
            request_fields = service.RewardRequest.__fields__
        self.assertNotIn("w_surv", request_fields)
        self.assertFalse(hasattr(RewardCalculator(), "w_surv"))

    def test_semantic_dedup_fixed_seed_metamorphic_pairs(self):
        rng = random.Random(20260805)
        names = ["x", "y", "z", "n", "i", "j"]

        def atom():
            return rng.choice(names + [str(rng.randint(-4, 4))])

        for index in range(5000):
            left, right, third = atom(), atom(), atom()
            case = index % 5
            if case == 0:
                original = f"{left} == {right}"
                transformed = f"{right} == {left}"
            elif case == 1:
                original = f"{left} >= {right}"
                transformed = f"{right} <= {left}"
            elif case == 2:
                original = f"{left} + {right} == {third}"
                transformed = f"{right} + {left} == {third}"
            elif case == 3:
                original = f"({left} && {right}) && {third}"
                transformed = f"{left} && ({right} && {third})"
            else:
                original = f"{left} ==> {right}"
                transformed = f"!{left} || {right}"
            with self.subTest(index=index):
                self.assertEqual(
                    invariant_dedup_key(original),
                    invariant_dedup_key(transformed),
                )

    def test_duplicate_penalty_does_not_charge_unique_zero_coverage_clauses(self):
        source = "void f(void) { int x = 0; while (x < 1) { x++; } }"
        program = parse_program(source)
        examples = ExampleSet(
            program=program,
            positives={0: [State(vars={"x": 0})]},
            negatives={
                0: [
                    State(vars={"x": -2}),
                    State(vars={"x": -1}),
                    State(vars={"x": 2}),
                ]
            },
            neg_groups={0: [[0], [1], [2]]},
        )
        rollout = {
            "invariants": ["x == x", "x >= -1", "x >= 0", "x >= 0"]
        }

        result = RewardCalculator(
            invariant_filter=self._IdentityFilter(), n_jobs=1
        ).compute(source, [rollout], examples=examples)
        score = result.rollouts[0]

        self.assertEqual(score.base, 2 / 3)
        self.assertEqual(score.redundant_clauses, 1)
        self.assertAlmostEqual(
            score.reward,
            2 / 3
            - 0.02,
        )

    def test_zero_negative_fallback_uses_binary_frama_c_validation(self):
        source = "void f(void) { int x = 0; while (x < 1) { x++; } }"
        examples = ExampleSet(
            program=parse_program(source),
            positives={0: []},
            negatives={0: []},
            neg_groups={0: []},
        )

        class SelectiveFilter:
            name = "cascade(positive->houdini)"

            @staticmethod
            def filter(_program, _loop_idx, invariants, _positives=None):
                return [inv for inv in invariants if inv == "x >= 0"]

        result = RewardCalculator(
            invariant_filter=SelectiveFilter(), n_jobs=1,
        ).compute(
            source,
            [["x >= 0"], ["x == 42"], []],
            examples=examples,
        )

        self.assertEqual(result.batch_score, 0.0)
        self.assertEqual(
            [rollout.reward for rollout in result.rollouts],
            [1.0, 0.0, 0.0],
        )
        self.assertEqual(
            result.to_dict()["reward_mode"],
            "binary_frama_c_validation",
        )
        self.assertNotIn("survival_bonus", result.to_dict())
        self.assertNotIn("marginal", result.to_dict())

    def test_default_reward_adds_positive_hardness_bonus(self):
        source = "void f(void) { int x = 0; while (x < 1) { x++; } }"
        examples = ExampleSet(
            program=parse_program(source),
            positives={0: [State(vars={"x": 0})]},
            negatives={
                0: [
                    State(vars={"x": -1}),
                    State(vars={"x": 1}),
                ]
            },
            neg_groups={0: [[0], [1]]},
        )

        calculator = RewardCalculator(
            invariant_filter=self._IdentityFilter(), n_jobs=1
        )
        result = calculator.compute(
            source,
            [["x == 0"], ["x <= 0"]],
            examples=examples,
        )
        strong, overlapping = result.rollouts

        self.assertEqual(calculator.w_base, 1.0)
        self.assertEqual(calculator.w_hard, 0.3)
        self.assertEqual(strong.base, 1.0)
        self.assertEqual(strong.hard_bonus, 0.25)
        self.assertEqual(strong.redundant_clauses, 0)
        self.assertAlmostEqual(
            strong.reward,
            1.0 + 0.3 * 0.25,
        )
        self.assertEqual(overlapping.base, 0.5)
        self.assertEqual(overlapping.hard_bonus, 0.0)
        self.assertEqual(overlapping.redundant_clauses, 0)
        self.assertEqual(overlapping.redundancy_penalty, 0.0)
        self.assertAlmostEqual(overlapping.reward, 0.5)

    def test_response_cap_truncates_and_penalizes_overflow_lines(self):
        source = "void f(void) { int x = 0; while (x < 1) { x++; } }"
        examples = ExampleSet(
            program=parse_program(source),
            positives={0: [State(vars={"x": 0})]},
            negatives={0: [State(vars={"x": -100})]},
            neg_groups={0: [[0]]},
        )
        rollout = [f"x >= {-index}" for index in range(25)]

        score = RewardCalculator(
            invariant_filter=self._IdentityFilter(), n_jobs=1
        ).compute(source, [rollout], examples=examples).rollouts[0]

        self.assertEqual(score.generated, 25)
        self.assertEqual(score.accepted, 20)
        self.assertEqual(score.overflow, 5)
        self.assertEqual(score.overflow_penalty, 0.25)
        self.assertEqual(len(score.invariants), 20)
        self.assertNotIn("x >= -24", score.invariants)

    def test_overlapping_unique_clauses_are_not_penalized(self):
        source = "void f(void) { int x = 0; while (x < 1) { x++; } }"
        examples = ExampleSet(
            program=parse_program(source),
            positives={0: [State(vars={"x": 0})]},
            negatives={
                0: [
                    State(vars={"x": -2}),
                    State(vars={"x": -1}),
                ]
            },
            neg_groups={0: [[0], [1]]},
        )
        calculator = RewardCalculator(
            invariant_filter=self._IdentityFilter(), n_jobs=1
        )

        score = calculator.compute(
            source,
            [["x >= 0", "x >= -1"]],
            examples=examples,
        ).rollouts[0]

        self.assertEqual(score.redundant_clauses, 0)
        self.assertEqual(score.redundancy_penalty, 0.0)

        reverse = calculator.compute(
            source,
            [["x >= -1", "x >= 0"]],
            examples=examples,
        ).rollouts[0]
        self.assertEqual(reverse.redundant_clauses, 0)
        self.assertEqual(reverse.redundancy_penalty, 0.0)

    def test_supporting_clause_enables_standalone_coverage_without_penalty(self):
        source = "void f(void) { int x = 0; while (x < 1) { x++; } }"
        examples = ExampleSet(
            program=parse_program(source),
            positives={0: [State(vars={"x": 0})]},
            negatives={0: [State(vars={"x": -1})]},
            neg_groups={0: [[0]]},
        )

        class DependencyFilter:
            @staticmethod
            def filter(_program, _loop_idx, invariants, _positives=None):
                invs = set(invariants)
                if {"x >= 0", "x == x"} <= invs:
                    return list(invariants)
                return [inv for inv in invariants if inv == "x == x"]

        score = RewardCalculator(
            invariant_filter=DependencyFilter(), n_jobs=1
        ).compute(
            source, [["x >= 0", "x == x"]], examples=examples
        ).rollouts[0]

        # x == x rejects no state itself, but lets x >= 0 survive Houdini.
        self.assertEqual(score.base, 1.0)
        self.assertEqual(score.reward, 1.0)
        self.assertEqual(score.redundant_clauses, 0)
        self.assertEqual(score.redundancy_penalty, 0.0)

    def test_unique_zero_coverage_clause_is_not_penalized(self):
        source = "void f(void) { int x = 0; while (x < 1) { x++; } }"
        examples = ExampleSet(
            program=parse_program(source),
            positives={0: [State(vars={"x": 0})]},
            negatives={0: [State(vars={"x": -1})]},
            neg_groups={0: [[0]]},
        )
        score = RewardCalculator(
            invariant_filter=self._IdentityFilter(), n_jobs=1
        ).compute(
            source, [["x >= 0", "x == x"]], examples=examples
        ).rollouts[0]

        self.assertEqual(score.redundant_clauses, 0)
        self.assertEqual(score.redundancy_penalty, 0.0)

    def test_non_surviving_clauses_are_not_counted_as_redundant(self):
        source = "void f(void) { int x = 0; while (x < 1) { x++; } }"
        examples = ExampleSet(
            program=parse_program(source),
            positives={0: [State(vars={"x": 0})]},
            negatives={0: [State(vars={"x": -1})]},
            neg_groups={0: [[0]]},
        )

        class SelectiveFilter:
            @staticmethod
            def filter(_program, _loop_idx, invariants, _positives=None):
                return [inv for inv in invariants if inv == "x >= 0"]

        score = RewardCalculator(
            invariant_filter=SelectiveFilter(), n_jobs=1
        ).compute(
            source, [["x >= 0", "x == 42"]], examples=examples
        ).rollouts[0]

        self.assertEqual(score.survivors, ["x >= 0"])
        self.assertEqual(score.redundant_clauses, 0)
        self.assertEqual(score.redundancy_penalty, 0.0)

    def test_refine_reward_caps_response_and_returns_trainable_overflow_penalty(self):
        source = "void f(void) { int x = 0; while (x < 1) { x++; } }"
        examples = ExampleSet(
            program=parse_program(source),
            positives={0: [State(vars={"x": 0})]},
            negatives={0: [State(vars={"x": -100})]},
            neg_groups={0: [[0]]},
        )
        calculator = RewardCalculator(
            invariant_filter=self._IdentityFilter(),
            w_base=1.0,
            w_redundancy=0.0,
            w_overflow=0.0,
            n_jobs=1,
        )

        result = refine_group_delta_base(
            source,
            [],
            [[f"x >= {-index}" for index in range(25)]],
            examples=examples,
            calculator=calculator,
        )

        self.assertEqual(result["delta_base"], [1.0])
        self.assertEqual(result["generated"], [25])
        self.assertEqual(result["accepted"], [20])
        self.assertEqual(result["overflow"], [5])
        self.assertEqual(result["overflow_penalty"], [0.25])
        self.assertEqual(result["refine_rewards"], [0.75])


@unittest.skipUnless(shutil.which("gcc"), "gcc is required for sampler tests")
class SamplerIntegrationRegressionTests(unittest.TestCase):
    def test_escape_uses_only_nearest_step_per_axis_and_direction(self):
        source = "void f(void) { int x = 0; while (x < 10) { x++; } }"
        sampler = ExampleSampler(source, n_runs=1)
        positives = [
            State(vars={"x": value}) for value in range(11)
        ]

        candidates = sampler._escape_negatives(
            ["x"], [State(vars={"x": 0})], positives
        )

        self.assertEqual(
            [candidate.state.vars["x"] for candidate in candidates],
            [13, -5],
        )

    def test_unknown_initialized_local_retains_loop_entry_snapshot(self):
        source = (
            "int unknown(void); "
            "void f(void) { int v = unknown(); int i = 0; "
            "int p = v; int fact = 1; "
            "while (i < 3) { p *= 2; i++; fact *= i; } }"
        )
        invariants = [
            r"p == \at(v,LoopEntry) * power(2, i)",
            "fact == factorial(i)",
            "0 <= i && i <= 3",
        ]

        examples = ExampleSampler(source, n_runs=4).sample()

        self.assertGreater(len(examples.pos(0)), 0)
        self.assertTrue(all(state.loop_entry for state in examples.pos(0)))
        self.assertTrue(all(
            eval_predicate(invariant, state) is True
            for state in examples.pos(0)
            for invariant in invariants
        ))
        self.assertTrue(all(
            state.loop_entry
            for state in examples.neg(0)
        ))

        class IdentityFilter:
            @staticmethod
            def filter(_program, _loop_idx, candidates, _positives=None):
                return list(candidates)

        score = RewardCalculator(
            invariant_filter=IdentityFilter(), n_jobs=1
        ).compute(
            source, [invariants], examples=examples
        ).rollouts[0]

        self.assertGreater(len(examples.groups(0)), 0)
        self.assertGreater(score.rejected, 0)
        self.assertGreater(score.base, 0.5)

    def test_linear_107_terminal_relations_reward_stronger_invariant(self):
        source = (ROOT / "src/input/linear/107.c").read_text(encoding="utf-8")
        examples = ExampleSampler(source).sample()
        stats = examples.stats[0]

        self.assertGreater(stats["relation"], 0)
        self.assertLessEqual(stats["relation"], stats["relation_budget"])
        self.assertLessEqual(stats["bound_overrun"], stats["overrun_budget"])
        self.assertLessEqual(stats["bound_escape"], stats["escape_budget"])
        self.assertLessEqual(stats["n_traces"], stats["negative_budget"])
        self.assertTrue(any(
            eval_predicate("0 <= k && k <= 1", state) is True
            and eval_predicate("k == 0 || a <= m", state) is False
            for state in examples.neg(0)
        ))

        class IdentityFilter:
            name = "identity"

            @staticmethod
            def filter(_program, _loop_idx, invariants, _positives=None):
                return list(invariants)

        result = RewardCalculator(
            invariant_filter=IdentityFilter(), n_jobs=1
        ).compute(source, [
            ["0 <= k", "k <= 1"],
            ["0 <= k", "k <= 1", "k == 0 || a <= m"],
        ], examples=examples)
        bounds_only, strongest = result.rollouts

        self.assertGreater(strongest.reward, bounds_only.reward + 0.15)

    def test_abnormal_program_exit_fails_sampling(self):
        source = (
            "#include <stdlib.h>\n"
            "void f(void) { int x = 0; while (x < 2) { x++; abort(); } }"
        )

        with self.assertRaisesRegex(ValueError, "exited abnormally"):
            ExampleSampler(source, n_runs=1).sample()

    def test_one_undefined_input_does_not_discard_valid_traces(self):
        source = (
            "/*@ requires n > 0; */\n"
            "void f(int n) { int guess = n / 2; int prev = 0; "
            "while (guess != prev) { prev = guess; "
            "guess = (guess + n / guess) / 2; } }"
        )

        examples = ExampleSampler(source, n_runs=12, seed=0).sample()

        self.assertGreater(len(examples.pos(0)), 0)
        self.assertEqual(examples.stats[0]["skipped_abnormal_run_count"], 1)
        skipped = examples.stats[0]["skipped_abnormal_runs"][0]
        self.assertEqual(skipped["inputs"], {"n": 1})
        self.assertIn("signal 8", skipped["error"])

    def test_typed_oracle_stub_and_labelled_body_compile(self):
        typed = (
            "extern unsigned int unknown_uint(void); "
            "void f(void) { unsigned int x = unknown_uint(); "
            "while (x > 0) { x--; } }"
        )
        program = parse_program(typed)
        instrumented = cexec.instrument(typed, program)
        full = cexec._build_program(instrumented, program, {}, run_seed=1)
        self.assertIn("unsigned int unknown_uint(void)", full)
        cexec._compile_run_parse(full, program, {}, 0, timeout=1)

        boolean = (
            "extern int unknown_bool(void); "
            "void f(void) { int x = 0; while (unknown_bool()) { x++; } }"
        )
        program = parse_program(boolean)
        full = cexec._build_program(
            cexec.instrument(boolean, program), program, {}, run_seed=1
        )
        self.assertIn("int unknown_bool(void){ return (int)(rand() & 1); }", full)

        labelled = "void f(void) { int x = 0; while (x < 1) { out: x++; } }"
        program = parse_program(labelled)
        instrumented = cexec.instrument(labelled, program)
        self.assertEqual(instrumented.count("out:"), 1)
        full = cexec._build_program(instrumented, program, {}, run_seed=1)
        cexec._compile_run_parse(full, program, {}, 0, timeout=1)

    def test_offline_jsonl_scoring_writes_structured_rows(self):
        source = "void f(void) { int x = 0; while (x < 2) { x++; } }"
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory, "rollouts.jsonl")
            output_path = Path(directory, "rewards.jsonl")
            reward_io.write_rows(str(input_path), [{
                "group_id": "g0",
                "program": source,
                "rollouts": [
                    {"invariants": ["x >= 0"]},
                    {"invariants": ["1 == 1"]},
                ],
            }])

            with mock.patch(
                "rl_pipeline.reward.score_file.filters.auto_filter",
                return_value=PositiveFilter(),
            ):
                stats = score_file(
                    str(input_path),
                    str(output_path),
                    reward_io.IOConfig(),
                    sampler_kwargs={"n_runs": 1, "seed": 0},
                )

            rows = [
                json.loads(line)
                for line in output_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(stats["failed"], 0)
        self.assertEqual(len(rows), 2)
        self.assertIsInstance(rows[0]["invariants"], list)
        self.assertIsInstance(rows[0]["survivors"], list)

    def test_oracle_sampling_repeats_a_fixed_valid_input(self):
        inputs = cexec.sample_inputs(
            ["x"],
            {"x": {"min": 0, "max": 0}},
            n_runs=5,
            requires="x == 0",
            single_ok=False,
        )

        self.assertEqual(inputs, [{"x": 0}] * 5)

    def test_unsigned_linear_234_stays_nonnegative(self):
        source = (ROOT / "src/input/linear/234.c").read_text(encoding="utf-8")
        program = parse_program(source)

        examples = ExampleSampler(source, n_runs=2).sample()

        self.assertIn("N", program.unsigned_vars)
        self.assertIn("x", program.unsigned_vars)
        self.assertGreater(len(examples.pos(0)), 0)
        self.assertTrue(all(state.vars["N"] >= 0 for state in examples.pos(0)))
        self.assertTrue(all(state.pre["N"] >= 0 for state in examples.pos(0)))
        self.assertEqual(
            PositiveFilter().filter(program, 0, ["N >= 0"], examples.pos(0)),
            ["N >= 0"],
        )
        instrumented = cexec.instrument(source, program)
        self.assertIn("N=%u", instrumented)
        self.assertIn("x=%u", instrumented)

    def test_invalid_c_fails_sampling_and_returns_http_400(self):
        source = (
            "void f(void) { int x = 0; while (x < 1) { "
            "this_is_not_c; x++; } }"
        )

        with self.assertRaisesRegex(ValueError, "gcc failed"):
            ExampleSampler(source, n_runs=1).sample()

        service._EXAMPLE_CACHE.clear()
        response = TestClient(service.build_app()).post(
            "/reward",
            json={
                "program": source,
                "rollouts": [{"invariants": ["x >= 0"]}],
                "sampler": {"n_runs": 1, "seed": 0},
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("gcc failed", response.json()["detail"])

    def test_nondeterministic_guard_and_body_keep_safe_negatives(self):
        programs = {
            "guard": (
                "int unknown(void); void f(void) { int x = 0; "
                "while (unknown()) { x++; } }"
            ),
            "body": (
                "int unknown(void); void f(void) { int x = 0; "
                "while (x < 100) { if (unknown()) break; x += 5; } }"
            ),
        }

        for label, source in programs.items():
            with self.subTest(label=label):
                examples = ExampleSampler(source, n_runs=1).sample()
                self.assertGreater(len(examples.pos(0)), 0)
                self.assertGreater(len(examples.neg(0)), 0)
                self.assertGreater(len(examples.groups(0)), 0)

    def test_sampling_determinizes_oracle_calls_without_rewriting_declarations(self):
        source = (
            "int unknown(); int unknown1(void); "
            "void f(int limit) { int x = unknown(); "
            "while (x < limit && unknown1()) { x++; } }"
        )

        determinized = ExampleSampler._determinize_source(source)

        self.assertIn("int unknown();", determinized)
        self.assertIn("int unknown1(void);", determinized)
        self.assertIn("void f(int limit, int _nd0, int _nd1)", determinized)
        self.assertIn("int x = _nd0", determinized)
        self.assertIn("x < limit && _nd1", determinized)

    def test_only_body_oracle_dependencies_are_tainted(self):
        preloop = parse_program(
            "int unknown(void); void f(void) { int x = unknown(); "
            "while (x < 10) { x++; } }"
        )
        in_body = parse_program(
            "int unknown(void); void f(void) { int x = 0; int y = 0; "
            "while (y < 10) { x = unknown(); y = x; } }"
        )

        self.assertEqual(ExampleSampler._nondet_tainted(preloop), set())
        self.assertEqual(ExampleSampler._nondet_tainted(in_body), {"x", "y"})

    def test_untracked_block_state_disables_synthetic_negatives(self):
        source = (
            "void f(void) { int x = 0; while (x < 10) { "
            "int temporary = x; temporary++; x++; } }"
        )

        examples = ExampleSampler(source, n_runs=1).sample()

        self.assertEqual(examples.neg(0), [])
        self.assertEqual(examples.stats[0]["untracked_state"], ["temporary"])


class InferenceRegressionTests(unittest.TestCase):
    class _IdentityFilter:
        @staticmethod
        def filter(program, loop_idx, invariants, positives=None):
            return list(invariants)

    @staticmethod
    def _fake_verifier(verify_result, validate_result=()):
        class FakeOutputVerifier:
            def __init__(self, logger=None):
                self.syntax_correct = True
                self.syntax_error = "syntax Correct"
                self.validate_result = list(validate_result)
                self.verify_result = list(verify_result)

            def run(self, path):
                return None

        return FakeOutputVerifier

    def test_no_invariants_can_still_verify_the_target(self):
        source = (
            "void f(void) { int x = 0; while (x < 1) { x++; } "
            "/*@ assert x == 1; */ }"
        )
        framework = InferenceFramework(
            source,
            rollout_provider=MockRolloutProvider([[]]),
            invariant_filter=self._IdentityFilter(),
            n_rollouts=1,
            max_rerolls=0,
        )

        with mock.patch.object(
            inference_module.filters, "frama_c_available", return_value=True
        ):
            no_invariants = self._fake_verifier([True], validate_result=[])
            with mock.patch("src.output_verify.OutputVerifier", no_invariants):
                self.assertIs(framework._verify(source), True)

            missing_result = self._fake_verifier([True], validate_result=[])
            annotated = annotate.build_annotated(
                framework.original_prog, ["x >= 0"]
            )
            with mock.patch("src.output_verify.OutputVerifier", missing_result):
                self.assertIs(framework._verify(annotated), False)

            successful_result = self._fake_verifier(
                [True], validate_result=[True]
            )
            with mock.patch("src.output_verify.OutputVerifier", successful_result):
                self.assertIs(framework._verify(annotated), True)

    def test_framework_caps_each_rollout_at_twenty_invariants(self):
        rollout = [f"x >= {-index}" for index in range(25)]
        framework = InferenceFramework(
            "void f(void) { int x = 0; while (x < 1) { x++; } }",
            rollout_provider=MockRolloutProvider([rollout]),
            invariant_filter=self._IdentityFilter(),
            n_rollouts=1,
            max_rerolls=0,
        )
        framework._verify = mock.Mock(return_value=True)

        result = framework.run()

        self.assertEqual(len(result.rollouts[0]), 20)
        self.assertEqual(len(result.final_invariants), 20)
        self.assertNotIn("x >= -24", result.final_invariants)

    def test_inference_preserves_unknown_and_injects_logic_definitions(self):
        source = (
            "int unknown(void); "
            "void f(int k) { int p = 1, i = 0, fact = 1; "
            "while (unknown()) { p *= k; i++; fact *= i; } "
            "/*@ assert p >= 1; */ }"
        )
        framework = InferenceFramework(
            source,
            rollout_provider=MockRolloutProvider([[
                "p == power(k, i)",
                "fact == factorial(i)",
            ]]),
            invariant_filter=self._IdentityFilter(),
            n_rollouts=1,
            max_rerolls=0,
        )
        framework._verify = mock.Mock(return_value=True)

        result = framework.run()

        self.assertIn("logic integer power", result.annotated_code)
        self.assertIn("logic integer factorial", result.annotated_code)
        self.assertEqual(result.annotated_code.count("unknown()"), 1)
        self.assertEqual(result.final_invariants, [
            "p == power(k, i)",
            "fact == factorial(i)",
        ])

    def test_importing_inference_does_not_import_sampler(self):
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        command = (
            "import sys; import rl_pipeline.inference; "
            "raise SystemExit(int('rl_pipeline.sampler' in sys.modules))"
        )

        completed = subprocess.run(
            [sys.executable, "-c", command],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_ensures_requires_a_successful_verification_goal(self):
        source = (
            "/*@ ensures \\result == 1; */ "
            "int f(void) { int x = 0; while (x < 1) { x++; } return 0; }"
        )
        framework = InferenceFramework(
            source,
            rollout_provider=MockRolloutProvider([["1 == 1"]]),
            invariant_filter=self._IdentityFilter(),
            n_rollouts=1,
            max_rerolls=0,
        )

        for verify_result, expected in (([], False), ([False], False), ([True], True)):
            with self.subTest(verify_result=verify_result):
                fake = self._fake_verifier(verify_result)
                with (
                    mock.patch.object(
                        inference_module.filters,
                        "frama_c_available",
                        return_value=True,
                    ),
                    mock.patch("src.output_verify.OutputVerifier", fake),
                ):
                    self.assertIs(framework._verify(source), expected)

    def test_in_loop_assertion_requires_a_successful_verification_goal(self):
        source = (
            "void f(void) { int x = 0; while (x < 1) { "
            "/*@ assert x >= 0; */ x++; } }"
        )
        framework = InferenceFramework(
            source,
            rollout_provider=MockRolloutProvider([["x >= 0"]]),
            invariant_filter=self._IdentityFilter(),
            n_rollouts=1,
            max_rerolls=0,
        )

        for verify_result, expected in (([], False), ([False], False), ([True], True)):
            with self.subTest(verify_result=verify_result):
                fake = self._fake_verifier(verify_result)
                with (
                    mock.patch.object(
                        inference_module.filters,
                        "frama_c_available",
                        return_value=True,
                    ),
                    mock.patch("src.output_verify.OutputVerifier", fake),
                ):
                    self.assertIs(framework._verify(source), expected)

    def test_only_final_verification_receives_the_original_assertion(self):
        source = (
            "/*@ requires limit >= 0; */\n"
            "void f(int limit) {\n"
            "  int x = 0;\n"
            "  while (x < limit) { x++; }\n"
            "  /*@ assert x == limit; */\n"
            "}\n"
        )
        seen = {}

        class RecordingProvider:
            @staticmethod
            def __call__(program, _n):
                seen["generate"] = program
                return [["x >= 0"]]

            @staticmethod
            def refine(program, _feedback, _n):
                seen["refine"] = program
                return [["x <= limit"]]

        class RecordingFilter:
            @staticmethod
            def precheck(program, _loop_idx, invariants):
                seen["precheck"] = program
                return [
                    inference_module.filters.Verdict(
                        invariants[0], False, "wp", "needs a companion"
                    )
                ]

            @staticmethod
            def filter(program, _loop_idx, invariants, positives=None):
                seen["filter"] = program
                return list(invariants)

        framework = InferenceFramework(
            source,
            rollout_provider=RecordingProvider(),
            invariant_filter=RecordingFilter(),
            n_rollouts=1,
            max_rerolls=0,
            m_refine=1,
        )
        framework._verify = mock.Mock(return_value=True)

        result = framework.run()

        for stage in ("generate", "precheck", "refine", "filter"):
            with self.subTest(stage=stage):
                program = seen[stage]
                self.assertNotIn("assert x == limit", program.source)
                self.assertEqual(program.post, "")
                self.assertEqual(program.requires, "limit >= 0")
        verified_source = framework._verify.call_args.args[0]
        self.assertEqual(verified_source, result.annotated_code)
        self.assertIn("assert x == limit", verified_source)
        self.assertIn("loop invariant x >= 0;", verified_source)
        self.assertIn("loop invariant x <= limit;", verified_source)

    def test_reroll_count_reports_attempts_even_when_first_result_stays_best(self):
        class Provider:
            def __init__(self):
                self.calls = 0

            def __call__(self, _program, _n):
                self.calls += 1
                if self.calls == 1:
                    return [["x >= 0", "x <= 2"]]
                return [["1 == 1"]]

        framework = InferenceFramework(
            "void f(void) { int x = 0; while (x < 2) { x++; } }",
            rollout_provider=Provider(),
            invariant_filter=self._IdentityFilter(),
            n_rollouts=1,
            max_rerolls=1,
        )
        framework._verify = mock.Mock(return_value=False)

        result = framework.run()

        self.assertEqual(result.final_invariants, ["x >= 0", "x <= 2"])
        self.assertEqual(result.reroll_count, 1)


class CommandAndPackagingRegressionTests(unittest.TestCase):
    def test_loopy_manifests_partition_supported_and_float_inputs(self):
        loopy = ROOT / "src" / "input" / "Loopy"
        manifest = [
            json.loads(line)
            for line in (loopy / "manifest.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        c_files = sorted(loopy.glob("*.c"), key=lambda path: int(path.stem))
        supported_ids = list(range(1, 353)) + list(range(356, 470))

        self.assertEqual(len(manifest), 466)
        self.assertEqual([row["id"] for row in manifest], supported_ids)
        self.assertEqual([row["file"] for row in manifest], [p.name for p in c_files])
        for row, path in zip(manifest, c_files):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(row["output_sha256"], digest)
            source = path.read_text(encoding="ascii")
            self.assertNotRegex(source, r"\b(?:for|do)\s*\(")
            self.assertEqual(len(parse_program(source).loops), 1)

        self.assertEqual(
            {row["semantic_status"] for row in manifest},
            {"integer-normalization"},
        )
        self.assertEqual(len(discover_programs("core")), 366)
        self.assertEqual(len(discover_programs("loopy")), 466)
        self.assertEqual(len(discover_programs("all")), 832)
        self.assertTrue((loopy / "UPSTREAM_LICENSE.txt").is_file())
        self.assertTrue((loopy / "sources.txt").is_file())
        licenses = [
            path for path in (loopy / "LICENSES").rglob("*")
            if "license" in path.name.lower()
        ]
        self.assertEqual(len(licenses), 17)
        self.assertFalse((ROOT / "benchmarks").exists())

        inference_cli = importlib.import_module("rl_pipeline.inference.__main__")
        expanded = inference_cli._expand([str(loopy)])
        self.assertEqual(expanded, sorted(str(path) for path in c_files))
        all_supported = inference_cli._expand([str(ROOT / "src" / "input")])
        self.assertEqual(len(all_supported), 832)
        unsupported = ROOT / "unsupported" / "loopy"
        self.assertFalse(
            any(str(unsupported) in path for path in all_supported)
        )

        float_manifest = [
            json.loads(line)
            for line in (unsupported / "manifest.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        float_files = sorted(
            unsupported.glob("*.c"), key=lambda path: int(path.stem)
        )
        self.assertEqual([row["id"] for row in float_manifest], [353, 354, 355])
        self.assertEqual(
            [row["file"] for row in float_manifest],
            [path.name for path in float_files],
        )
        for row, path in zip(float_manifest, float_files):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(row["source_sha256"], digest)
            self.assertEqual(row["output_sha256"], digest)
            self.assertEqual(row["semantic_status"], "unsupported-float")
            self.assertIn("float", path.read_text(encoding="ascii"))
        self.assertEqual(
            set(supported_ids) | {row["id"] for row in float_manifest},
            set(range(1, 470)),
        )

    def test_refine_reward_rejects_an_out_of_range_loop_index(self):
        source = "void f(void) { int x = 0; while (x < 1) { x++; } }"
        service._EXAMPLE_CACHE.clear()
        with mock.patch.object(service, "_SHARED_FILTER", PositiveFilter()):
            response = TestClient(service.build_app()).post(
                "/refine_reward",
                json={
                    "program": source,
                    "pool": ["x >= 0"],
                    "refinements": [["x <= 1"]],
                    "loop_idx": 1,
                    "sampler": {"n_runs": 1, "seed": 0},
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("loop_idx 1 is out of range", response.json()["detail"])

    def test_score_file_accepts_valid_options_and_reports_failed_batches(self):
        score_module = importlib.import_module("rl_pipeline.reward.score_file")
        valid_argv = [
            "score_file",
            "--input", "input.jsonl",
            "--output", "output.jsonl",
            "--runs", "3",
            "--seed", "7",
            "--w-base", "0.7",
            "--reroll-threshold", "0.4",
            "--include-program",
            "--quiet",
        ]
        with (
            mock.patch.object(sys, "argv", valid_argv),
            mock.patch.object(
                score_module,
                "score_file",
                return_value={"failed": 0},
            ) as scorer,
        ):
            self.assertEqual(score_module.main(), 0)

        args = scorer.call_args.args
        self.assertEqual(args[0:2], ("input.jsonl", "output.jsonl"))
        self.assertEqual(args[3], {"n_runs": 3, "seed": 7})
        self.assertEqual(args[4:7], (0.7, 0.4, True))

        failed_argv = [
            "score_file",
            "--input", "input.jsonl",
            "--output", "output.jsonl",
        ]
        with (
            mock.patch.object(sys, "argv", failed_argv),
            mock.patch.object(
                score_module,
                "score_file",
                return_value={"failed": 1},
            ),
        ):
            self.assertEqual(score_module.main(), 1)

    def test_docker_context_keeps_inference_package(self):
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        patterns = {
            line.strip().rstrip("/")
            for line in dockerignore.splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "!"))
        }

        self.assertNotIn("rl_pipeline", patterns)
        self.assertNotIn("rl_pipeline/inference", patterns)
        dockerfile = (ROOT / "deploy/Dockerfile.inference").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "COPY rl_pipeline/inference/ /app/rl_pipeline/inference/",
            dockerfile,
        )

    def test_native_timeout_kills_the_descendant_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "child.pid"
            child_code = (
                "import pathlib,subprocess,time;"
                f"p=subprocess.Popen(['sleep','30']);"
                f"pathlib.Path({str(pid_path)!r}).write_text(str(p.pid));"
                "time.sleep(30)"
            )
            result = full832_native._run(
                [sys.executable, "-c", child_code],
                cwd=Path(directory),
                env=os.environ.copy(),
                timeout=1,
            )
            self.assertIsNone(result[0])
            self.assertTrue(result[1])
            self.assertLess(result[4], 3)
            child_pid = int(pid_path.read_text())
            deadline = time.monotonic() + 2
            while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertFalse(Path(f"/proc/{child_pid}").exists())


if __name__ == "__main__":
    unittest.main()
