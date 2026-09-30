# CortexAi — everything left to do

Eight packets, drawn so **no two touch the same file**. Each is a self-contained brief you can
hand to a separate person or AI session.

**Updated after:** the frontend merged and was chosen, the rename to CortexAi, live GoDaddy
discovery proving out, and two dead Gemini keys.

> New to the project? Read [`../GUIDE.md`](../GUIDE.md) first — it is the complete picture.

## How to use one

**[`CODEX_PROMPT.md`](CODEX_PROMPT.md) has ready-to-paste prompts** for a coding agent — they tell
it what to read, in what order, what not to touch, how to verify, and the traps that are not
discoverable from the code.

Or by hand:

1. Fresh session.
2. Paste [`../onboarding.md`](../onboarding.md) — the project context primer.
3. Paste the packet file.
4. Work on that packet's branch, only in that packet's files.

---

## Where the project actually stands

| | Status |
|---|---|
| **Backend** | Complete. 135 unit tests, all five demo scenarios green end to end. |
| **Frontend** | Next.js, 6 routes, builds clean — and makes **zero network calls**. |
| **Live ANS discovery** | **Working today**, no credential, against 216,110 real agents. |
| **Live ANS identity** | Blocked on a PAT. Resolution and certificates 302 to a login. |
| **LLM** | **Nothing is using one.** Two keys tried, both dead. Running on templates. |
| **Domain** | `getcortex.vip` (Porkbun), wired as an `ANS_DOMAIN` switch, both modes tested. |
| **Name** | CortexAi. Settled, renamed across both halves. |

The two things that decide the demo: **the frontend is not connected to the backend**, and
**no agent is thinking**. Everything else is polish or reach.

---

## The eight packets

| | Packet | Blocked? | Owns | Priority |
|---|---|---|---|---|
| **P1** | [Frontend data adapter](p1-frontend-adapter.md) | no | `src/lib/api/`, provider, env config | **critical** |
| **P2** | [Trust & Guardian views](p2-frontend-views.md) | no | new components + routes | **critical** |
| **P3** | [Backend API for the frontend](p3-backend-api.md) | no | `services/hub/app.py`, contracts | high |
| **P7** | [Live discovery over 216k agents](p7-live-discovery.md) | no | `mc/discovery.py` (new) | **high — prize track** |
| **P8** | [Make the agents think](p8-llm.md) | needs a key | `mc/llm.py` | **high** |
| **P6** | [Demo script + rehearsal](p6-demo.md) | no | demo script, smoke test | high by Saturday |
| **P4** | [Live ANS registration + deployment](p4-live-ans.md) | needs a PAT | `mc/ans/`, scripts, `deployment/` | medium |
| **P5** | [Agent output quality](p5-agent-output.md) | no | `mc/skills.py` | medium |

**Run three or four at a time, not eight.** More than that and the weekend goes on reviewing
merges. If you can only staff four: **P1, P2, P7, P8**.

### Why that four

P1 + P2 connect the two halves of the project — without them you demo a beautiful mock or an ugly
real thing, not both. P7 is the prize track and needs nothing from anyone. P8 is the difference
between a system that plans and one that replays a template.

P4 is the one most likely to be cut. Read its hybrid section: registering **one** agent for real
buys most of the credit for a fraction of the risk.

---

## File ownership

```
P1   src/lib/api/HttpControlApi.ts   src/lib/api/backend-map.ts (new)
     src/lib/env/config.ts           src/components/provider.tsx

P2   src/components/trust/**         src/components/guardian/**       (all new)
     src/components/recruitment/**   src/app/(workspace)/guardian/**
     src/app/(workspace)/events/**

P3   services/hub/app.py             docs/contracts.md
     services/agents/commander.py    -- Mission state and run() only

P7   mc/discovery.py (new)           tests/test_discovery.py (new)
     services/agents/commander.py    -- discover() only

P8   mc/llm.py                       .env.example

P4   mc/ans/**                       scripts/check_ans.py
     scripts/register_agents.py      deployment/**

P5   mc/skills.py

P6   docs/demo-script.md             scripts/smoke_test.py
```

### Three coordinated files — declare before editing

- **`services/agents/commander.py`** — P3 owns `Mission` and `run()`; P7 owns `discover()`. Far
  apart in the file, but say so in chat and merge often.
- **`config/agents.yaml`** — P4 owns `agents:` domains, P5 owns `job_scopes:`, P8 owns `llm:`,
  Zay owns `policy:`.
- **`docs/contracts.md`** — P3 owns it. Anyone changing an event or API shape tells them.

**Reading another packet's file is fine. Editing it is what causes the conflict.**

---

## Fixtures — why P1 and P2 do not block each other

[`fixtures/`](fixtures/) holds **real captured output** from a complete demo run, not invented
shapes. P2 builds the views against these today; when P1 lands the adapter, the same shapes
arrive live and the views keep working.

| File | What |
|---|---|
| `events.json` | 95 events from one mission, in order |
| `events-by-type.json` | one example of each of the **32** event types |
| `mission.json` | plan, jobs, hires, recruitments, roster, stats |
| `state.json` | every agent: ANS name, version, status, fingerprint |
| `guardian-incidents.json` | allowed / denied / needs-review, with full decisions |
| `guardian-grants.json` | live scoped grants with expiries |
| `integrations.json`, `transparency-log.json` | health and ANS log |

---

## Rules for every packet

1. **Branch per packet.** `git checkout -b p1-adapter`. Merge to `main` twice a day minimum.
2. **Run the checks before pushing.** `pytest` (135, under a second) · `npm run typecheck && npm run build` · anything structural: `python scripts/smoke_test.py`.
3. **Stay in your files.** Need something elsewhere? Ask; don't reach in.
4. **The house rules in [`../onboarding.md`](../onboarding.md) §10 are not negotiable.** Never show
   a green check you did not earn. `unverified` is not `pass`. No secret reaches the browser. No
   fake progress. Deterministic code decides; the model only explains.
5. **Only one process can hold the ports.** `run_all.py` fails to bind if another instance is up,
   and you will then be testing stale services with accumulated state. This has already cost two
   confusing debugging sessions. If results look strange, check this first.

---

## The two errands that unblock people

Neither is a coding task. Both are worth doing before anything else.

**A working Gemini key.** aistudio.google.com → **create it in a NEW project**. Existing projects
produce zero-quota keys. Paste it as text, never a photo. Unblocks P8.

**A GoDaddy PAT.** Take [`../godaddy-questions.md`](../godaddy-questions.md) to the sponsor table
and ask question 1 first. Unblocks the second half of P4. Also worth asking: *"is there a
reachable sandbox agent we are authorized to invoke?"* — a yes changes what P7 can demonstrate.

---

## What is already done — don't rebuild it

Five-stage Trust Gate with per-check evidence and four states (`pass`/`fail`/`unverified`/
`not_run`). Standing separated from inclusion, verified offline from tokens agents present.
Deliverables bound to their job and mission. Mission-scoped expiring grants. A deterministic
policy engine with hard denies, scope matching and single-use human review. Network actions
performed by the Guardian, not the agent. Plans as validated dependency graphs with a bounded
retry. Roster-based recruitment with replacement. Eight ANS lifecycle states told apart. Live
registry mapping pinned to a verbatim captured response.

Full detail in [`../status.md`](../status.md) §4.
