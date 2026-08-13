from __future__ import annotations

import unittest

from experiments.gpt5nano_full832.r5_extension import (
    EXTENSION_PROTOCOL,
    N_ROLLOUTS,
    _compatible_completed,
)


class R5ExtensionTests(unittest.TestCase):
    def test_combine_five_is_one_request_with_five_rollouts(self):
        row = {
            "generation_status": "completed",
            "extension_protocol": EXTENSION_PROTOCOL,
            "api_call_count": 1,
            "fresh_api_call_count": 1,
            "n_rollouts": N_ROLLOUTS,
            "rollout_count": N_ROLLOUTS,
            "raw_responses": [f"response {i}" for i in range(N_ROLLOUTS)],
        }
        self.assertEqual(N_ROLLOUTS, 5)
        self.assertTrue(_compatible_completed(row))

    def test_old_five_request_result_is_incompatible(self):
        row = {
            "generation_status": "completed",
            "extension_protocol": EXTENSION_PROTOCOL,
            "api_call_count": 5,
            "fresh_api_call_count": 5,
            "n_rollouts": 5,
            "rollout_count": 5,
            "raw_responses": ["response"] * 5,
        }
        self.assertFalse(_compatible_completed(row))


if __name__ == "__main__":
    unittest.main()
