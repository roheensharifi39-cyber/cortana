# CortexAi — the complete guide

**This is the single source of truth.** If another document disagrees with this one, this one is
right and the other is stale. Paste this whole file into any AI assistant before asking it for
help with the project.

Last verified: 19 September 2026. Every number here was measured, not estimated.

---

## 1. What this is

A person types one goal in plain English — *"Launch an online presence for Hokie Bites, a food
truck in Blacksburg."*

A **Commander** agent works out what that involves, discovers specialist agents belonging to
*other organisations*, proves who they are through **GoDaddy's Agent Name Service (ANS)**, gives
each one narrowly scoped permission for that mission only, watches what they do, blocks what they
are not allowed to do, verifies the signed work they return, and produces a real finished website.

Built for VTHacks 14, GoDaddy **"Best Use of ANS"** track. Ship Sunday 8 AM, judging 10:30 AM,
science-fair format.

### The one idea everything serves

> **Verified identity is not unlimited authority.**

Knowing exactly who an agent is tells you nothing about what it should be allowed to do. Two
different questions, answered separately and visibly:

- **ANS** answers *who is this?* — identity, discovery, lifecycle, revocation.
- **We** answer *may it do this, here, now?* — scopes, grants, policy.

Anything that blurs those two is a bug, even when it makes a demo smoother.

GoDaddy's own workshop deck says the same thing: *"Never let discovery alone authorize an
action."* Their docs say ANS "does not establish that the caller is authorized to use the agent."
We built the layer they say is needed and deliberately did not build.

---

## 2. Status right now — the honest version

| | State |
|---|---|
| **Backend** | Complete. 135 unit tests, all five demo scenarios green end to end. |
| **Frontend** | Next.js, 6 routes, builds clean — and makes **zero network calls**. |
| **ANS discovery (live)** | **Working.** No credential. 216,110 real agents in production. |
| **ANS stranger verification** | **Working.** Real GoDaddy-issued certs verified by our own code. |
| **A2A interaction with a stranger** | **Working.** Proven against `agent.webmesh.ai`. |
| **ANS registration (live)** | Blocked — needs an API key from the GoDaddy table. |
| **LLM** | **Nothing is using one.** Two keys tried, both dead. Running on templates. |
| **Domain** | `getcortex.vip` at Porkbun. Wired as a switch; both modes pass the full demo. |
| **Name** | CortexAi. Settled. Anything saying PERIHELION, APHELION or Mission Control is stale. |

**The two things that decide the demo:** the frontend is not connected to the backend, and no
agent is thinking. Everything else is polish or reach.

### Size

```
4,667 lines  application Python      (mc/, services/, scripts/)
1,169 lines  tests                   (135 passing, under a second)
3,889 lines  TypeScript/TSX          (src/, e2e/)
   32        distinct event types
```

---

## 3. Vocabulary

| Term | Means |
|---|---|
| **ANS** | Agent Name Service. GoDaddy's registry: agents register a name, prove they control the domain, get a certificate. DNS for AI agents. |
| **ANS name** | `ans://v1.0.0.sitebuilder.sitesmith.xyz` — version, label, domain. The version is part of the identity: v1.0.0 and v1.1.0 are *different agents*. |
| **Agent card** | A JSON document at the agent's URL saying who it is and what it does. |
| **Trust card** | `/.well-known/ans/trust-card.json` — ANS name, keys, X.509 chain, transparency receipt. How a stranger proves identity offline. |
| **Trust Gate** | Our five checks before trusting another agent. |
| **Guardian** | Our policy layer. Decides whether a specific action is allowed. |
| **Grant** | Permission for one agent, on one mission, with named scopes, that expires. |
| **Scope** | `http.fetch:fonts.googleapis.com` — an action paired with where or what. |
| **Capability** | A kind of work: `brand.identity`, `site.generate`, `compliance.review`. |
| **Transparency log** | Append-only Merkle log of registrations. Proves a registration happened. |
| **Status token** | Short-lived signed proof an agent is in good standing *right now*. |
| **Mission / task / job** | Mission = the whole goal. Task = one step in the plan. Job = one task handed to one agent. |
| **A2A** | Agent-to-agent protocol, JSON-RPC. How strangers exchange messages. |
| **MCP** | Model Context Protocol. How an agent exposes tools. |

GoDaddy's framing: **"MCP connects. A2A coordinates. ANS verifies."**

---

## 4. The cast

Five fictional companies, all built by us. Each is a separate process with its own ANS identity
and its own private key.

| Agent | Org | Port | Does |
|---|---|---|---|
| Commander | LaunchPad | 8001 | Plans, hires, verifies, assembles |
| Brand agent | BrandStudio | 8002 | `brand.identity` — tagline, colours, fonts |
| Site builder | WebForge | 8003 | `site.generate` — a one-page site |
| Backup site builder | SiteSmith | 8004 | `site.generate` — same job, different company |
| Compliance agent | LegalCheck | 8005 | `compliance.review` — legal and accessibility check |
| Impostor | — | 8006 | Not in ANS. Copies BrandStudio's public identity and bids for work. |

Plus the **hub** (8000: event stream, Guardian, dashboard) and the **ANS simulator** (8100).

**Important:** the Commander does *not* register agents. Each agent registers *itself* at startup
— generates its own key, builds a CSR, proves it controls its own domain, receives a certificate.
If the Commander could mint identities the whole trust model would collapse, and the impostor
demo would be meaningless.

---

## 5. Architecture

```
dashboard / Next.js app ──WebSocket──▶ hub :8000 ◀── events from every agent
                                         │  │
                                         │  └── Guardian: grants, scopes, action decisions
                                         ▼
                                  Commander :8001 ──hires──▶ vendors :8002-8005
                                         │                   (impostor :8006, not in ANS)
                                         ▼
                         ANS: local simulator :8100, or GoDaddy's hosted API
```

Three boundaries:

- **Trust Gate** — who gets in. Runs inside *every* agent, not just the Commander: vendors verify
  the Commander's signed job request before doing any work. Trust goes both ways.
- **Guardian** — what they may do once in. Lives in the hub specifically so it can refuse the
  Commander *and* the agents. Neither can switch it off.
- **ANS** — identity only. Real cryptography either way.

---

## 6. The Trust Gate — five checks, four states

Run before trusting any agent. Each produces its own result and its own evidence.

| # | Check | Question | How |
|---|---|---|---|
| 1 | `resolve` | Is it registered? | ANS lookup, fetch its agent card |
| 2 | `authenticate` | Does it hold the private key? | It signs a random nonce; we verify against the public key in its ANS identity certificate, which must chain to the ANS CA and name this exact ANS name |
| 3 | `status` | Still in good standing? | Two things: a Merkle inclusion proof it was registered, **and** a fresh signed status token saying it is ACTIVE now |
| 4 | `capability` | Does it do this job? | Its registered function names contain the capability |
| 5 | `policy` | Do *we* allow it? | Domain allowlist, approved versions, and it is answering from the address ANS lists |

**Each check has four states, not two:**

```
pass       the evidence says yes
fail       the evidence says no
unverified we could not obtain the evidence
not_run    an earlier check failed, so we stopped
```

`unverified` refuses the hire — we fail closed — but it is **not** the agent's fault and must
never be drawn like `fail`, and absolutely never like `pass`.

### The distinction that matters most, in check 3

A **transparency-log receipt** proves a registration happened. It stays valid forever, including
ten minutes after the agent is revoked. A **status token** is short-lived, signed, and says the
agent is in good standing *now*. Only the second can catch a revocation.

Conflating them was a real bug we fixed. **Do not merge checks 2 and 3 into one "verify identity"
step** — an outside suggestion to do that would destroy the project's best technical point.

Agents attach their own status token (`X-ANS-Status-Token`) to every response, so we verify it
**offline** against root keys we already hold. Presented tokens are pinned to their subject,
because a valid ACTIVE token might be about somebody else.

### Eight lifecycle states, not four

Real ANS has `PENDING_VALIDATION`, `PENDING_CERTS`, `PENDING_DNS`, `ACTIVE`, `DEPRECATED`,
`REVOKED`, `EXPIRED`, `FAILED`. All non-ACTIVE states refuse the hire, but the three `PENDING_*`
report as `unverified` (our registration is unfinished — the agent did nothing wrong) while the
rest report as `fail`. An unrecognised future state fails closed.

---

## 7. The Guardian

Passing the Trust Gate gets an agent through the door. It says nothing about what it may touch.

When the Commander hires, it asks the Guardian to record a **grant** — *this agent, this mission,
these scopes, expiring* — over a signed request. Then every sensitive thing the agent wants to do
comes back and asks.

```
1. Hard denies      secrets.read, key.export, ans.revoke, guardian.disable
                    Refused before grants or scopes are even looked at.
                    NO HUMAN CAN APPROVE THESE.
2. Grant            no grant, wrong mission, wrong agent, or expired -> denied
3. Scope            http.fetch:fonts.googleapis.com is a different permission
                    from external.upload:anywhere.example.net
4. Human review     outward-facing actions (publish, email) stop and ask a person,
                    bound to an exact payload digest, usable exactly once
```

Network actions go out **through** the Guardian, against its own allowlist, so a denied upload is
denied in the only sense that counts: nothing left the building. Every decision is plain
deterministic code — the model explains, it never decides.

---

## 8. Missions are dependency graphs

The planner asks a model for a plan, then **validates it server-side before anything runs**: no
cycles, no dangling dependencies, no capability we cannot source, one to six tasks. If validation
fails, the specific complaints go back to the model once. If that fails too, a deterministic
three-task plan runs.

A plan that survives validation may still be a *bad* plan. What it cannot be is unrunnable.

Work the mission asks for that we have no capability for lands in `plan.unsupported` and is said
out loud, never faked.

---

## 9. What is REAL and what is SIMULATED

This is the question a judge will ask, and the answer must be exact.

### Genuinely real, identical either way

- Key generation, CSRs, X.509 parsing and chain verification
- ECDSA and RSA signature creation and verification
- Merkle inclusion proofs
- Status token signing, freshness and subject pinning
- The whole Trust Gate decision logic
- The Guardian policy engine
- All orchestration, recruitment and replacement

### Simulated by default

Where the registry lives. `ANS_BACKEND=sim` runs a local Registration Authority, CA, Merkle
transparency log and DNS on `:8100`. It is **not a mock** — it implements the same protocol with
real cryptography. A forged certificate carrying the same ANS name and subject is rejected.

### Genuinely live against production GoDaddy, today, no credential

- **Discovery.** `GET https://api.godaddy.com/v1/ans/registered-agents` — 216,110 agents,
  `?query=` text search and `?capabilities=` exact filter both work unauthenticated.
- **Stranger identity verification.** Agents publish `/.well-known/ans/trust-card.json` with an
  X.509 chain issued by *GoDaddy Private ANS Issuing CA*. We verify that chain, confirm the ANS
  name is bound into the certificate's SAN, and reject a forgery carrying the same name.
- **A2A interaction.** `agent.webmesh.ai` exposes `verify`, `discover` and `interact` over A2A
  JSON-RPC with `noAuth`. We have successfully called it and received a real verification verdict.

### Not done at all

- Registration against live ANS — needs an API key
- SCITT COSE receipts — GoDaddy's are COSE_Sign1, ours are JSON; `mc/merkle.py` cannot read theirs
- mTLS / DPoP — we use our own nonce challenge
- The CA trust anchor for hosted mode — `ANS_CA_BUNDLE_PATH` is unset

### The honest sentence for a judge

> The cryptography, the trust decisions and the policy enforcement are real and tested end to end.
> By default the registry is ours, running locally with real crypto. Against production GoDaddy we
> can already discover 216,000 agents and verify a stranger's identity certificate — what we have
> not done is register our own agents there, because that needs a credential.

---

## 10. The live ANS findings

### Discovery has a trap

Our capability names (`brand.identity`, `site.generate`) match **zero** of 216,110 agents.
Registry capability names are free text chosen by registrants: `Generate Logo`, `Generate
Website`, `Translate Text`.

Text search alone is worse than useless — roughly 200k of those agents are auto-generated customer
support bots, and `?query=build a landing page` returns support bots for businesses with
"Landing" in their name, all declaring `Answer Questions, Order Lookup`.

The working strategy is two-stage: **cast** with `?query=`, **harvest** the capability names the
results actually declare, then **filter** with `?capabilities=`. The Trust Gate's capability check
rejects the rest.

### Who you can and cannot call

| Agent | Reachable? |
|---|---|
| `agent.webmesh.ai` — verify/discover/interact | **yes, noAuth, proven working** |
| `supplier.webmesh.ai` — travel supplier reference | yes; `get_quote` noAuth, `book_flight` needs a mandate |
| `fraud.webmesh.ai` — 13-attack adversary | yes, noAuth |
| GoDaddy Website Builder (`Generate Website`) | no — DNS does not resolve publicly |
| GoDaddy Logo Generation (`Generate Logo`) | no — 401 |
| `shopagent.cloud` | no — needs `ansMtls` / `ansJwt` / `apiKey` |

### The Webmesh reference deployment

The sponsor runs a complete live example. Treat it as the reference implementation:

```
agent.webmesh.ai       verify / discover / interact
supplier.webmesh.ai    the defended agent
authority.webmesh.ai   signs spending mandates
fraud.webmesh.ai       13 attacks: replay, tamper, quote-swap, wrong-audience,
                       wrong-scope, wrong-key, corrupt-JWS, unknown-key,
                       canonicalization, card-drift
rogue-supplier.webmesh.ai   valid ANS identity, skips required checks
```

Their agent cards are **JWS-signed** against the key in their trust card. That is card-tamper
detection, and the fraud agent treats card drift as an attack.

Saved as fixtures in `docs/work/fixtures/webmesh/`.

---

## 11. The LLM situation

**No agent is currently using a language model.** Every mission runs on hand-written deterministic
templates in `mc/skills.py`.

Two keys have been tried and both failed:

| Key | Result |
|---|---|
| `AIza…6go` | Authenticates, lists 58 models, project has `quota_limit_value: '0'` |
| `AQ.Ab8…sIQ` | `401` on all three auth mechanisms; wrong format entirely (`AQ.` + 53 chars, not `AIza` + 35) |

**The fix:** aistudio.google.com/apikey → create the key in a **NEW** project. Paste as text.

The plumbing is complete and correct. Which brain does which job lives in `config/agents.yaml`:

```yaml
llm:
  mission.plan:      gemini    # free text into a plan is the model-shaped task
  brand.identity:    gemini    # creative, cheap, visible variety
  site.generate:     offline   # the page is the artifact; the template never surprises us
  compliance.review: gemini    # with the rule floor beneath it
```

`LLM_MODE=offline` makes everything deterministic for rehearsals; `LLM_MODE=gemini` forces it.

### The review floor

`review_site` has a deterministic floor and it exists for a reason. The demo's review loop depends
on a real finding — the first draft genuinely has no allergen notice, and rule checks catch it. A
model alone turned that into a coin flip.

Rule checks run on **every** review and their findings are not negotiable. The model adds what a
rule cannot express; it can never remove a rule finding. Ten tests enforce this. **If a test in
`tests/test_review.py` fails, the change is wrong, not the test.**

### Degradation is already proven

A full mission with a configured-but-failing key produced four `llm.fallback` events, reported
Gemini as `DEGRADED` with the real reason, fell back to templates, and delivered with all five
scenarios green. Nothing pretended to work. A provider outage on Sunday costs polish, not the demo.

---

## 12. The frontend situation

There are **two** frontends.

| | `dashboard/` | `src/` |
|---|---|---|
| Stack | plain HTML/CSS/JS, no build | Next.js 16, React 19, TypeScript, Tailwind |
| Backend | **fully wired** — WebSocket, all routes | **none** |
| Looks | functional | considerably better |
| Drives the demo today | yes | no |

**Decision: the Next.js app ships.** `dashboard/` stays as a fallback and as the only thing that
currently exercises every contract end to end, which makes it a useful reference while wiring.
Do not delete it until the Next.js app has run all five scenarios twice.

### Why the shapes do not line up

The frontend was built from the original design brief; the backend grew into `docs/contracts.md`.
Different envelope, `camelCase` vs `snake_case`, 4 event types defined against 32 real ones, and a
single `identityStatus` enum where we emit five checks with four states each.

Connecting them needs `HttpControlApi` plus a mapping layer. **Real captured backend output is in
`docs/work/fixtures/`** — 95 events, one example of each of the 32 types, every REST response.

---

## 13. What the demo shows

```
1.  one sentence typed in
2.  the Commander plans it and finds it has nobody for the first job
3.  it searches ANS, finds candidates, runs the Trust Gate
4.  an impostor bids and is refused — it cannot prove it holds the key
5.  real agents are admitted, each with narrow scoped permission
6.  one is revoked mid-job; its work is discarded and a replacement recruited
7.  one ships a new version unannounced; the mission pauses for a human
8.  one tries to send data off-site; the Guardian refuses before anything leaves
9.  compliance sends the work back; it is fixed and re-reviewed
10. a finished, signed, provenanced website
```

Step 8 is the strongest beat and it is often undersold: that agent is **fully verified**. Valid
ANS identity, passed all five checks — and still blocked, because identity is not authority.

The sponsor asks for three things explicitly: *show the success path and the refusal path, show
the evidence not just the demo, and name your threat model and the tradeoff you chose.* The third
is not yet written down.

---

## 14. House rules — not negotiable

1. **Never show a green check you did not earn.** `unverified` is not `pass`. "Configured" is not
   "connected."
2. **Fail closed, and say which kind of no it was.** Evidence says no, versus we could not get the
   evidence. They must never render the same.
3. **No secret reaches the browser** or an event payload. Ever.
4. **No fake progress.** UI state comes from events, never a timer.
5. **Deterministic code decides.** A model may explain; it never decides.
6. **No invented numbers.** No trust percentages, no match scores. Every number on screen comes
   from a field in the contract. GoDaddy's own `trustScore` is kept under `provider_scores`,
   clearly attributed to them, never rendered as our judgement.
7. **Never weaken a check or an assertion to make something pass.** A failing test is information.
8. **The result HTML is untrusted model output.** Render it in `<iframe sandbox="">`.

---

## 15. Repo layout

```
mc/                    shared library
  trustgate.py         the five checks
  guardian.py          grants, scopes, policy engine
  standing.py          status tokens
  planning.py          plan validation
  crypto.py merkle.py  certificates, signatures, Merkle proofs
  identity.py          keys, CSRs, registration
  skills.py            what each agent produces — prompts and offline templates
  llm.py               provider layer and per-capability routing
  discovery.py         (to be created — two-stage live search)
  ans/                 sim_client.py, godaddy_client.py, base.py
services/
  hub/app.py           REST + WebSocket + Guardian gateway
  agents/commander.py  planning, hiring, execution graph
  ans_sim/             the local ANS
config/agents.yaml     the cast, policy, job scopes, llm routing
dashboard/             the wired plain-HTML frontend (fallback)
src/                   the Next.js frontend (ships)
scripts/
  run_all.py           start everything
  smoke_test.py        all five scenarios, asserted
  check_ans.py         read-only live ANS preflight
  register_agents.py   registration
docs/
  GUIDE.md             this file — start here
  onboarding.md        shorter context primer
  status.md            detailed done / not done / risks
  contracts.md         every route, event and object shape
  work/                the eight work packets + fixtures
tests/                 135 tests
```

---

## 16. Running it

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python scripts/run_all.py --fresh
```

Dashboard at http://127.0.0.1:8000. No API keys needed — it runs fully offline.

```bash
pytest -q                            # 135 tests, under a second
python scripts/smoke_test.py         # all five scenarios, asserted
npm run typecheck && npm run build   # the frontend
python scripts/check_ans.py          # live ANS preflight, read-only
```

Useful: `DEMO_PACE=0` removes the deliberate pauses, `2.0` slows it for an audience.
`POST /api/reset` or the Reset button for a clean slate.

### The trap that has cost us three debugging sessions

**Only one instance can hold the ports.** If a previous `run_all.py` is still alive, the new one
fails to bind and you end up testing stale services with accumulated state — versions crept up,
agents already revoked, scenarios that cannot fire. If results look strange, check this first.

---

## 17. The eight work packets

Full briefs in [`work/`](work/), drawn so no two touch the same file.
Ready-to-paste AI prompts in [`work/CODEX_PROMPT.md`](work/CODEX_PROMPT.md).

| | Packet | Blocked? | Priority |
|---|---|---|---|
| P1 | Frontend data adapter | no | **critical** |
| P2 | Trust & Guardian views | no | **critical** |
| P3 | Backend API for the frontend | no | high |
| P7 | Live discovery over 216k agents | no | **high — prize track** |
| P8 | Make the agents think | needs a key | **high** |
| P6 | Demo script + rehearsal | no | high by Saturday |
| P4 | Live ANS registration + deployment | needs a key | medium |
| P5 | Agent output quality | no | medium |

Three or four at a time, not eight. If you can only staff four: **P1, P2, P7, P8**.

### Two errands that unblock people

**A Gemini key** — aistudio.google.com/apikey, new project, pasted as text.
**A GoDaddy API key** — the GoDaddy table, Saturday 1:00–3:30, or `#godaddy` on the VTHacks
Discord. Ask plainly: *"can we have the hackathon ANS API key, and should we use production or
OTE?"* Also worth asking: *"is there a reachable sandbox agent we're authorized to invoke?"*

---

## 18. Known traps and past bugs

- **Ports.** See §16. Three sessions lost to this.
- **RSA vs ECDSA.** `cert_issued_by` hardcoded ECDSA because the simulator issues ECDSA. GoDaddy's
  ANS certificates are RSA, so every genuine GoDaddy agent was reported as a signature failure —
  indistinguishable from a forgery. Fixed; 13 tests pin it to real captured trust cards.
- **`unverified` collapsing into `fail`.** Guard this in every mapping and every view.
- **The review floor.** A model must never be able to remove a rule finding.
- **Capability vocabulary.** Ours matches nothing in the real registry. See §10.
- **Disk.** The dev machine has run out of space twice. `.git` is 54 MB and `docs/` is 44 MB,
  mostly committed screenshots.

---

## 19. Honest framing for judges

**"Is this real ANS or a simulation?"** — Say exactly which. By default the registry is ours,
running locally, with real cryptography. Against production GoDaddy we discover 216,000 agents and
verify strangers' identity certificates for real. Never fudge this; it is the one answer that can
cost the track.

**"Couldn't an agent just not ask the Guardian?"** — Network actions go out *through* the Guardian,
so for those there is no other path. For in-process actions the agents cooperate — and here is how
you would enforce it in production.

**"You wrote the agent that misbehaves."** — Yes, deliberately. A demo where permission is only
ever granted teaches nobody anything. The refusal is not staged: the Guardian has no special
knowledge of that request and the same rules would deny any agent.

**"You registered all five agents yourself."** — Two answers. The impostor holds BrandStudio's
exact public identity and still fails, because it cannot produce a signature — that is a real
discriminator, not self-verification. And we verify complete strangers: `supplier.webmesh.ai`,
run by Webmesh, against GoDaddy's production CA.

**"What does this actually prove?"** — That an agent is who it says it is, that it is in good
standing right now, and that it acted inside permissions we granted. **Not** that its output is
correct. Keeping those three claims separate is what makes it rigorous instead of overclaiming.

---

## 20. Where else to look

| Document | For |
|---|---|
| [`status.md`](status.md) | detailed done / not done / risks / decisions |
| [`contracts.md`](contracts.md) | every REST route, WebSocket event and object shape |
| [`work/README.md`](work/README.md) | the eight packets and file ownership |
| [`work/CODEX_PROMPT.md`](work/CODEX_PROMPT.md) | ready-to-paste prompts for an AI session |
| [`work/fixtures/`](work/fixtures/) | real captured backend output and Webmesh trust cards |
| [`godaddy-questions.md`](godaddy-questions.md) | what to ask at the sponsor table |
| [`onboarding.md`](onboarding.md) | a shorter primer if this file is too long |
