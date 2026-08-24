from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "paper" / "scripts" / "filter_training_by_negative_coverage.py"
sys.path.insert(0, str(ROOT))

from rl_pipeline.sampler.example_sampler import NEGATIVE_SCHEMA_VERSION  # noqa: E402


def _record(source: str) -> dict:
    return {
        "conversations": [
            {"from": "system", "value": "system"},
            {"from": "human", "value": "task\nProgram:\n" + source},
            {"from": "gpt", "value": "loop invariant x >= 0;"},
        ]
    }


def _coverage(source: str, *, scorable: bool, negatives: int) -> dict:
    return {
        "coverage_schema_version": NEGATIVE_SCHEMA_VERSION,
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "scorable": scorable,
        "n_negative_traces": negatives,
    }


class NegativeCoverageFilterTests(unittest.TestCase):
    def _run(self, records: list[dict], ledger: list[dict]):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        input_path = directory / "input.json"
        ledger_path = directory / "ledger.jsonl"
        output_path = directory / "output.json"
        report_path = directory / "report.json"
        input_path.write_text(json.dumps(records), encoding="utf-8")
        ledger_path.write_text(
            "".join(json.dumps(row) + "\n" for row in ledger),
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "sft",
                "--input",
                str(input_path),
                "--ledger",
                str(ledger_path),
                "--output",
                str(output_path),
                "--report",
                str(report_path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        return result, output_path, report_path

    def test_release_keeps_only_source_hashed_loops_with_negatives(self):
        positive = "void f(void) { int x = 0; while (x < 2) { x++; } }"
        zero = "void g(void) { int x = 0; while (x < 2) { x++; } }"

        result, output, report = self._run(
            [_record(positive), _record(zero)],
            [
                _coverage(positive, scorable=True, negatives=1),
                _coverage(zero, scorable=True, negatives=0),
            ],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(output.read_text()), [_record(positive)])
        self.assertEqual(json.loads(report.read_text())["dropped_rows"], [1])

    def test_release_fails_closed_when_source_hash_is_missing(self):
        source = "void f(void) { int x = 0; while (x < 2) { x++; } }"

        result, output, report = self._run([_record(source)], [])

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ledger is incomplete", result.stderr)
        self.assertFalse(output.exists())
        self.assertFalse(report.exists())

    def test_release_fails_closed_on_sampler_error(self):
        source = "void f(void) { int x = 0; while (x < 2) { x++; } }"

        result, output, report = self._run(
            [_record(source)],
            [_coverage(source, scorable=False, negatives=0)],
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unscorable rows", result.stderr)
        self.assertFalse(output.exists())
        self.assertFalse(report.exists())

    def test_release_fails_closed_on_stale_sampler_schema(self):
        source = "void f(void) { int x = 0; while (x < 2) { x++; } }"
        stale = _coverage(source, scorable=True, negatives=1)
        stale["coverage_schema_version"] = NEGATIVE_SCHEMA_VERSION - 1

        result, output, report = self._run([_record(source)], [stale])

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stale sampler schema", result.stderr)
        self.assertFalse(output.exists())
        self.assertFalse(report.exists())


if __name__ == "__main__":
    unittest.main()
