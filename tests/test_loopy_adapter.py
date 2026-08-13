from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest

from experiments.gpt5nano_full832 import loopy_adapter


LOOPY_ROOT = Path("/home/yangfp/Loopy")


class LoopyAdapterTests(unittest.TestCase):
    def test_original_schedule_constants(self):
        self.assertEqual(loopy_adapter.INITIAL_COMPLETIONS, 15)
        self.assertEqual(loopy_adapter.INITIAL_BATCH_SIZE, 5)
        self.assertEqual(loopy_adapter.COMBINE_K, 8)
        self.assertEqual(loopy_adapter.SHUFFLE_TIMES, 10)
        self.assertEqual(loopy_adapter.MAX_REPAIRS, 7)
        self.assertEqual(loopy_adapter.TEMPERATURE, 0.7)
        self.assertEqual(loopy_adapter.TOP_P, 1.0)
        self.assertEqual(loopy_adapter.REASONING_EFFORT, "none")
        self.assertEqual(loopy_adapter.MAX_TOKENS, 8192)

    def test_all_model_facing_sources_can_remain_target_hidden(self):
        hidden = "void f(void) { int x = 0; while (x < 2) x++; }"
        target = "assert(x == 2)"
        initial = loopy_adapter._official_messages(LOOPY_ROOT, hidden)
        repair = loopy_adapter._repair_messages(
            LOOPY_ROOT,
            hidden,
            "loop invariant x >= 0 is inductive.",
        )
        joined = "\n".join(
            message["content"] for message in initial + repair
        )
        self.assertIn(hidden, joined)
        self.assertNotIn(target, joined)

    def test_candidate_schedule_selects_eight_rollouts_ten_times(self):
        rollouts = [[f"x >= {index}"] for index in range(15)]
        candidates = loopy_adapter._candidate_rollouts(rollouts, seed=7)
        self.assertEqual(len(candidates), 10)
        self.assertTrue(all(len(indices) == 8 for indices, _ in candidates))
        self.assertTrue(all(len(selected) == 8 for _, selected in candidates))

    def test_only_first_valid_fenced_block_is_extracted(self):
        response = (
            "explanation\n```acsl\nloop invariant x >= 0;\n```\n"
            "```acsl\nloop invariant x <= 10;\n```"
        )
        self.assertEqual(loopy_adapter._official_invariants(response), ["x >= 0"])
        self.assertEqual(
            loopy_adapter._official_invariants("loop invariant x >= 0;"), []
        )

    def test_request_disables_reasoning_and_records_reported_reasoning_tokens(self):
        usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=450,
            total_tokens=550,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=300),
        )
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="```\nloop invariant x >= 0;\n```"),
                    finish_reason="stop",
                )
            ],
            usage=usage,
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: response)
            )
        )
        responses, record = loopy_adapter._request(
            client,
            model="gpt-5-nano",
            messages=[{"role": "user", "content": "hidden"}],
            n=1,
            phase="repair_1",
        )
        self.assertEqual(len(responses), 1)
        self.assertEqual(record["reasoning_effort"], "none")
        self.assertEqual(record["reasoning_tokens"], 300)
        self.assertEqual(record["api_call_count"], 1)


if __name__ == "__main__":
    unittest.main()
