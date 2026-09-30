> **Current integration checkpoint - September 19, 2026:** The approved frontend now supports demo/live adapters, same-origin hub transport, strict confirmed start_mission voice handoff, and truthful ANS/Guardian state. Typecheck, lint and build pass; 20 JS unit tests, 124 Python tests, 17 demo browser tests and the separate real-hub browser test pass. Gemini was not contacted (key absent); ElevenLabs token issuance returned HTTP 401. Older disconnected/draft-only descriptions below are historical. See [live integration handoff](LIVE_INTEGRATION.md) for run commands, screenshots and limits. No redesign or deployment.

# CortexAi — context primer

> **Start with [`GUIDE.md`](GUIDE.md)** — the single source of truth, kept current. If this file
> disagrees with it, the guide is right and this one is stale.
**Read this first. Then paste the whole file into your AI assistant before asking it for help
with this project.** It is written to be self-contained: an assistant that has read it can
reason about the codebase without seeing the code.

Then read your own brief in [`docs/team/`](team/), and the status doc in
[`docs/status.md`](status.md) for what is currently done and open.

---

## 1. The product in one paragraph

A person types one goal in plain English — *"Launch an online presence for Hokie Bites, a food
truck in Blacksburg."* A **Commander** agent works out what that involves, discovers specialist
agents belonging to *other organisations*, proves who they are through **GoDaddy's Agent Name
Service (ANS)**, gives each one narrowly scoped permission for that mission only, watches what
they do, blocks what they are not allowed to do, verifies the signed work they return, and
produces a real finished website at the end.

Built for VTHacks 14, GoDaddy **"Best Use of ANS"** track. Demo is Sunday.

## 2. The one idea everything serves

> **Verified identity is not unlimited authority.**

Knowing exactly who an agent is tells you nothing about what it should be allowed to do. Those
are two different questions, and the product answers them separately and visibly:

- **ANS** answers *who is this?* — identity, discovery, lifecycle, revocation.
- **We** answer *may it do this, here, now?* — scopes, grants, policy.

Anything that blurs those two is a bug, even when it makes a demo smoother.

## 3. Vocabulary

| Term | Means |
|---|---|
| **ANS** | Agent Name Service. GoDaddy's registry: agents register a name, prove they control the domain, and get a certificate. Like DNS for AI agents. |
| **ANS name** | `ans://v1.0.0.sitebuilder.sitesmith.xyz` — version, label, domain. The version is part of the identity: v1.0.0 and v1.1.0 are different agents. |
| **Agent card** | A JSON document at the agent's URL saying who it is and what it does. |
| **Trust Gate** | Our five checks before trusting another agent. |
| **Guardian** | Our policy layer. Decides whether a specific action is allowed. |
| **Grant** | Permission for one agent, on one mission, with named scopes, that expires. |
| **Scope** | `http.fetch:fonts.googleapis.com` — an action paired with where or what. |
| **Capability** | A kind of work: `brand.identity`, `site.generate`, `compliance.review`. |
| **Transparency log** | Append-only Merkle log of registrations. Proves a registration happened. |
| **Status token** | Short-lived signed proof an agent is in good standing *right now*. |
| **Mission / job / task** | Mission = the whole goal. Task = one step in the plan. Job = one task handed to one agent. |

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

## 5. Architecture

```
dashboard (browser) ──WebSocket──▶ hub :8000 ◀── events from every agent
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
- **ANS** — identity only. Real cryptography either way: with the simulator it runs on your
  laptop, with `ANS_BACKEND=godaddy` it is GoDaddy's.

## 6. The five Trust Gate checks

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

### The distinction that matters most in check 3

A **transparency-log receipt** proves a registration happened. It stays valid forever, including
ten minutes after the agent is revoked. A **status token** is short-lived and signed and says the
agent is in good standing *now*. Only the second can catch a revocation. Conflating them was a
real bug we fixed; don't reintroduce it.

Agents attach their own status token (`X-ANS-Status-Token`) to every response, so we verify it
**offline** against root keys we already hold — no registry round-trip. Presented tokens are
pinned to their subject, because a valid ACTIVE token might be about somebody else.

## 7. The Guardian

Passing the Trust Gate gets an agent through the door. It says nothing about what it may touch.

When the Commander hires, it asks the Guardian to record a **grant** — *this agent, this mission,
these scopes, expiring* — over a signed request. Then every sensitive thing the agent wants to do
comes back and asks.

```
1. Hard denies      secrets.read, key.export, ans.revoke, guardian.disable
                    Refused before grants or scopes are even looked at.
                    NO HUMAN CAN APPROVE THESE.
2. Grant            no grant, wrong mission, wrong agent, or expired → denied
3. Scope            http.fetch:fonts.googleapis.com is a different permission
                    from external.upload:anywhere.example.net
4. Human review     outward-facing actions (publish, email) stop and ask a person,
                    bound to an exact payload digest, usable exactly once
```

Network actions go out **through** the Guardian, against its own allowlist, so a denied upload is
denied in the only sense that counts: nothing left the building. Every decision is plain
deterministic code — the model explains, it never decides.

## 8. Missions are dependency graphs

The planner asks Gemini for a plan, then **validates it server-side before anything runs**: no
cycles, no dangling or self-references, no duplicate ids, no capability we cannot source, one to
six tasks. Valid JSON matching a schema can still describe a task depending on itself, so the
schema is not the check.

A plan that fails validation goes back to the model **once**, with the specific complaints
attached. Fail twice and we plan it ourselves.

The Commander walks the graph, handing each task the output of what it declared it needed **and
nothing else**.

## 9. What the demo shows

The mission runs `brand kit → landing page → compliance review`, with a fix-and-recheck loop.
Five things go wrong on purpose:

| Scenario | What happens |
|---|---|
| **Impostor** | Copies BrandStudio's real public identity and bids. Passes `resolve`, fails `authenticate` — it cannot sign with a key it does not have — and fails `policy` for answering from the wrong address. Blocked. |
| **Revocation** | WebForge is revoked in ANS mid-build. Its deliverable is discarded, it comes off the roster, SiteSmith is recruited as a replacement. |
| **Version change** | LegalCheck ships v1.1.0 unannounced. The version is part of the ANS name, so the Commander notices; policy only approves 1.0.x, so the mission pauses for a human. |
| **Scope violation** | The hired site builder tries to send the brand kit to its own analytics host. It holds no scope for that. Guardian refuses before anything leaves. |
| **Human review** | It asks to publish the page. That stops and asks a person, bound to that exact payload, usable once. |

## 10. House rules

These are not style preferences. Breaking one undermines the product's whole claim.

1. **Never show a green check you did not earn.** `unverified` is not `pass`. "Configured" is not
   "connected". An API key in a file proves someone pasted a string.
2. **Fail closed, and say which kind of no it was.** "The evidence says no" and "we could not get
   the evidence" are different sentences.
3. **Verified, authorized, running and finished are four different things.** "Identity verified,
   permission denied" is a legitimate state that must be renderable.
4. **No secret ever reaches the browser.** No keys, no tokens, no PATs, in any payload or event.
5. **No fake progress.** A node appears because an event arrived, never because of a timer.
6. **Demo mode and live mode are both honest.** Never silently fall back from a failed live call
   to a fake success. If ANS is unreachable, say "ANS unavailable".
7. **The model explains; deterministic code decides.** No LLM output reaches a policy decision.
8. **Generated HTML is untrusted.** It is model output. It renders in `<iframe sandbox="">`.

## 11. Layout

```
config/agents.yaml       who's who: orgs, domains, ports, policy, scopes
mc/                      shared library
  trustgate.py           the five checks, plus signed-deliverable verification
  guardian.py            grants, scopes, the policy every action is judged against
  standing.py            status tokens: good standing now, vs. registered once
  planning.py            plan validation: cycles, dependencies, capabilities
  identity.py            keys, CSRs, registration (keys live in keys/, gitignored)
  ans/                   ANS client: sim_client.py, godaddy_client.py
  crypto.py, merkle.py   certificates, signatures, transparency-log proofs
  skills.py              what each agent does: Gemini prompts and offline templates
  agent_base.py          the web service every agent runs (/card, /challenge, /job)
services/
  ans_sim/               local ANS: registration authority, CA, transparency log, DNS
  hub/                   event stream, Guardian gateway, demo controls, dashboard
  agents/                commander.py, vendor.py, impostor.py
dashboard/               plain HTML/CSS/JS, no build step
scripts/                 run_all.py, smoke_test.py, check_ans.py, register_agents.py
tests/                   90 unit tests, none needing a server
docs/                    status.md, contracts.md, godaddy-questions.md, team/
```

## 12. Running it

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # macOS/Linux: cp .env.example .env
python scripts/run_all.py --fresh
```

Dashboard at **http://127.0.0.1:8000**. Press **Launch mission**.

```bash
pytest                            # 90 tests, no server needed, under a second
python scripts/smoke_test.py      # the whole demo asserted (system must be running)
```

**No API keys are needed for any of this.** Agents fall back to offline templates without a
Gemini key, and ANS runs locally by default.

**Uvicorn does not auto-reload.** After changing Python, Ctrl+C `run_all.py` and start it again.

## 13. Working agreements

- **Branch off `main`, small commits, push often.** Four people, one weekend.
- **`config/agents.yaml` is the one shared file.** Say so in chat before editing it.
- **Do not weaken a check to make a demo work.** Raise it instead — there is almost always an
  honest version that demos better.
- **If you add or change an event or an API shape, update [`docs/contracts.md`](contracts.md) in
  the same commit.** The frontend is built from that file, not from the Python.
- **Run `pytest` before you push.** It takes under a second.
- **Never commit `.env`, `keys/`, or anything in `data/`.** Already gitignored — keep it that way.

## 14. Honest framing (say this to judges)

The five companies are fictional and all ours. Every registration, certificate, signature,
revocation and log proof is real cryptography. With the simulator the ANS side runs locally;
with `ANS_BACKEND=godaddy` it is GoDaddy's.

The agent that misbehaves is ours too — we wrote it, because a demo where permission is only ever
granted teaches nobody anything. What is *not* staged is the refusal: the Guardian is given no
special knowledge of that request, and the same rules deny it that would deny any other agent.

And note what we do **not** claim: the Guardian decides whether an action is permitted, not
whether an agent's output is *correct*. ANS proves who an agent is, not that it will behave.
Which is exactly why there is a Guardian at all.
