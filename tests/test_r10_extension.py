from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from experiments.gpt5nano_full832.common import Task, sha256_text
from experiments.gpt5nano_full832.r10_extension import _reusable_batches
from rl_pipeline.common import prompts


SOURCE = "void f(int n) { int x = 0; while (x < n) { x++; } }\n"


class R10ExtensionTests(unittest.TestCase):
    def test_four_rollout_attempts_are_reused_only_with_matching_prompt(self):
        task = Task(
            suite="linear",
            case_id="x",
            source_path=Path("/tmp/not-read.c"),
            source_sha256="source",
            hidden_source=SOURCE,
            hidden_source_sha256="hidden",
        )
        prompt = prompts.GENERATE_PROMPT.format(program=SOURCE)
        records = [
            {
                "prompt_sha256": sha256_text(prompt),
                "response": f"loop invariant x >= {index};",
                "seconds": 1.0,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "api_call_count": 1,
                "token_accounting": "exact",
            }
            for index in range(8)
        ]
        with tempfile.TemporaryDirectory() as raw:
            artifact = Path(raw) / "api_calls.json"
            artifact.write_text(json.dumps(records))
            batches, source = _reusable_batches(
                task,
                {"generation_status": "completed", "api_calls_artifact": str(artifact)},
            )
            self.assertEqual([len(batch) for batch in batches], [4])
            self.assertEqual(source, str(artifact))

            records[0]["prompt_sha256"] = "different"
            artifact.write_text(json.dumps(records))
            batches, source = _reusable_batches(
                task,
                {"generation_status": "completed", "api_calls_artifact": str(artifact)},
            )
            self.assertEqual(batches, [])
            self.assertIsNone(source)


if __name__ == "__main__":
    unittest.main()
