# Debugging Methodology — Learned From a Real Investigation

This document extracts a reusable debugging process from one real incident:
a Cloud Run deploy where `/healthz` returned a 404 while `/readyz`, in the
same container, worked perfectly. The bug itself doesn't matter much. The
**method** that found it does — this is written so you can reapply that
method to a problem that looks nothing like this one.

**The core principle, stated once, referenced everywhere below:**
> When something fails mysteriously, don't guess and patch. Form a specific,
> falsifiable theory, find the cheapest test that could disprove it, run
> that test, and only move to the next theory once the current one is
> actually eliminated by evidence — not just abandoned because you got
> tired of it.

---

## The shape of a debugging session

Every investigation below follows the same skeleton:

1. **State what you observe** — not what you assume caused it.
2. **Form ONE specific theory** — vague theories ("something's wrong with
   networking") can't be tested. Specific ones ("the URL I'm using doesn't
   match the real service URL") can.
3. **Find the cheapest command that could prove the theory wrong** — not
   the most thorough one. Cheap and fast means you can run five theories
   in the time one expensive investigation takes.
4. **Run it. Read the actual output, not the summary.**
5. **If disproven, the theory is DEAD — don't keep circling back to it.**
   Move to the next one, informed by what you just ruled out.
6. **If proven, stop guessing and fix the real thing.**

---

## Step 1 — Establish the baseline observation

**What we saw:** `/readyz` returned `200 OK`. `/healthz`, on the identical
deployed service, returned a 404 — but not our application's 404. A
Google-branded HTML error page.

**Why this distinction mattered immediately:** FastAPI's own "route not
found" response is plain JSON: `{"detail":"Not Found"}`. A styled HTML page
with Google's logo is a different system's error page entirely. This single
observation — reading the *content* of the error, not just the status code
— was the first real clue, and it came from looking closely rather than
seeing "404" and assuming "route problem."

**Lesson:** Before forming any theory, look hard at exactly what came back.
A 404 is not just a 404 — *whose* 404 it is tells you which system produced
it.

---

## Step 2 — Theory 1: "The two endpoints behave differently because the
route doesn't exist in the deployed code"

**Command:**
```bash
grep -n "@app.get\|def healthz\|def readyz" /tmp/phase0-deploy-test/main.py
```

**Why this exact command:** Cheapest possible test of "does the route exist
in the file that was actually deployed." Not "does it exist in the file I
think I wrote" — the *actual file in the actual deploy directory*. This
session had already found once (the golden-set duplication, the missing
`DECISIONS.md` entries) that assumed file state and real file state can
silently diverge. Grepping the real file costs nothing and eliminates an
entire category of theory in one command.

**Result:** Route existed, correctly decorated, right where expected.
**Theory eliminated.** Not "probably fine" — confirmed, with evidence, and
crossed off the list permanently.

---

## Step 3 — Theory 2: "Wrong URL — I'm testing against a URL that isn't
actually the service"

**Command:**
```bash
gcloud run services describe policy-copilot-phase0-test \
  --region us-central1 --project sentinel-desk-dev \
  --format="value(status.url)"
```

**Why this exact command:** Don't trust a URL you copied from deploy output
several steps ago, or one you're recalling from memory. Ask the *authority*
— the service itself, via the tool that manages it — what its real URL is,
right now. This is the general pattern: when two things should match and
don't, go to the source of truth, not to your memory of the source of truth.

**Result:** The URL being tested (`...-295685350939...run.app`) genuinely
differed from the URL `gcloud` reported as canonical
(`...-zfgsnt3knq-uc.a.run.app`). Real discrepancy found.

**But — and this is important — retesting against the correct URL
reproduced the EXACT SAME 404.** The theory wasn't fully wrong (a real
mismatch existed), but it wasn't the *actual* explanation for the bug. Both
URLs led to the same underlying service, and the failure was identical on
both.

**Lesson:** A theory can be "true" (a real mismatch existed) without being
"the cause" of the symptom you're chasing. Confirm the fix actually
resolves the original symptom before declaring victory — don't stop
investigating just because you found *something* wrong.

---

## Step 4 — Theory 3: "The container is crash-looping; Cloud Run is
serving its own generic error because there's no healthy instance"

**Command:**
```bash
gcloud run services logs read policy-copilot-phase0-test \
  --region us-central1 --project sentinel-desk-dev --limit 50
```

**Why this exact command:** If the container were crashing, the logs would
show a Python traceback, an exit code, a restart loop — direct, unambiguous
evidence. This is always worth checking early when a service "seems broken"
in any way, because it's cheap, fast, and either confirms or eliminates an
entire class of problem (anything happening *inside* your process) in one
shot.

**Result:** Logs showed a completely clean startup (`Application startup
complete`), and — critically — **two successful `GET /readyz` requests,
fully logged, with real timestamps.** Meanwhile, `/healthz` never appeared
in the logs at all. Not as a failed request. Not logged at all.

**Theory eliminated, and something new discovered:** The container is
healthy. The problem isn't inside the app. And the absence of `/healthz`
from the logs is itself a critical clue — it means the request never
reached the container in the first place.

**Lesson:** Sometimes a test doesn't just eliminate a theory — it hands you
the next one for free. "Present in logs" vs "absent from logs" reframed the
entire investigation from "why does the app respond wrong" to "why doesn't
the request arrive at all."

---

## Step 5 — Theory 4: "Cloud Run's own health-check probe is intercepting
`/healthz` for its own internal use"

**Command:**
```bash
gcloud run services describe policy-copilot-phase0-test \
  --region us-central1 --project sentinel-desk-dev \
  --format="yaml(spec.template.spec.containers[0].startupProbe, \
                  spec.template.spec.containers[0].livenessProbe)"
```

**Why this exact command:** If Cloud Run had auto-configured an HTTP probe
against `/healthz`, that configuration would be visible directly in the
service's own spec — no guessing needed, just ask the platform what it's
actually configured to do.

**Result:** The startup probe was a plain **TCP** check (`tcpSocket: port:
8080`) — it never sends an HTTP request to any path at all. This theory is
**structurally impossible** given the evidence: a TCP-only probe cannot be
the thing intercepting a specific HTTP path.

**Theory eliminated cleanly, by direct configuration inspection — not
inference.**

**Lesson:** When you have a specific, checkable theory, check it directly
against the system's own configuration rather than reasoning about what it
*probably* does. This test took 10 seconds and closed a plausible-sounding
theory permanently, instead of leaving it as a nagging "maybe" hanging over
the rest of the investigation.

---

## Step 6 — Isolating the layer: bypass public routing entirely

**Command:**
```bash
gcloud run services proxy policy-copilot-phase0-test \
  --region us-central1 --project sentinel-desk-dev --port 9090
# in a second terminal:
curl -s -w "\n[%{http_code}]\n" http://localhost:9090/healthz
```

**Why this exact command:** At this point, every application-level and
config-level theory had been eliminated. The remaining question was
architectural: *which layer* is producing this 404 — your container, or
something in front of it? `gcloud run services proxy` creates an
authenticated tunnel that talks directly to the Cloud Run service,
bypassing the public internet's edge/load-balancing layer entirely. If the
bug disappeared through this tunnel, the cause would be edge/public-routing
related. If it persisted, the cause was deeper in Cloud Run's own serving
infrastructure — a stronger, more specific test than anything before it.

**Result:** The identical 404 reproduced, even through the authenticated
proxy. This is strong evidence the interception happens at the platform's
core routing layer, not at a public-internet-specific edge.

**Lesson:** When you can't isolate a problem by removing infrastructure
piece by piece in code, look for a tool that lets you bypass a whole layer
at once. A proxy tunnel is a scalpel that cuts the investigation in half
with one command, rather than requiring you to reason through every
possible layer individually.

---

## Step 7 — When local reasoning runs out, search for prior art

**What we did:** Searched directly for the exact symptom —
`cloud run "/healthz" reserved path returns 404` — rather than continuing
to invent new theories from first principles.

**Why this was the right moment to search, not earlier:** Searching too
early risks anchoring on someone else's (possibly wrong) explanation before
you've built your own understanding of the evidence. Searching *after*
systematically eliminating five theories means you arrive with sharp,
specific context — "TCP probe ruled out, proxy tunnel still fails, app logs
clean" — which makes it much easier to recognize the *right* answer in
search results instead of a plausible-sounding wrong one.

**Result:** Found two independent confirmations: Google's own documented
issues page stating `/healthz` is a reserved path on Cloud Run, and a
completely unrelated project (Streamlit) that hit the exact same symptom
with the exact same fix.

**Lesson:** Some platform behaviors are genuinely undocumented-in-the-obvious-place
but well-known in aggregate — searched community reports and vendor issue
trackers often surface exactly this class of gotcha. The skill isn't
knowing the answer in advance; it's knowing *when* your own evidence has
narrowed the problem enough that a search will actually land on the right
thing instead of a red herring.

---

## The full theory-elimination trail, as a table

| # | Theory | Test | Result |
|---|---|---|---|
| 1 | Route doesn't exist in deployed code | `grep` the real deployed file | Eliminated — route exists correctly |
| 2 | Wrong URL being tested | `gcloud run services describe ... status.url` | Real mismatch found, but not the cause — same bug on correct URL |
| 3 | Container crash-looping | `gcloud run services logs read` | Eliminated — clean startup, `/readyz` logged and healthy |
| 4 | Cloud Run's own probe intercepting the path | `gcloud run services describe ... startupProbe` | Eliminated — probe is TCP-only, can't intercept a specific HTTP path |
| 5 | Edge/public-routing-layer issue specifically | `gcloud run services proxy` + curl through tunnel | Eliminated — identical failure even bypassing public routing |
| 6 | Platform-reserved path (found via search) | Confirmed against Google's own docs + independent report | **Confirmed as the real cause** |

---

## The transferable checklist

Next time something fails mysteriously — in this project or any other —
work through these in order, and don't skip a cheap check because a later
one feels more likely to be "the real answer":

1. **Read the actual error content, not just the status/exit code.** Whose
   error is it? What system's fingerprint is on it?
2. **Verify the file/config you're testing against is the file/config you
   think it is.** Grep the real thing. Don't trust memory of what you wrote.
3. **Verify identifiers (URLs, IDs, paths) against an authoritative source**
   — the tool that manages the resource, not a value copied several steps
   ago.
4. **Check logs at the layer closest to where the failure might originate**
   before assuming anything about layers further away.
5. **If you have a specific, checkable theory about configuration, check
   the configuration directly** — don't reason about what a system
   "probably" does when you can just ask it.
6. **If you can bypass a whole layer with one tool (a proxy, a direct
   exec-into-container, a local reproduction), do that before trying to
   reason through the layer piece by piece.**
7. **Search only after you have specific, narrowed context** — vague
   searches early return vague or misleading answers; specific searches
   after real elimination tend to land precisely.
8. **Every eliminated theory should be marked dead and never revisited**
   unless new evidence resurrects it. Don't let a debugging session become
   circular.

The one-line version, worth pinning above your desk:
**"What's the cheapest thing I could check right now that would prove my
current best guess wrong?"** Then go check it.