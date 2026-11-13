### Q4 — Moreover, the trained 8B model still reaches only 12% on the nonlinear subset, indicating that the overall improvement does not yet extend to such difficult tasks.

Thank you for highlighting the limited performance on the nonlinear subset. Qwen3-8B improves from 10% at compose@32 before our training to 12% at compose@1 afterward. On the same subset, CRAFT with GPT-5-nano/minimal reaches 34% at compose@1 and 44% at compose@16.

Our training relies on model-generated candidates, so missing nonlinear relations limit both composition and useful training supervision. The results support this explanation and show that CRAFT can solve more nonlinear tasks with stronger-model candidates, motivating models with better nonlinear reasoning.
