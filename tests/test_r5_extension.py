from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from experiments.gpt5nano_full832.common import Task, sha256_text
from experiments.gpt5nano_full832.r5_extension import _load_prefix
from rl_pipeline.common import prompts


SOURCE = "void f(int n) { int x = 0; while (x < n) { x++; } }\n"


class R5ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.task = Task(
            suite="linear",
            case_id="x",
            source_path=Path("/tmp/not-read.c"),
            source_sha256="source",
            hidden_source=SOURCE,
            hidden_source_sha256="hidden",
        )
        prompt_hash = sha256_text(prompts.GENERATE_PROMPT.format(program=SOURCE))
        self.records = [
            {
                "prompt_sha256": prompt_hash,
                "response": f"loop invariant x >= {index};",
                "seconds": 1.0,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "api_call_count": 1,
                "token_accounting": "exact",
            }
            for index in range(10)
        ]

    def test_loads_exactly_first_five_r10_responses(self):
        with tempfile.TemporaryDirectory() as raw:
            artifact = Path(raw) / "api_calls.json"
            artifact.write_text(json.dumps(self.records))
            prefix, source = _load_prefix(
                self.task,
                {"generation_status": "completed", "api_calls_artifact": str(artifact)},
            )
            self.assertEqual(len(prefix), 5)
            self.assertEqual(
                [record["response"] for record in prefix],
                [record["response"] for record in self.records[:5]],
            )
            self.assertEqual(source, str(artifact.resolve()))

    def test_rejects_prompt_mismatch(self):
        with tempfile.TemporaryDirectory() as raw:
            artifact = Path(raw) / "api_calls.json"
            self.records[7]["prompt_sha256"] = "different"
            artifact.write_text(json.dumps(self.records))
            with self.assertRaisesRegex(RuntimeError, "incompatible R10"):
                _load_prefix(
                    self.task,
                    {"generation_status": "completed", "api_calls_artifact": str(artifact)},
                )


if __name__ == "__main__":
    unittest.main()
