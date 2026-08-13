from __future__ import annotations

import unittest

from experiments.gpt5nano_full832.r10_extension import (
    EXTENSION_PROTOCOL,
    N_ROLLOUTS,
    REQUESTS_PER_TASK,
    ROLLOUTS_PER_REQUEST,
    _compatible_completed,
)


class R10ExtensionTests(unittest.TestCase):
    def test_combine_ten_is_two_requests_with_five_rollouts_each(self):
        row = {
            "generation_status": "completed",
            "extension_protocol": EXTENSION_PROTOCOL,
            "api_call_count": REQUESTS_PER_TASK,
            "fresh_api_call_count": REQUESTS_PER_TASK,
            "n_rollouts": N_ROLLOUTS,
            "rollout_count": N_ROLLOUTS,
            "raw_responses": [f"response {i}" for i in range(N_ROLLOUTS)],
        }
        self.assertEqual(N_ROLLOUTS, 10)
        self.assertEqual(REQUESTS_PER_TASK, 2)
        self.assertEqual(ROLLOUTS_PER_REQUEST, 5)
        self.assertTrue(_compatible_completed(row))

    def test_old_request_schedule_is_incompatible(self):
        row = {
            "generation_status": "completed",
            "extension_protocol": EXTENSION_PROTOCOL,
            "api_call_count": 1,
            "fresh_api_call_count": 1,
            "n_rollouts": 10,
            "rollout_count": 10,
            "raw_responses": ["response"] * 10,
        }
        self.assertFalse(_compatible_completed(row))


if __name__ == "__main__":
    unittest.main()
