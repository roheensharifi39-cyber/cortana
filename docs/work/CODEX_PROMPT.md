# Prompt template for a coding agent (Codex, Claude Code, etc.)

Copy one of the ready-made prompts below into a fresh session. Replace `<PACKET>` if you use the
generic form.

---

## The generic prompt

```
You are joining CortexAi, an in-progress hackathon project. Demo is Sunday.

STEP 1 — READ BEFORE WRITING ANYTHING. In this order:
  1. docs/GUIDE.md             the complete picture — product, status, what is real, house rules
  2. docs/status.md            deeper detail on risks and open decisions
  3. docs/work/README.md       how work is split so we do not collide
  4. docs/work/<PACKET>.md     YOUR brief — scope, tasks, done-criteria
  5. docs/contracts.md         only if your packet touches the API or event stream

Do not skim these. The briefs contain traps that are not obvious from the code, and every
one of them has already cost someone hours.

STEP 2 — ORIENT. Before changing anything:
  git log --oneline -15
  pytest -q                          # 135 tests, under a second
  ls docs/work/fixtures/             # real captured backend output

STEP 3 — CONFIRM, THEN BUILD. Tell me in three or four sentences what you understand your
packet to be and what you intend to change, then start. If the brief and the code
disagree, say so rather than guessing which is right.

RULES THAT ARE NOT NEGOTIABLE (docs/GUIDE.md §14):
  - Never show a green check you did not earn. "unverified" is not "pass"; "configured"
    is not "connected".
  - Fail closed, and say which kind of no it was: evidence says no, versus we could not
    get the evidence. They must never render the same.
  - No secret ever reaches the browser or an event payload.
  - No fake progress. UI state comes from events, never from a timer.
  - Deterministic code decides; a model may explain but never decide.
  - Never weaken a check or an assertion to make something pass. A failing test is
    information. If a rule blocks you, say so — do not route around it.

SCOPE:
  Work ONLY in the files your brief lists as yours. Other packets are being worked in
  parallel by other sessions. Reading another packet's file is fine; editing it causes a
  merge conflict that costs us more than your change is worth. If you need something
  outside your scope, write down exactly what you need and tell me.

  Branch: git checkout -b <PACKET-short-name>

VERIFY BEFORE YOU SAY YOU ARE DONE:
  pytest -q                             # must stay green
  npm run typecheck && npm run build    # if you touched src/
  python scripts/run_all.py --fresh     # then, in a second terminal:
  python scripts/smoke_test.py          # must pass all five scenarios

  TRAP: only one instance can hold the ports. If run_all.py was already running, the new
  one fails to bind and you will be testing stale services with accumulated state. This
  has already caused three confusing debugging sessions. If results look strange, check
  this first.

REPORTING:
  Tell me what you actually verified, not what you expect to work. If something is
  half-done, say which half. If you could not test something, say so and why. I would
  much rather hear "this compiles but I could not run it" than find out on Sunday.
```

---

## Ready to paste — P7, live discovery

Fully unblocked, highest ceiling, needs no credential.

```
You are joining CortexAi, an in-progress hackathon project. Demo is Sunday.

Read these before writing anything, in order:
  docs/GUIDE.md, docs/work/README.md, docs/work/p7-live-discovery.md

Then orient: git log --oneline -15 && pytest -q

Your packet is P7 — live discovery across GoDaddy's real ANS registry. The key facts,
all already verified so do not re-investigate them:

  - GET https://api.godaddy.com/v1/ans/registered-agents answers with NO credential,
    including ?query= text search and ?capabilities= exact filtering.
  - There are 216,110 registered agents. Roughly 200k are auto-generated customer
    support bots that all declare the same two functions and can do no real work.
  - Our capability vocabulary (brand.identity, site.generate, compliance.review) matches
    ZERO of them. Registry capability names are free text: "Generate Logo",
    "Generate Website", "Translate Text".
  - Text search alone is a trap: ?query="build a landing page" returns support bots for
    businesses with "Landing" in their name.

Build the two-stage strategy described in the brief: cast with ?query=, harvest the
capability names the results actually declare, then filter precisely with ?capabilities=,
and let the existing Trust Gate capability check reject the rest.

Put the strategy in a NEW module mc/discovery.py with pure, testable functions. The only
change outside it is inside discover() in services/agents/commander.py — P3 owns the rest
of that file, so keep your diff inside that one function.

Critical, and nuanced — most real agents cannot be hired, but some can:

  - GoDaddy's own Website Builder Agent: DNS does not resolve publicly. Unreachable.
  - GoDaddy's Logo Generation Agent: 401. shopagent.cloud: needs ansMtls/ansJwt/apiKey.
  - BUT the Webmesh fleet is deliberately open: agent.webmesh.ai exposes verify,
    discover and interact over A2A JSON-RPC with noAuth, and we have successfully
    called it. supplier.webmesh.ai answers get_quote with noAuth too.

So: DO NOT FAKE A HIRE, and do not claim a refusal that did not happen either. Where an
agent genuinely cannot be invoked, have the Commander verify what it can, stop at the
authorization boundary, say exactly why, and fall back to an agent we do have authority
over. Where one genuinely can be invoked, invoking it is the better demo. That honest
boundary is the point of the product, not a failure of it.

Keep the simulator path unchanged — ANS_BACKEND=sim must behave exactly as it does today.
Bound the search: pages, candidates and wall-clock time.

Verify with: pytest -q, then python scripts/run_all.py --fresh and
python scripts/smoke_test.py in a second terminal. All five scenarios must still pass.

Branch: p7-discovery. Work only in the files your brief lists. Report what you actually
verified, not what you expect to work.
```

---

## Ready to paste — P1, connect the frontend

Critical path. The Next.js app currently makes zero network calls.

```
You are joining CortexAi, an in-progress hackathon project. Demo is Sunday.

Read these before writing anything, in order:
  docs/GUIDE.md, docs/work/README.md,
  docs/work/p1-frontend-adapter.md, docs/contracts.md

Then orient: git log --oneline -15 && pytest -q && ls docs/work/fixtures/

Your packet is P1 — the frontend data adapter. The Next.js app is complete, builds clean
and makes NO network calls at all: HttpControlApi.ts throws NOT_CONFIGURED, the provider
is hardwired to a localStorage mock, and live mode reports available: false. The backend
is finished and running. Your job is to connect them WITHOUT changing a single component.

The shapes do not line up, because the frontend was built from a design brief while the
backend grew into docs/contracts.md. You are writing the translation layer. Real captured
backend output is in docs/work/fixtures/ — 95 events, one example of each of the 32 event
types, and every REST response. Build against those; they are exact.

Three rules for the mapping, in order of importance:
  1. Never invent a value. If the backend does not send it, map it to null. Do not
     compute a plausible-looking number.
  2. Never collapse "unverified" into "pass". Trust checks have four states: pass, fail,
     unverified, not_run. If the target enum has no room for unverified, WIDEN THE ENUM.
     Rounding it to something that looks fine destroys the product's whole claim.
  3. Carry the detail through. The five per-check rows, their detail strings and their
     evidence objects are the product. Losing them in translation is the failure mode.

Backend down must render as "backend unavailable", never as an empty successful-looking
dashboard.

Read dashboard/app.js first — it is plain JavaScript, about 60 lines of data handling,
and it is wired to everything. It is the reference implementation for what you are doing.

Verify: npm run typecheck && npm run build, then run the backend with
python scripts/run_all.py --fresh and confirm live mode shows a real mission.

Branch: p1-adapter. Work only in src/lib/api/, src/lib/env/config.ts and
src/components/provider.tsx. Do not touch src/contracts/index.ts — P2 may be extending
it. Report what you actually verified.
```

---

## Why the prompts are shaped this way

**Read first, in a fixed order.** A coding agent that starts editing before reading `onboarding.md`
will reinvent things that exist and violate rules it never saw.

**The traps are stated explicitly.** "Only one process can hold the ports" and "`unverified` is not
`pass`" are not discoverable from the code in a reasonable time. Both have already cost hours.

**Scope is a hard boundary.** Several sessions run in parallel. A helpful two-line fix in someone
else's file is a merge conflict, and merge conflicts on Saturday night are how demos die.

**Verification is named, not implied.** "Make sure it works" gets you a claim. `pytest -q` and
`scripts/smoke_test.py` get you evidence.

**Honest reporting is asked for directly.** Cheerful over-reporting from an agent is worse than a
plain "I could not test this" — you find out on Sunday instead of Saturday.
