
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

---

## D17 — D15 resolved: explicit rate_limited signal, no silent SDK retries
**Chose:** generate.py's OpenAI client now sets max_retries=0. A caught
RateLimitError produces a GeneratedAnswer with rate_limited=True and an
honest user-facing message, instead of either an uncaught exception or
the SDK's previous silent retry-and-wait behavior.
**Because:** D15 identified that default SDK retries hid real 429
rejections as invisible added latency. Verified fix directly: 7 requests
against a 5/minute limit now show requests 1-5 succeeding normally and
6-7 failing immediately and visibly with rate_limited=True, rather than
all 7 "succeeding" after a hidden delay.
**Cost:** The caller (eventually the API layer, Phase 8's dashboards) must
now actually check `rate_limited` and handle it -- e.g., surface a retry
prompt to the user. A caller that ignores the field and only reads `.text`
would show the literal fallback message as if it were a real answer, which
is honest but not a good user experience on its own. Follow-up for Phase 5
or the API layer: translate rate_limited=True into a proper HTTP 429 at
the FastAPI boundary, not just a string in the response body.

---

## D18 — Ragas judge calls bypass the gateway entirely; deliberate deferral, not fixed
**Chose:** Ragas metric calls continue going directly to OpenAI via
LangChain's own credential lookup, NOT through the LiteLLM gateway.
Fixed the immediate crash by exporting OPENAI_API_KEY into the shell.
**Because:** Discovered while wiring answer_relevancy/context_quality --
Ragas uses langchain_openai internally, which reads the raw OS
OPENAI_API_KEY environment variable directly, completely independent of
our pydantic-settings/.env mechanism AND independent of the gateway.
Every Ragas call all session (including every faithfulness score used to
find D13) has been going straight to OpenAI, not through LiteLLM --
worked only because a real key happened to be in the environment; broke
the moment that stopped being reliably true.
**Cost / open gap:** This is a real inconsistency with ADR-4 ("no vendor
SDK outside the gateway client"). Eval-judge spend is invisible to our
gateway's cost tracking and budget enforcement (D16) -- it could exceed a
key's budget with zero gateway-level signal, since it never touches the
gateway at all. The export-based fix is SESSION-SCOPED ONLY: a new
terminal requires re-exporting or evals crash again. Not fixed properly
because eval traffic was judged a different class from production traffic
(not user-facing, no fallback/rate-limit need) -- deferred, not resolved.
Follow-up: either configure LangChain's OpenAI client to point at the
gateway's base_url explicitly (langchain_openai supports a base_url
param), or accept this permanently and document eval cost as untracked
by design.

---

## D19 — context_precision requires ground truth too; original design was wrong
**Chose:** context_precision and context_recall are now gated together on
the same expected_answer field, both skipped together when it's absent.
**Because:** Original check_context_quality assumed context_precision
needed no ground truth (based on prior research summarized in the
metric-selection framework). Running it for real threw a ValueError
requiring a 'reference' column. Corrected: context_precision wants the
ground-truth field named 'reference'; context_recall wants the SAME data
under the field name 'ground_truth' -- two different column names for
one underlying concept, confirmed via the real API error, not
documentation. General lesson, repeated again this session: verify a
tool's actual required inputs by running it, don't trust a description
of what it "should" need.

---

## D20 — CI gate built and correctly failing on first real run; left red, not downgraded
**Chose:** evals/runners/ci_gate.py -- tiered severity (blocking vs
informational per item), known_unreliable_checks downgrade specific
checks to informational even on blocking items, regression detection
against the previous saved report, absolute 70% floor as backstop.
First real run: FAILS, correctly, on diachronic-notification-event-001
(date_binding=0%, citation_presence=67%).
**Because:** This is not a gate bug. date_binding=0% reflects the real,
still-unresolved notification-event misattribution found earlier this
session -- a soft prompt fix only achieved 1/3 improvement and was never
fully resolved (unlike the structurally-similar fabricated-comparison
case, D12, which WAS fully fixed via deterministic identity injection).
A weaker gate checking only faithfulness (which scores this exact case
100%, per D13) would have shown green here. This is the entire point of
building custom checks: catching what the "obvious" metric misses.
**Decision: left failing, not downgraded to informational.** Explicitly
rejected quietly marking this item informational just because it's
inconvenient right now -- a gate that gets softened the moment it
catches something real stops being a gate. This failure is now a visible,
tracked TODO: date-binding reliability on diachronic questions must
improve before this item can honestly pass. Options for a real fix,
not yet attempted: (a) a structural prompt rewrite requiring the model
to quote verbatim from a specific version block before making any
version-attributed claim, closer in spirit to D12's approach but for a
case where identity can't be precomputed the way "are these texts equal"
can; (b) a post-generation verification pass using check_date_bindings
itself to catch and retry/flag bad attributions before returning an
answer to the user, moving the check from eval-time to serve-time.
