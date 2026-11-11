### Q3 — There are two reporting points I find somewhat confusing and would like clarified.

#### Q3.1 — Second, in Table 23, GPT-5-nano's pass@1 is 3.80% on Linear and 3.86% on Loopy, whereas in Table 25 it is 2.53% and 4.72%; both tables report an overall value of 3.61%, and the text states that the same sample pool is used. Is this a typo in one of the tables, or do the two evaluations differ in how they are processed?

Thank you for identifying this reporting error. The GPT-5-nano pass@1 results in Tables 23 and 25 come from two independent runs. We will correct the shared-pool description and use the first run, underlying Table 23, as the common reporting source for both tables.

#### Q3.2 — First, does the SFT (single) in K.2 also use the same coverage threshold as the compositional labels? Does "approximately the same number of training samples" mean that both groups use the same batch of programs?

Thank you for requesting this clarification. SFT (single) does not use the same coverage threshold. Both datasets start from exactly the same input-loop pool and use the same SFT settings. Composed labels use multi-round composition, filtering, and coverage-based acceptance; SFT (single) generates one response per loop, removes incorrect clauses, and skips examples with none remaining.

"Approximately the same number of training samples" refers to the resulting dataset sizes. The procedures retain slightly different inputs: over 93% of the distinct prompts in the composed-label dataset also appear in the single-response dataset. We will clarify this distinction between the original program pool and the retained training sets in K.2.
