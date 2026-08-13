import tempfile
import unittest
from pathlib import Path

from experiments.gpt5nano_full832.retry_native_infrastructure import _retryable


class RetryNativeInfrastructureTests(unittest.TestCase):
    def row(self, root: Path, *, status: str = "failed", log: str = "") -> dict:
        hidden = root / "input.hidden.c"
        hidden.write_text("void f(void) {}\n")
        (root / "command.log").write_text(log)
        return {
            "generation_status": status,
            "hidden_source": str(hidden),
        }

    def test_connection_failure_is_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = self.row(
                Path(tmp),
                log="httpcore.ConnectError: TLS/SSL connection has been closed",
            )
            self.assertTrue(_retryable(row))

    def test_rate_limit_is_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = self.row(Path(tmp), log='HTTP/1.1 429 Too Many Requests')
            self.assertTrue(_retryable(row))

    def test_native_proof_failure_is_not_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = self.row(Path(tmp), log="native verifier exhausted all attempts")
            self.assertFalse(_retryable(row))

    def test_benchmark_timeout_is_not_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = self.row(
                Path(tmp), status="timeout", log="Request timed out."
            )
            self.assertFalse(_retryable(row))


if __name__ == "__main__":
    unittest.main()
