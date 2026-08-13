from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest import mock

from experiments.gpt5nano_full832.api import RecordingChat
from rl_pipeline.common.program import parse_program
from rl_pipeline.inference import BatchedLLMRolloutProvider


SOURCE = "void f(int n) { int x = 0; while (x < n) { x++; } }\n"


class BatchedCombineTests(unittest.TestCase):
    @mock.patch("experiments.gpt5nano_full832.api.openai.OpenAI")
    def test_recording_chat_sends_n_and_counts_one_api_call(self, openai_cls):
        create = openai_cls.return_value.chat.completions.create
        create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=f"loop invariant x >= {i};"),
                    finish_reason="stop",
                )
                for i in range(10)
            ],
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=200,
                total_tokens=300,
            ),
        )
        recorder = RecordingChat(api_key="test")

        responses = recorder.chat_n("program", 10)

        self.assertEqual(len(responses), 10)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(create.call_args.kwargs["n"], 10)
        self.assertEqual(len(recorder.records), 1)
        self.assertEqual(recorder.records[0]["choice_count"], 10)
        self.assertEqual(recorder.usage()["api_call_count"], 1)
        self.assertEqual(recorder.usage()["total_tokens"], 300)

    def test_provider_passes_n_once_and_preserves_rollout_parsing(self):
        calls = []

        def chat_n(prompt: str, n: int):
            calls.append((prompt, n))
            return [f"loop invariant x >= {i};" for i in range(n)]

        rollouts = BatchedLLMRolloutProvider(chat_n)(parse_program(SOURCE), 5)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], 5)
        self.assertEqual(len(rollouts), 5)
        self.assertEqual(rollouts[3], ["x >= 3"])


if __name__ == "__main__":
    unittest.main()
