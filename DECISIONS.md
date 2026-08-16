
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

---

## D14 — LiteLLM gateway, fallback verified by deliberate failure injection
**Chose:** LiteLLM proxy (pinned v1.94.2, not `main-latest`), OpenAI as
primary under alias "fast", Gemini 2.5 Flash as fallback in the same alias
group. Application code (generate.py) only ever references "fast" -- ADR-4
realized in code.
**Because:** Fallback groups are only trustworthy once proven to actually
fire, not just configured. Verified by deliberately injecting an invalid
OpenAI key and confirming a real, correct answer still came back --
provably from Gemini, since OpenAI could not have answered.
**Cost, found during setup, not assumed:**
  - `main-latest` produced "exec format error" on confirmed matching
    x86_64 hardware -- almost certainly a broken/mismatched publish on
    that floating tag. Pinning to a signed release tag fixed it.
  - `gemini-1.5-flash` returned 404 -- Gemini 1.0 and 1.5 are fully
    shut down as of this session. Fixed to `gemini-2.5-flash`.
  - Model parity is NOT guaranteed across the fallback: same question,
    same prompt, OpenAI cited "314.3" while Gemini cited "314.3(a)" --
    different citation granularity from the same instructions. Any code
    checking citation format (unverifiable_citations in generate.py) needs
    to tolerate this variance or it will misflag real Gemini citations as
    fabricated purely due to formatting differences, not content errors.
