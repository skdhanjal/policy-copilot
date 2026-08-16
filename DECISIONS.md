
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

---

## D15 — Rate limiting via virtual keys, verified by exceeding the limit; SDK default retries hide 429s
**Chose:** Application uses a scoped virtual key (rpm_limit=5, max_budget=$1/24h,
models=["fast"] only), never the master key, for regular traffic. Master
key reserved for admin operations (creating/managing keys) only.
**Because:** Verified directly, not assumed from config. First test (default
AsyncOpenAI client) showed all 7 requests "succeeding" with an unexplained
pause -- looked like the limit did nothing. Checking LiteLLM's own logs
directly revealed the truth: request 6 WAS correctly rejected with a 429
("Current limit: 5, Remaining: 0"), but the OpenAI SDK's default retry
behavior caught it, waited, and retried automatically -- entirely inside
the client, invisible to application code and to our own test script.
**Cost / open risk:** Any code using the default AsyncOpenAI client against
this gateway will silently absorb rate-limit rejections as added latency,
not visible errors. Under real load this means "user requests are
mysteriously slow" with no application-level signal that a limit was hit --
the only way to see it is checking gateway logs directly, which does not
scale as an operational practice. RESOLVED: need to either (a) set
max_retries=0 explicitly and handle 429s in application code with a clear
user-facing signal, or (b) keep retries but log/emit a metric whenever the
underlying client actually retried, so rate-limit pressure is visible in
our own telemetry (Phase 0's LLMCall span -- retry count belongs there).
Not yet implemented; tracked as follow-up before Phase 5 (latency work)
where a hidden retry delay would directly corrupt our own latency
measurements without us knowing why.

---

## D16 — Budget enforcement verified, first test miscalibrated by real pricing
**Chose:** Budget caps verified via a virtual key with max_budget set BELOW
a real measured call cost, not guessed.
**Because:** First attempt used max_budget=$0.0001, assuming a single
gpt-4o-mini call would exceed it. All 3 test calls succeeded --
investigated via /key/info (which itself required debugging: curl's -G
flag combined with a JSON -d body produced a malformed request that
failed silently with HTTP 000, resolved by passing the key as a plain
query parameter instead) and found real spend was $0.0000735 for all
three calls combined -- genuinely BELOW the $0.0001 budget. Not a
tracking or enforcement bug; the test threshold was simply larger than
real cost. Recalibrated to $0.00001 (below one call's ~$0.0000245
measured cost) and re-ran: request 1 succeeded, requests 2+ correctly
rejected with 429 "budget_exceeded", exact spend and limit in the error
message.
**Cost:** None on the gateway side -- both failures were test-design
errors (wrong threshold, wrong curl flag combination), not gateway
defects. Worth the general lesson: an unexpected result is at least as
often a wrong assumption in the test as a bug in the thing being tested,
and /key/info-style introspection endpoints are what let you tell the
difference instead of guessing.
