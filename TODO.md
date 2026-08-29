# Outstanding work tracker

Derived from a full audit of DESIGN.md's phase roadmap against DECISIONS.md
(D1-D55), current Terraform state, cloudbuild.yaml, and the golden set
(2026-08-29). Superseded D46's stale tracker. Work top to bottom within each
tier; re-prioritize freely as items get fixed or new gaps surface.

Status legend: `[ ]` not started, `[~]` in progress, `[x]` done.

## P0 -- correctness / security, blocks calling this production-ready

- [~] **1. D20 grounding bug**: diachronic date-attribution hallucination.
  Narrowed, not closed (D56): the retry loop now targets the actual
  flagged sentence instead of a generic warning, and the check itself
  had four real bugs fixed (sentence-splitting, substring vs whole-word
  matching, word-selection, markdown stripping). Verified live: last 5/5
  uncached real runs correct and grounded, one earlier run's exact wrong
  claim caught and self-corrected on retry. The underlying model
  tendency to conflate "introduced in X" with "retained since X" is not
  claimed fixed, only caught/corrected more reliably. One residual
  heuristic false-positive seen once, not reproducible offline.
- [ ] **2. Ingest path is broken-as-deployed**: `infra/jobs.tf`'s
  `google_cloud_run_v2_job.ingest` cannot actually run -- eCFR blocks
  Cloud Run's outbound IP range (D49). Real ingest path is a manual
  Cloud SQL proxy tunnel from a local machine, which temporarily makes
  the DB publicly reachable (password-only). Either fix Terraform to stop
  implying an unusable automated path exists, or solve the IP-block
  problem for real (e.g. Cloud NAT with a static egress IP, if eCFR would
  allowlist it).
- [~] **3. LiteLLM gateway has no real service-to-service auth**: made fully
  public (`allUsers` + `run.invoker`) as a workaround (D50). Investigated
  (D57): the originally-planned identity-token fix was structurally
  broken (Cloud Run IAM and LiteLLM's own key both wanted the
  `Authorization` header), and internal-only ingress needs real VPC/DNS
  work with genuinely conflicting requirements across sources. Found the
  real fix instead -- Cloud Run's `X-Serverless-Authorization` header,
  which lets the identity token and LiteLLM's key coexist. **Code done**:
  `app/rag/generate.py` fetches and attaches the token, no-ops locally.
  **Still pending**: restrict `infra/iam.tf`'s `litellm_public` binding
  away from `allUsers` and live-test against the deployed `/query`
  endpoint -- deliberately not applied blind this session.

## P1 -- eval/pipeline completeness (can't trust the numbers yet)

- [ ] **4. Golden set stuck at 16/50 items**; grow toward the original target.
- [ ] **5. Judge calibration never done** (Cohen's kappa between the LLM judge
  and a human rater) -- no confidence in how much to trust ragas scores.
- [ ] **6. 3 golden-set items with generation never actually verified**
  (retrieval-only, marked TODO in `evals/datasets/golden_set.yaml`):
  `lookup-classifier-lexical-gap-001`, `lookup-classifier-lexical-gap-002`,
  `lookup-resolve-padding-001`. Re-run once API access/budget allows, then
  upgrade `severity` if any answer is actually wrong.
- [ ] **7. Citation-format compliance**: model inconsistently emits the
  `[cite: ...]` tag format the system prompt asks for. Currently caught
  (`unverifiable_citations`) but not corrected -- decide whether to fix
  via prompt, post-processing, or accept as a known limitation.

## P2 -- production hardening / observability (Phase 8 leftovers)

- [ ] **8. No rollback trigger, dashboards, or alerts.** `cloudbuild.yaml`
  deploys to a `--tag=canary` revision with `--no-traffic` but nothing
  automates promotion or rollback, and there's no monitoring on top of
  Cloud Run's defaults.
- [ ] **9. Phase 5 latency work never re-measured against real cloud infra**
  (D50) -- only ever benchmarked locally; cold starts, VPC connector
  hops, and cross-service network time are unquantified in production.

## P3 -- housekeeping

- [x] **10.** `CLAUDE.md` -- decided to keep it local rather than commit;
  added to `.gitignore` instead.
- [x] **11.** Root `litellm.Dockerfile` was an orphaned duplicate (never
  referenced by any build step -- `cloudbuild-litellm.yaml` builds from
  `infra/litellm.Dockerfile` via `dir: infra`). Removed; only the
  `infra/` copy remains.

## Already verified fixed (no action needed, listed so we don't re-litigate)

- [x] D21's three batched gaps -- Reg E/nickname matching
  (`app/rag/retrieval.py`), cross-family synthesis abstention and
  HISTORICAL intent branch (`app/rag/intent.py`, `app/rag/generate.py`)
  all confirmed present in current code.
- [x] CI gate re-enabled in the pipeline (D54) -- runs as a real Cloud Run
  Job step in `cloudbuild.yaml`, not disabled/placeholder.
- [x] API service Cloud Run Terraform resource, schema-apply automation,
  README, architecture diagram -- all exist despite D46 listing them as
  gaps (that tracker predates them).
