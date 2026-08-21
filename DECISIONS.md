# Decision Log

One entry per real decision made, in the order made. Purpose: make the
reasoning solid enough that you could re-derive it, and fast enough to
re-read that you actually will. Each entry: what we chose, what we didn't,
why, and what it cost. If an entry has no cost line, the decision wasn't
examined closely enough.

---

## D1 — WSL2 over native Windows
**Chose:** Develop inside WSL2 Ubuntu, not native Windows Python.
**Because:** Cloud Run is Linux. The container we deploy is Linux. Learning
in the same environment we deploy to means no translation layer between
"what I practiced" and "what runs in production."
**Cost:** An extra setup layer (WSL install, Docker integration toggle) that
native Windows wouldn't have needed. Accepted because the goal was closing
an infra skill gap, not shipping fastest.

---

## D2 — Cloud Run over GKE
**Chose:** Cloud Run for the API service, gateway, and ingest job.
**Because:** One service calling hosted model APIs, not a fleet of custom
services needing orchestration. Cloud Run gives scale-to-zero and
request-based autoscaling with no cluster to operate.
**Cost:** No path to self-hosted GPU inference later without a bigger
platform change. Accepted as out of scope for this project.

---

## D3 — pgvector over a dedicated vector database
**Chose:** Vectors live in Cloud SQL Postgres, alongside the lineage tables.
**Because:** Retrieval needs to JOIN vector similarity against version
metadata and date ranges (see D6). One database makes that a SQL join;
two databases make it an application-level merge and a sync problem.
**Cost:** Recall degrades at large scale (~5M+ vectors). Not a concern at
our corpus size (~700 chunks).

---

## D4 — Local embeddings, no gateway
**Chose:** `sentence-transformers` (`all-MiniLM-L6-v2`, 384-dim), called
directly, not through a gateway.
**Because:** The gateway's job (ADR-4 in DESIGN.md) is controlling calls to
*hosted* providers -- routing, fallback, cost tracking across vendors. A
local model has no API key, no per-call cost, and no vendor to fail over
from. None of the gateway's reasons for existing apply.
**Cost:** Lower embedding quality than a hosted model like
`text-embedding-3-small`. Accepted for Phase 1, revisit if Phase 2 eval
scores show retrieval quality (not mechanism) is the bottleneck.

---

## D5 — Direct OpenAI client for generation, no gateway (same reasoning as D4)
**Chose:** `gpt-4o-mini`, called directly via the OpenAI SDK, isolated in
one small module (`generate.py`).
**Because:** One call site, proving the generation *mechanism* works, not
yet needing routing/fallback/budget enforcement. Same reasoning as D4,
extended to a real hosted model this time.
**Cost:** This is real vendor code outside the gateway boundary -- later
made illegal everywhere by ADR-4 once Phase 3 built the real gateway.
`generate.py` was deliberately kept small so it was a clean swap, not a
rewrite, when Phase 3 arrived (see D14).

---

## D6 — Two-stage retrieval: semantic resolve, then relational lineage expand
**Chose:** Stage 1 (embedding search) runs ONLY against current-version
chunks. Stage 2 (SQL join on `section_path`) walks history, and only runs
for diachronic questions.
**Because:** Near-duplicate versions of one policy score within noise of
each other. Searching all versions lets one policy's amendment history
flood the top-k and evict a genuinely different, relevant policy.
Semantic search answers "which policy"; SQL answers "which version of it."
**Cost:** A withdrawn policy with no current version becomes unreachable by
stage 1 (documented, unmitigated). `section_path` must stay stable across
amendments for stage 2 to work -- true for CFR by regulatory convention,
NOT guaranteed for every corpus (this is why SEBI was rejected -- see D9).

---

## D7 — `section_path` as the join key for version alignment
**Chose:** Compare versions of a section by matching its citation string
(`314.2`, `275.204-2`), not by re-running semantic search or fuzzy text
matching.
**Because:** One indexed SQL query, exact alignment, no drift. The
alternative (semantic re-search per version) would introduce a second,
independent source of retrieval error on top of the first.
**Cost:** Entirely dependent on the corpus having stable citations across
amendments. This is a corpus PROPERTY we verified before committing (see D9),
not a general-purpose technique -- it would not work unmodified on a corpus
without that guarantee.

---

## D8 — `version_label` derived from `effective_from`, never from a source citation
**Chose:** Version boundaries come from the date content actually changed,
not from the regulatory citation (e.g. Federal Register number) attached to
that change.
**Because:** Empirically confirmed on 16 CFR 314: one Federal Register
rulemaking produced FIVE separate eCFR amendment dates as different
sections phased in on different effective dates. Labelling by citation
would have collapsed distinct, individually-answerable version boundaries
into one.
**Cost:** None significant -- this is strictly more correct than the
alternative once the staggered-effective-date reality was confirmed.

---

## D9 — Corpus: eCFR financial-services regulations, not SEBI
**Chose:** Three CFR Parts (17 CFR 275, 16 CFR 314, 12 CFR 1005) after
rejecting SEBI as a source and rejecting five other CFR Parts on inspection.
**Because (two separate reasons, don't conflate):**
  1. HIPAA/general-commerce subject matter gave no way to sanity-check
     outputs -- couldn't tell a plausible answer from a wrong one without
     domain intuition. Financial-services compliance maps to SEBI-adjacent
     concepts the builder already reasons about natively.
  2. SEBI itself was investigated and rejected -- no point-in-time API, and
     published research confirms SEBI amendments don't preserve section
     numbering across versions, breaking the D6/D7 mechanism entirely.
     Solving that is a research problem, not Phase 1 ingest scope.
**Cost:** Three regulators instead of a rounder four -- eight candidate CFR
Parts were probed; the rest were either omnibus-scale (one Part spanning
dozens of unrelated programs) or too thin (fewer than 3 amendment dates).
A forced mediocre fourth family was explicitly rejected in favor of three
well-shaped ones.

---

## D10 — Sequential input processing; speculative retrieval deferred, not dropped
**Chose:** PII redaction, then injection classification, then retrieval --
in order, not concurrent. The "use barrier" (nothing retrieved may be used
before the guardrail verdict resolves) is a permanent invariant regardless
of execution strategy.
**Because:** Speculative concurrent retrieval was originally planned to save
~150ms, but introduces a cancellation-under-load risk (pool exhaustion from
cancelled in-flight queries -- exactly what a volume of blocked/malicious
queries would trigger). That risk is solvable (structured concurrency,
separate pool for speculative reads, load testing) but only WITH a load
test and latency baseline, which don't exist until Phase 5.
**Cost:** ~150ms slower per request until Phase 5. Explicitly rejected
alternative: keep speculation, drop the barrier -- this was on the table and
directly rejected, because it keeps the cancellation risk while removing the
one safety control, which is strictly worse than either extreme.

---

## D11 — Citations must be mechanically checkable, not just claimed
**Chose:** The model is asked to emit `[cite: section_path]` tags, and every
citation is checked against what was actually retrieved
(`unverifiable_citations`).
**Because:** A citation the model claims but that doesn't correspond to
retrieved content is a fabrication vector. Checking mechanically catches it
without relying on the model to self-report honesty.
**Cost (found empirically):** The check only works if the model reliably
emits the tag format. Observed at least one real case where a
factually-correct answer had ZERO citation tags -- the safety check was
silent not because nothing needed checking, but because it had nothing to
check. This became golden-set item citation-format-unreliable-001.

---

## D12 — Push identity/comparison judgments into deterministic code, not model reasoning
**Chose:** When our own code can establish a fact with certainty (e.g. "are
these N document versions byte-identical"), compute it directly and state
it in the prompt -- don't ask the model to derive it from raw text.
**Because:** Measured directly. A soft prompt instruction ("don't
misattribute facts across versions") fixed a real hallucination in only
1 of 3 test runs (see the still-unresolved diachronic-notification-event-001
golden-set item). A deterministic fact stated directly in context fixed a
different real hallucination in 3 of 3 test runs
(hallucination-fabricated-comparison-001). The difference: an instruction
competes with the model's own priors and can lose; a stated fact removes
the judgment call entirely.
**Cost:** Only applies where the fact is genuinely computable in code.
Doesn't generalize to judgments that require actual language understanding
(e.g. citation-format compliance, D11) -- that gap stayed open precisely
because there's no equivalent deterministic check available for it.


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

---

## D21 — Three real gaps found during golden-set exploration; batched for later fix
**Chose:** Continue golden-set growth toward 50 items (breadth first);
defer fixing the following three findings until exploration is complete,
so all fixes happen with full system context rather than piecemeal.
**Found, all via deliberate exploration, not assumption:**
  1. extract_citations() only matches numeric CFR citations
     ("314.2"-shaped), not regulation nicknames ("Reg E", "Reg P").
     multi-policy-recordkeeping-comparison-001 asked about "Reg E"
     explicitly and retrieval never found cfr-12-1005.
  2. Generation abstains even when retrieval correctly surfaces multiple
     relevant families (multi-policy-overlap-abstention-001) -- _SYSTEM_PROMPT
     was written for single-family/version comparisons, has no explicit
     instruction for cross-family synthesis.
  3. HISTORICAL intent branch existed in original design (intent.py
     comments still reference it) but was never rebuilt after the
     always-diachronic classifier bug fix. "As of [date]" questions
     silently fall through to DIACHRONIC. Verified NOT a correctness bug
     (3/3 explored cases answered correctly) but a real efficiency gap --
     pulls full version history (up to 8 chunks) instead of one targeted
     lookup. Real Phase 4 token-cost finding.
**Cost:** All three currently undocumented anywhere except individual
golden-set item rubrics. This entry is the index -- when golden-set
growth finishes, come back here for the fix list rather than
re-discovering these by re-reading 50 item rubrics individually.

---

## D22 — Phase 0's Cloud Run deploy exit criterion was never met; genuine gap, not a deferral
**Chose:** Nothing yet -- flagging this honestly rather than retroactively
justifying it as intentional.
**Because:** DESIGN.md Phase 0 exit criteria explicitly states "first
Cloud Run deploy succeeds." This never happened. Unlike every other
deferred item this session (D1, D18, D21), there was no explicit decision
to defer it, no stated reason, no tracked follow-up -- development simply
moved to local-only setup (WSL, venv, Docker Compose) and the actual
`gcloud run deploy` step was never executed. Caught only because it was
directly asked about, not because it was noticed proactively.
**Cost of the gap, concretely:**
  - The Dockerfile has never been built and run as an actual container --
    everything has run through the venv directly.
  - Cloud SQL / Memorystore connection behavior under real GCP networking
    (vs. Compose's service-name DNS) is unverified.
  - Secret Manager injection is untested -- all secrets so far have come
    from a local .env file, which does not exist on Cloud Run.
  - ADR-1's own stated cost (instance freezing between requests breaking
    background telemetry flush) has never been tested against real
    behavior.
**Next step:** A first real Cloud Run deploy attempt, now more valuable
than it would have been at Phase 0 since there's a real system (gateway,
generation, eval harness) to deploy rather than a bare health check --
likely to surface genuine integration issues worth finding now rather
than compounding further into Phase 4+.

---

## D23 — DECISIONS.md itself was missing D1-D12; reconstructed and verified
**Chose:** Reconstructed D1 through D12 from the actual decisions made
earlier in this session and merged them ahead of the existing D13-D22.
**Because:** User directly checked and found the file only started at
D13 -- the original file creation (a single large write, early in the
session) never actually landed on the user's machine, while every
subsequent entry (appended via separate commands) did. Root cause: no
verification step existed after the initial file creation to confirm it
matched what was shown. Same failure class as the golden-set duplication
bug (D-adjacent, untracked) -- an assumed state was trusted instead of
checked.
**Cost:** None going forward, but worth the general lesson: after any
"create this artifact" instruction spanning significant surrounding
conversation, verify the artifact's real state on the user's machine
before continuing to build on top of an assumption of what it contains.

---

## D24 — /healthz is a reserved path on Cloud Run; must rename the real liveness route
**Chose:** Rename the liveness endpoint from /healthz to /health-live (or
similar) for any Cloud Run deployment, including the eventual real
deploy of app/api/main.py.
**Because:** Discovered through a multi-step live debugging session:
/readyz worked perfectly on the deployed service; /healthz consistently
returned a Google-branded (not app-level) 404, on every URL form,
including through an authenticated gcloud run services proxy tunnel that
bypasses public edge routing entirely. No request ever appeared in
container logs. Confirmed via Google's own documented issues page
(cloud.google.com/run/docs/issues#ah) and corroborated by an identical,
independently-reported Streamlit issue: /healthz is a RESERVED path on
Cloud Run's serving infrastructure, intercepted before reaching the
container, regardless of whether your app defines a route there.
**Cost:** app/api/main.py (the REAL application, not just this scaffold
test) currently uses /healthz as its liveness route name and will hit
this exact same wall on its first real deploy if not renamed first.
This must be fixed in the real app before Phase 8's actual production
deploy, not just in this throwaway test. Also worth checking whether any
other path names we've used elsewhere collide with other GCP-reserved
prefixes -- this was found by accident, not by systematically checking
Google's reserved-path documentation in advance.
