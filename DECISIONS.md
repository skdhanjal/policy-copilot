
---

## D13 — Ragas faithfulness is unreliable on negative/no-change claims; never sole signal for those
**Chose:** For any golden-set item whose correct answer is "no change
occurred" or an abstention, faithfulness score is treated as informational
only, never as the pass/fail signal. A deterministic check (date_binding,
or a purpose-built check) is required.
**Because:** Measured directly, twice, independently. Two different
correct, verified-true "nothing changed" answers scored inconsistently on
faithfulness across repeated evaluations of the SAME input:
`unanswerable-pre-window-001` scored 0.57, then 0.5 (x3 in one batch),
then 0.25 (run alone), then 1.0 -- four different scores for one fixed
answer. `hallucination-fabricated-comparison-001`, verified correct 3/3
manually and 100% on our own date_binding check, scored faithfulness=0%.
Working theory: proving a negative is harder for an entailment-style judge
to verify than confirming a positive claim, and Ragas's public API in this
version doesn't expose per-claim breakdown to confirm which specific
sub-claim triggers it.
**Cost:** This is a meaningful gap for our domain specifically -- ADR-7
makes "no change" and abstention first-class correct outcomes, not edge
cases, which means the metric is least reliable exactly where correctness
matters most. Mitigated by pairing every no-change/abstention item with a
deterministic check; not solved, since deterministic checks don't scale to
domains without an obvious ground-truth computation the way "are these
texts byte-identical" does.
