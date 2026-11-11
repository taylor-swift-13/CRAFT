### Q5 — Are the post-SFT gains from Shapley credit consistent across training seeds? Table 27 reports improvements of 1.32 percentage points in compose@1 and 1.68 points in compose@32, but the stability of these incremental gains is not established by the reported experiments.

Thank you for asking about stability. Three independent training runs with different seeds and the same training and evaluation settings show small positive Shapley gains. Each compares Full with the corresponding clause-decomposed reward without Shapley:

| Repetition | Shapley gain in compose@1 (pp) | Shapley gain in compose@32 (pp) |
|---|---:|---:|
| 1 | +0.6 | +0.1 |
| 2 | +1.8 | +1.7 |
| Reported in Table 27 | +1.3 | +1.7 |

The modest gains after SFT are consistent with our expectations and mechanism. Our SFT data construction collects multiple responses, jointly filters their clauses, and selects composed labels using negative-trace coverage. It substantially improves compositional verification with few responses: compose@1 reaches 63.94% after SFT, exceeding compose@32 before SFT (57.81%). Thus, SFT makes a fixed sampling budget more effective.

Our method for constructing discriminative negative traces also provides an informative signal for clause-level rewards. Effective SFT and this reward signal make a smaller marginal benefit from additional credit assignment plausible. At each fixed sampling budget, Table 27 shows that Shapley gains decrease after SFT: from 3.00 to 1.32 percentage points at compose@1 and from 4.32 to 1.68 points at compose@32, consistent with this explanation.

For context, Appendix K.10 (Figure 12) analyzes distribution sharpening under whole-response RL: pass@1 rises from 6.37% to 15.50%, while pass@32 falls from 25.96% to 18.39%. Our clause-decomposed training and compositional inference mitigate this coverage loss.
