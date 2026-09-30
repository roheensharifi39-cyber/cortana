"""The Commander: plans a mission, finds agents, verifies them through ANS, hires them,
checks every signed deliverable, and assembles the result.

The plan is a small dependency graph, not a fixed script — one task or six, in whatever order
the dependencies allow. Running it is a walk over that graph, handing each task the output of
the things it declared it needed.

Demo scenarios (flags sent with a mission) fire off the kind of work being done rather than a
position in a script, so they still land in the right place when the plan changes:
  impostor  — before the first hire, an impostor offers to do the job
  revoke    — while the first site build runs, that vendor is revoked in ANS
  upgrade   — before the first compliance review, LegalCheck ships a new version
  exfil     — the site builder tries to send the brand kit off-site
  publish   — the site builder asks to put the page on the public web
"""
import asyncio
import datetime as dt
import traceback
import uuid
from dataclasses import dataclass, field

import httpx
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

from mc import config
from mc.agent_base import create_agent_app
from mc.events import emit
from mc.guardian import job_scopes
from mc.http import client
from mc.llm import generate_json
from mc.planning import Task as PlanTask
from mc.skills import plan_mission
from mc.trustgate import NEEDS_APPROVAL, TRUSTED, Policy, verify_result

app, state = create_agent_app("commander")

MISSIONS: dict[str, "Mission"] = {}
CURRENT: dict[str, str | None] = {"id": None}
SUBMISSIONS: dict[str, str] = {}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def me() -> str:
    return state.identity.ans_name if (state and state.identity) else "ans://v1.0.0.commander.launchpad.xyz"


class MissionFailed(Exception):
    pass


@dataclass
class Recruitment:
    """One round of "we need someone for this, who is out there?".

    Created the moment the Commander finds it has nobody for a capability, and kept for the
    life of the mission — including the candidates that were turned away, because who we
    refused is as much a part of the record as who we hired.
    """
    mission_id: str
    job_title: str
    capability: str
    requested_scopes: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: "rec_" + uuid.uuid4().hex[:8])
    status: str = "discovering"  # discovering | evaluating | awaiting_approval | admitted | rejected | failed
    candidates: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    selected: str | None = None
    replacing: dict | None = None  # set when we are backfilling an agent we lost mid-mission
    granted_scopes: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: now())
    finished_at: str | None = None

    def public(self) -> dict:
        return {
            "id": self.id, "mission_id": self.mission_id, "job_title": self.job_title,
            "capability": self.capability, "status": self.status, "candidates": self.candidates,
            "selected": self.selected, "replacing": self.replacing, "rejected": self.rejected,
            "requested_scopes": self.requested_scopes, "granted_scopes": self.granted_scopes,
            "started_at": self.started_at, "finished_at": self.finished_at,
        }


def roster_match(roster: dict, capability: str, excluded: set) -> dict | None:
    """Is anyone already working on this mission able to do this job?

    Deterministic and boring on purpose: first hired wins, and anyone we have dropped is
    not a candidate no matter what their card says.
    """
    for entry in roster.values():
        if capability in entry["capabilities"] and (entry["ans_name"], entry["endpoint"]) not in excluded:
            return entry
    return None


class Mission:
    def __init__(self, text: str, scenario: dict):
        self.id = "m_" + uuid.uuid4().hex[:8]
        self.text = text
        self.scenario = scenario
        self.status = "planning"
        self.business: dict = {}
        self.jobs: list[dict] = []
        self.hires: list[dict] = []
        self.recruitments: list[Recruitment] = []
        self.plan = None
        # Who is already working on this mission, by ANS name. Being on it gets you the next
        # job of the same kind; it does not carry any authority — that is granted per job.
        self.roster: dict[str, dict] = {}
        # Agents we had and lost, by capability — so backfilling one reads as a replacement
        # rather than as never having had anyone.
        self.lost: dict[str, dict] = {}
        self.excluded: set[tuple[str, str]] = set()
        self.policy = Policy.from_config(config.policy())
        self.approval: dict | None = None
        self.approval_event = asyncio.Event()
        self.decision: str | None = None
        self.fired: set[str] = set()
        self.result_html: str | None = None
        self.review: dict | None = None
        self.stats = {"checks_passed": 0, "blocked": 0, "signed": 0}
        self.engines: set[str] = set()
        self.error: str | None = None
        self.started_at = now()
        self.finished_at: str | None = None
        self.task: asyncio.Task | None = None

    def public(self) -> dict:
        return {
            "id": self.id, "text": self.text, "scenario": self.scenario, "status": self.status,
            "business": self.business, "jobs": self.jobs, "hires": self.hires, "approval": self.approval,
            "recruitments": [r.public() for r in self.recruitments],
            "plan": self.plan.as_dict() if self.plan else None,
            "roster": [{"ans_name": e["ans_name"], "org": e["org"], "version": e["version"],
                        "capabilities": e["capabilities"]} for e in self.roster.values()],
            "stats": self.stats, "review": self.review, "error": self.error, "has_result": bool(self.result_html),
            "engines": sorted(e for e in self.engines if e), "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


async def say(m: Mission, type: str, message: str, **data):
    await emit(type, message, actor=me(), mission_id=m.id, **data)


async def hub_post(path: str):
    try:
        r = await client().post(f"{config.hub_url()}{path}", timeout=30)
        return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


async def _delayed_hub_post(path: str, delay: float):
    await asyncio.sleep(delay)
    await hub_post(path)


# --- finding and hiring -----------------------------------------------------------------

async def discover(m: Mission, rec: Recruitment) -> list[dict]:
    """Candidates come from two places: open offers on the web, and the ANS registry.

    Anyone can post an offer — that is the point of having them, and it is how the impostor
    gets in front of us. Nothing here is trusted; this only decides who we bother to check.
    """
    await say(m, "discovery.started", f"Searching for an agent that can {rec.capability}",
              capability=rec.capability, recruitment_id=rec.id,
              sources=["ANS registry", "open-web offers"])

    cands: list[dict] = []
    try:
        offers = (await client().get(f"{config.hub_url()}/api/offers",
                                     params={"capability": rec.capability}, timeout=5)).json()
    except (httpx.HTTPError, ValueError):
        offers = []
    for o in offers:
        cands.append({"ans_name": o["ans_name"], "endpoint": o["endpoint"], "source": "open-web offer",
                      "org": o.get("org"), "pitch": o.get("pitch"), "capabilities": [rec.capability]})

    rank: dict[str, int] = {}
    for i, name in enumerate(m.policy.preferred_vendors):
        try:
            rank[config.host_of(config.agent(name))] = i
        except KeyError:
            rank[name] = i  # a raw host or domain is fine too

    def preference(record: dict) -> int:
        parsed = config.parse_ans_name(record["ans_name"])
        return rank.get(parsed["host"], rank.get(parsed["domain"], 99))

    records = await state.ans.search(capability=rec.capability)
    records.sort(key=preference)
    for r in records:
        cands.append({"ans_name": r["ans_name"], "endpoint": r["endpoint"], "source": "ANS registry",
                      "org": r.get("org"), "version": r.get("version"),
                      "capabilities": r.get("capabilities", []), "pitch": None})

    seen, out = set(), []
    for c in cands:
        key = (c["ans_name"], c["endpoint"])
        if key not in seen and key not in m.excluded:
            seen.add(key)
            out.append(c)

    for c in out:
        rec.candidates.append(dict(c))
        await say(m, "discovery.candidate_found",
                  f"{c.get('org') or c['ans_name']} offers {rec.capability}"
                  + (f" — \"{c['pitch']}\"" if c.get("pitch") else ""),
                  subject=c["ans_name"], recruitment_id=rec.id, capability=rec.capability, **c)
        await asyncio.sleep(config.pace() / 4)

    await say(m, "discovery.completed", f"Found {len(out)} candidate(s) for {rec.capability}",
              capability=rec.capability, recruitment_id=rec.id, count=len(out))
    return out


async def reverify(entry: dict) -> tuple[bool, str]:
    """A quick re-check before handing someone we already hired another job.

    Only two things can have changed since we admitted them: their standing in ANS, and their
    version. So those are the two we look at. A new version is a different agent — different
    card, different capabilities, different policy standing — so it goes back through the full
    gate instead of inheriting the old admission.
    """
    record = await state.ans.resolve(entry["ans_name"])
    if not record:
        return False, "no longer registered in ANS"
    if record.get("version") != entry["version"]:
        return False, f"now running v{record.get('version')} (we verified v{entry['version']})"
    check = await state.gate.recheck_status(entry["ans_name"])
    if not check.ok:
        return False, check.detail
    # Keep the certificate fresh: it is what we check their signed deliverables against.
    entry["trust"].record = record
    entry["trust"].cert_pem = record.get("identity_cert_pem") or entry["trust"].cert_pem
    return True, check.detail


async def ask_approval(m: Mission, job: dict, cand: dict, res) -> bool:
    v = config.parse_ans_name(cand["ans_name"])
    pin = m.policy.version_pins.get(v["host"], "?")
    m.status = "paused"
    m.decision = None
    m.approval_event.clear()
    m.approval = {"job": job["title"], "ans_name": cand["ans_name"], "org": res.record["org"],
                  "version": v["version"], "approved_version": f"{pin}.x", "reason": res.reason}
    await say(m, "approval.required",
              f"{res.record['org']} is now running v{v['version']}; our policy approves {pin}.x. Continue with the new version?",
              subject=cand["ans_name"], **m.approval)
    await m.approval_event.wait()
    approved = m.decision == "approve"
    m.approval = None
    m.status = "working"
    await say(m, "approval.decided",
              f"Owner {'approved' if approved else 'rejected'} {res.record['org']} v{v['version']}",
              subject=cand["ans_name"], decision=m.decision)
    if approved:
        m.policy.version_pins[v["host"]] = f"{v['major']}.{v['minor']}"
    return approved


async def hire(m: Mission, job: dict) -> tuple[dict, object]:
    """Someone we already have, or someone we go and find."""
    capability = job["capability"]

    known = roster_match(m.roster, capability, m.excluded)
    if known:
        ok, detail = await reverify(known)
        if ok:
            m.stats["checks_passed"] += 1
            await say(m, "capability.covered",
                      f"{known['org']} is already on this mission and still in good standing",
                      subject=known["ans_name"], capability=capability, job=job["title"],
                      ans_name=known["ans_name"], org=known["org"], detail=detail)
            return {"ans_name": known["ans_name"], "endpoint": known["endpoint"],
                    "source": "mission roster", "org": known["org"]}, known["trust"]
        # They were fine when we hired them and they are not fine now. Off the roster.
        m.roster.pop(known["ans_name"], None)
        m.excluded.add((known["ans_name"], known["endpoint"]))
        m.lost[capability] = {"ans_name": known["ans_name"], "org": known["org"], "reason": detail}
        m.stats["blocked"] += 1
        await say(m, "agent.unavailable", f"{known['org']} can no longer take this job: {detail}",
                  subject=known["ans_name"], ans_name=known["ans_name"], org=known["org"], reason=detail)

    return await recruit(m, job)


# --- Gemini Master Orchestrator: candidate selection, refusal, supervision ---------------

SELECT_CANDIDATE_SYSTEM = """You are the Gemini Master Orchestrator for CortexAI.
You are selecting which specialist agent to hire for a specific task in an active mission.

CRITICAL SECURITY RULES:
1. You may ONLY choose an agent from the provided list of verified candidates.
2. You CANNOT invent agent names, choose unverified agents, or choose candidates outside this list.
3. Every candidate in the list has already passed cryptographic Trust Gate verification.
4. Evaluate candidate capabilities, version, domain reputation, and fit for the task.

Return JSON:
{
  "selected_ans_name": str,
  "rationale": str
}"""


async def gemini_choose_candidate(m: Mission, job: dict, rec: Recruitment,
                                  verified: list[tuple[dict, object]]) -> tuple[dict, object, str, str]:
    """Gemini evaluates verified candidates and selects who to hire.

    If Gemini is offline, unavailable, or selects an unverified agent, deterministic preference is used.
    """
    def fallback_choice() -> dict:
        best_cand, _ = verified[0]
        return {
            "selected_ans_name": best_cand["ans_name"],
            "rationale": f"Selected {best_cand.get('org', best_cand['ans_name'])} via deterministic vendor preference policy.",
        }

    cands_summary = []
    for cand, res in verified:
        rec_data = getattr(res, "record", {}) or {}
        cands_summary.append({
            "ans_name": cand["ans_name"],
            "org": rec_data.get("org") or cand.get("org"),
            "domain": config.parse_ans_name(cand["ans_name"])["domain"],
            "version": rec_data.get("version") or cand.get("version"),
            "capabilities": rec_data.get("capabilities") or cand.get("capabilities", []),
            "endpoint": cand["endpoint"],
            "source": cand.get("source"),
        })

    prompt = (
        f"Mission: {m.text}\n"
        f"Business: {m.business}\n"
        f"Job: {job['title']} (capability: {job['capability']})\n"
        f"Brief: {job.get('brief', '')}\n"
        f"Requested Scopes: {rec.requested_scopes}\n\n"
        f"Verified Candidates (choose exactly ONE from this list):\n"
        f"{cands_summary}"
    )

    raw, engine = await generate_json(
        SELECT_CANDIDATE_SYSTEM, prompt, fallback_choice,
        label="Candidate selection", capability="orchestrator.select_candidate",
        actor=me(), mission_id=m.id,
    )

    selected_name = raw.get("selected_ans_name")
    rationale = raw.get("rationale") or "Selected by Gemini Master Orchestrator"

    # Strictly verify that selected_name is in the verified list
    match = next(((c, t) for c, t in verified if c["ans_name"] == selected_name), None)
    if match and engine == "gemini":
        return match[0], match[1], rationale, "gemini"

    if selected_name and not match and engine == "gemini":
        await say(m, "candidate.invalid_selection",
                  f"Gemini proposed candidate {selected_name!r} which is NOT in the verified set. Discarding model choice and applying deterministic policy.",
                  invalid_candidate=selected_name, capability=job["capability"])

    fallback_cand, fallback_trust = verified[0]
    fb_rationale = (f"Selected {fallback_cand.get('org', fallback_cand['ans_name'])} via deterministic policy "
                    f"({'invalid model candidate rejected' if (selected_name and not match) else 'offline engine'}).")
    return fallback_cand, fallback_trust, fb_rationale, "deterministic_fallback"


REFUSAL_SYSTEM = """You are the Gemini Master Orchestrator for CortexAI.
An agent you hired has refused the job or failed during execution.
The refusing candidate has been permanently excluded from this role for this mission.
Decide the next orchestrator action:
- "search_again": Query ANS and recruit another verified candidate to backfill the job.
- "fail": Abort the mission if the job is strictly irreplaceable.

Return JSON:
{
  "action": "search_again" | "fail",
  "reasoning": str
}"""


async def gemini_on_refusal(m: Mission, job: dict, cand: dict, refusal_reason: str) -> dict:
    def fallback():
        return {
            "action": "search_again",
            "reasoning": "Defaulting to ANS search for alternate verified candidate following refusal.",
        }

    prompt = (
        f"Mission: {m.text}\n"
        f"Job: {job['title']} (capability: {job['capability']})\n"
        f"Agent: {cand.get('org', cand['ans_name'])} ({cand['ans_name']})\n"
        f"Refusal Reason: {refusal_reason}\n"
        f"Status: Agent has been permanently excluded from this mission."
    )

    decision, engine = await generate_json(
        REFUSAL_SYSTEM, prompt, fallback,
        label="Refusal resolution", capability="orchestrator.refusal",
        actor=me(), mission_id=m.id,
    )
    if decision.get("action") not in ("search_again", "fail"):
        decision["action"] = "search_again"
    return decision


SUPERVISION_SYSTEM = """You are the Gemini Master Orchestrator supervising mission execution in CortexAI.
You review deliverables as tasks complete and determine whether:
1. "accept": Deliverable meets specifications, proceed to subsequent tasks.
2. "needs_security_review": The deliverable involves security-sensitive components or the user requested security/verification, so a Shield security review task is required.
3. "request_revision": The deliverable has deficiencies that require rework.

Return JSON:
{
  "status": "accept" | "needs_security_review" | "request_revision",
  "rationale": str
}"""


async def gemini_supervise_task(m: Mission, task: PlanTask, output: dict, remaining: list[PlanTask]) -> dict:
    def fallback():
        return {"status": "accept", "rationale": "Task output verified and accepted by orchestrator."}

    summary_out = {k: v for k, v in output.items() if k != "html"} if isinstance(output, dict) else str(output)
    prompt = (
        f"Mission: {m.text}\n"
        f"Completed Task: {task.title} (capability: {task.capability})\n"
        f"Task Brief: {task.brief}\n"
        f"Deliverable Summary: {summary_out}\n"
        f"Remaining Tasks: {[t.title for t in remaining]}"
    )
    res, engine = await generate_json(
        SUPERVISION_SYSTEM, prompt, fallback,
        label="Task supervision", capability="orchestrator.supervise",
        actor=me(), mission_id=m.id,
    )
    if res.get("status") not in ("accept", "needs_security_review", "request_revision"):
        res["status"] = "accept"
    return res


async def recruit(m: Mission, job: dict) -> tuple[dict, object]:
    """Nobody we have can do this. Say so out loud, then go looking."""
    capability = job["capability"]
    lost = m.lost.pop(capability, None)
    rec = Recruitment(mission_id=m.id, job_title=job["title"], capability=capability,
                      requested_scopes=job_scopes(capability), replacing=lost)
    m.recruitments.append(rec)
    job["recruitment_id"] = rec.id

    roster = [{"ans_name": e["ans_name"], "org": e["org"], "capabilities": e["capabilities"]}
              for e in m.roster.values()]
    if lost:
        await say(m, "recruitment.replacement_requested",
                  f"{lost['org']} is out and the {job['title'].lower()} still needs doing. Finding a replacement.",
                  subject=lost["ans_name"], capability=capability, job=job["title"], recruitment_id=rec.id,
                  requested_scopes=rec.requested_scopes, replacing=lost, roster=roster)
    else:
        await say(m, "capability.missing",
                  f"Nobody on this mission can {capability}. Looking for an agent that can.",
                  capability=capability, job=job["title"], recruitment_id=rec.id,
                  requested_scopes=rec.requested_scopes, roster=roster)
    await asyncio.sleep(config.pace() / 2)

    candidates = await discover(m, rec)
    if not candidates:
        rec.status, rec.finished_at = "failed", now()
        await say(m, "discovery.failed", f"No agent anywhere offers {capability}",
                  capability=capability, recruitment_id=rec.id)
        raise MissionFailed(f"No verified agent is available for {capability}")

    rec.status = "evaluating"
    verified: list[tuple[dict, object]] = []

    for cand in candidates:
        res = await state.gate.check_agent(cand["ans_name"], cand["endpoint"], capability, m.policy,
                                           mission_id=m.id, source=cand["source"])
        m.stats["checks_passed"] += res.passed

        admitted = res.verdict == TRUSTED
        if not admitted and res.verdict == NEEDS_APPROVAL:
            rec.status = "awaiting_approval"
            admitted = await ask_approval(m, job, cand, res)
            rec.status = "evaluating"

        if admitted:
            verified.append((cand, res))
        else:
            failed = next((c for c in res.checks if c.ok is False), None)
            rec.rejected.append({"ans_name": cand["ans_name"], "org": cand.get("org"),
                                 "failed_check": failed.name if failed else None,
                                 "reason": failed.detail if failed else "rejected"})
            await say(m, "agent.rejected", f"{cand.get('org') or cand['ans_name']} was turned away: {res.reason}",
                      subject=cand["ans_name"], recruitment_id=rec.id, ans_name=cand["ans_name"],
                      org=cand.get("org"), failed_check=failed.name if failed else None, reason=res.reason,
                      source=cand["source"])
            m.stats["blocked"] += 1
            m.excluded.add((cand["ans_name"], cand["endpoint"]))
            await asyncio.sleep(config.pace() / 2)

    if not verified:
        rec.status, rec.finished_at = "rejected", now()
        raise MissionFailed(f"No verified agent is available for {capability}")

    # Gemini Master Orchestrator chooses who to hire among VERIFIED candidates only
    cand, trust, rationale, source = await gemini_choose_candidate(m, job, rec, verified)

    org = trust.record["org"]
    rec.selected, rec.status, rec.finished_at = cand["ans_name"], "admitted", now()
    rec.granted_scopes = list(rec.requested_scopes)
    m.roster[cand["ans_name"]] = {
        "ans_name": cand["ans_name"], "endpoint": cand["endpoint"], "org": org,
        "version": trust.record.get("version"), "capabilities": trust.record.get("capabilities", []),
        "trust": trust, "hired_at": now(), "selection_source": source, "rationale": rationale,
    }
    await say(m, "candidate.selected", f"Master Orchestrator selected {org} ({source}): {rationale}",
              subject=cand["ans_name"], recruitment_id=rec.id, capability=capability,
              ans_name=cand["ans_name"], org=org, rationale=rationale, selection_source=source)
    await say(m, "agent.admitted", f"{org} passed the Trust Gate and joins the mission",
              subject=cand["ans_name"], recruitment_id=rec.id, capability=capability,
              ans_name=cand["ans_name"], org=org, granted_scopes=rec.granted_scopes,
              source=cand["source"], selection_source=source)
    return cand, trust


async def issue_grant(m: Mission, ans_name: str, job_id: str, capability: str) -> list[str]:
    """Hire, then authorize — separately, and narrowly.

    Passing the Trust Gate says who this agent is. It says nothing about what it may touch,
    so the Commander asks the Guardian to record a grant covering this job and no more. The
    request is signed: the Guardian takes orders from the Commander's ANS identity, not from
    anything that can reach its port.
    """
    scopes = job_scopes(capability)
    payload = {"from": me(), "mission_id": m.id, "job_id": job_id, "agent": ans_name,
               "scopes": scopes, "capability": capability}
    try:
        r = await client().post(f"{config.hub_url()}/api/guardian/grants",
                                json={"payload": payload, "signature": state.identity.sign_obj(payload)},
                                timeout=15)
        if r.status_code != 200:
            raise MissionFailed(f"The Guardian refused to authorize {ans_name}: {r.text[:200]}")
    except httpx.HTTPError as exc:
        raise MissionFailed(f"The Guardian is unreachable ({type(exc).__name__}); nobody works unauthorized")
    return scopes


async def run_job(m: Mission, job: dict, job_input: dict, *, revoke_during: str | None = None) -> dict:
    job["status"] = "hiring"
    while True:
        cand, trust = await hire(m, job)
        org = trust.record["org"]
        job.update(status="working", agent=cand["ans_name"], org=org)
        job_id = "job_" + uuid.uuid4().hex[:8]
        scopes = await issue_grant(m, cand["ans_name"], job_id, job["capability"])
        job["scopes"] = scopes
        payload = {"job_id": job_id, "mission_id": m.id, "from": me(),
                   "capability": job["capability"], "input": job_input, "allowed_scopes": scopes}
        signature = state.identity.sign_obj(payload)
        await say(m, "job.sent", f"Hired {org} for {job['title'].lower()}", subject=cand["ans_name"], job=job["title"])

        # Demo scenario: revoke this vendor while it's working. We wait for the revocation to land
        # before checking the deliverable, so the scenario plays out the same way every time.
        revoke_task = None
        if revoke_during and "revoke" not in m.fired and \
                config.parse_ans_name(cand["ans_name"])["domain"] == config.agent(revoke_during)["domain"]:
            m.fired.add("revoke")
            revoke_task = asyncio.create_task(_delayed_hub_post(f"/api/chaos/revoke/{revoke_during}", 0.3))

        async def drop(reason: str):
            """Take an agent off this mission. Says so out loud, so the board can grey them out."""
            m.excluded.add((cand["ans_name"], cand["endpoint"]))
            if m.roster.pop(cand["ans_name"], None):
                m.lost[job["capability"]] = {"ans_name": cand["ans_name"], "org": org, "reason": reason}
            m.stats["blocked"] += 1
            job.update(status="rehiring", agent=None, org=None)
            await say(m, "agent.unavailable", f"{org} is off the {job['title'].lower()}: {reason}",
                      subject=cand["ans_name"], ans_name=cand["ans_name"], org=org,
                      capability=job["capability"], reason=reason)

        try:
            r = await client().post(f"{cand['endpoint']}/job", json={"payload": payload, "signature": signature},
                                    timeout=240)
        except httpx.HTTPError as exc:
            await say(m, "result.rejected", f"{org} didn't respond ({type(exc).__name__}); finding another agent",
                      subject=cand["ans_name"])
            await drop("no response")
            continue
        if r.status_code != 200:
            detail = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
            await say(m, "result.rejected", f"{org} refused the job: {detail}", subject=cand["ans_name"])
            await drop(f"refused: {detail}")
            refusal_decision = await gemini_on_refusal(m, job, cand, str(detail))
            await say(m, "orchestrator.refusal_action",
                      f"Orchestrator resolved refusal: {refusal_decision['action']} — {refusal_decision.get('reasoning', '')}",
                      action=refusal_decision["action"], reasoning=refusal_decision.get("reasoning"),
                      excluded=cand["ans_name"])
            if refusal_decision.get("action") == "fail":
                raise MissionFailed(f"{org} refused the job ({detail}) and orchestrator aborted mission.")
            continue

        result = r.json()
        if revoke_task:
            await revoke_task
        ok, why = verify_result(result, cand["ans_name"], trust.cert_pem,
                                job_id=job_id, mission_id=m.id)
        if ok:
            status = await state.gate.recheck_status(cand["ans_name"])
            if not status.ok:
                ok, why = False, f"{org} is now {status.detail}. Deliverable discarded."
        if not ok:
            await say(m, "result.rejected", why, subject=cand["ans_name"])
            await drop(why)
            decision = await gemini_on_refusal(m, job, cand, why)
            await say(m, "orchestrator.refusal_action",
                      f"Orchestrator handled verification failure for {org}: {decision['action']} — {decision.get('reasoning', '')}",
                      action=decision["action"], reasoning=decision.get("reasoning"), excluded=cand["ans_name"])
            if decision.get("action") == "fail":
                raise MissionFailed(f"{org} deliverable verification failed ({why}) and orchestrator aborted.")
            continue

        out = result["payload"]
        m.stats["signed"] += 1
        m.engines.add(out.get("engine"))
        m.hires.append({
            "job": job["title"], "capability": job["capability"], "ans_name": cand["ans_name"], "org": org,
            "endpoint": cand["endpoint"], "checks": [c.as_dict() for c in trust.checks],
            "owner_approved": trust.verdict == NEEDS_APPROVAL,
            "log_index": trust.log_index, "fingerprint": result.get("cert_fingerprint"),
            "output_sha256": out["output_sha256"], "signed_at": out["signed_at"], "engine": out.get("engine"),
            "selection_source": m.roster.get(cand["ans_name"], {}).get("selection_source", "verified"),
            "rationale": m.roster.get(cand["ans_name"], {}).get("rationale", ""),
        })
        await say(m, "result.verified", f"Verified {org}'s signed {job['title'].lower()}", subject=cand["ans_name"],
                  sha256=out["output_sha256"][:16])
        job["status"] = "done"
        return out["output"]


# --- the mission -------------------------------------------------------------------------

def _job(title: str, capability: str, brief: str = "", *, task_id: str = "",
         depends_on: list[str] | None = None) -> dict:
    return {"id": task_id, "title": title, "capability": capability, "brief": brief,
            "depends_on": list(depends_on or []), "status": "pending", "agent": None, "org": None}


def build_inputs(m: Mission, task, outputs: dict) -> dict:
    """What a task gets to work with: the mission, its brief, and what it depends on.

    Keyed by the capability that produced it rather than by task id, because an agent is hired
    to do a kind of work and should not have to know what the planner called the step before it.
    A task is given the output of its dependencies and nothing else — the plan decides who sees
    what, which is the same principle as the Guardian's scopes, one level up.
    """
    inputs = {"mission": m.text, "business": m.business, "brief": task.brief}
    dep_artifacts = {}
    for dep in task.depends_on:
        produced = outputs.get(dep)
        if not isinstance(produced, dict):
            continue
        dep_artifacts[dep] = produced
        if "html" in produced:
            inputs["html"] = produced["html"]
            inputs["previous_html"] = produced["html"]
        elif "approved" in produced:
            inputs["issues"] = produced.get("issues") or []
        elif "images" in produced:
            inputs["images"] = produced.get("images") or []
        elif "palette" in produced:
            inputs["brand_kit"] = produced
        else:
            inputs[dep] = produced
    if dep_artifacts:
        inputs["artifacts"] = dep_artifacts
    # A site builder needs the brand kit even when the plan only wired it to something else.
    if task.capability == "site.generate" and "brand_kit" not in inputs:
        inputs["brand_kit"] = next((o for o in outputs.values()
                                    if isinstance(o, dict) and "palette" in o), {})
    return inputs


async def run(m: Mission):
    try:
        await say(m, "mission.started", f"Mission received: {m.text}")
        plan, engine = await plan_mission(m.text, actor=me(), mission_id=m.id)
        m.engines.add(engine)
        m.business = plan.business
        m.plan = plan
        m.jobs = [_job(t.title, t.capability, t.brief, task_id=t.id, depends_on=t.depends_on)
                  for t in plan.tasks]
        await say(m, "mission.planned",
                  f"Planned {len(m.jobs)} job(s) for {m.business.get('name', 'this business')}",
                  engine=engine, tasks=[t.as_dict() for t in plan.tasks],
                  jobs=[{"title": j["title"], "capability": j["capability"]} for j in m.jobs])
        if plan.unsupported:
            # Say what we cannot do rather than quietly doing something adjacent.
            await say(m, "mission.unsupported",
                      "Out of scope for this crew: " + ", ".join(plan.unsupported),
                      capabilities=plan.unsupported)
        m.status = "working"
        await asyncio.sleep(config.pace())

        if m.scenario.get("impostor"):
            await hub_post("/api/chaos/impostor")
            await asyncio.sleep(config.pace() / 2)

        outputs: dict[str, dict] = {}
        fired: set[str] = set()
        # The plan is already in dependency order, so running it is a walk, not a scheduler.
        queue = list(zip(plan.tasks, m.jobs))
        while queue:
            task, job = queue.pop(0)

            # Chaos is keyed to the kind of work, not to a position in a fixed script.
            if task.capability == "compliance.review" and m.scenario.get("upgrade") and "upgrade" not in fired:
                fired.add("upgrade")
                await hub_post("/api/chaos/upgrade/compliance")
                await asyncio.sleep(config.pace())

            inputs = build_inputs(m, task, outputs)
            if task.capability == "site.generate" and "site" not in fired:
                fired.add("site")
                inputs["attempt_exfil"] = bool(m.scenario.get("exfil"))
                inputs["attempt_publish"] = bool(m.scenario.get("publish"))

            revoke_during = None
            if task.capability == "site.generate" and m.scenario.get("revoke") and "revoke" not in fired:
                revoke_during = "webforge"

            outputs[task.id] = await run_job(m, job, inputs, revoke_during=revoke_during)

            # Gemini Master Orchestrator supervises the task output
            supervision = await gemini_supervise_task(m, task, outputs[task.id], remaining=[t for t, _ in queue])
            await say(m, "orchestrator.supervision",
                      f"Orchestrator reviewed {task.title}: {supervision.get('status')} — {supervision.get('rationale')}",
                      task_id=task.id, status=supervision.get("status"), rationale=supervision.get("rationale"))

            # Dynamic security review injection if requested by Gemini and not yet scheduled
            if supervision.get("status") == "needs_security_review" and not any(j["capability"] == "security.review" for j in m.jobs):
                sec_task = PlanTask(id=f"{task.id}.sec", title="Security review",
                                    capability="security.review", brief=f"Security audit following {task.title}",
                                    depends_on=[task.id])
                plan.tasks.append(sec_task)
                new_sec_job = _job(sec_task.title, sec_task.capability, sec_task.brief,
                                   task_id=sec_task.id, depends_on=sec_task.depends_on)
                m.jobs.append(new_sec_job)
                queue.append((sec_task, new_sec_job))
                await say(m, "mission.task_added", f"Orchestrator added security review by ShieldSec following {task.title}",
                          task_id=sec_task.id, capability="security.review")

            # A failed review sends the work back. The plan did not know it would be needed, so
            # the tasks are added now, wired to the ones that already ran.
            review = outputs[task.id]
            if task.capability == "compliance.review" and not review.get("approved") and review.get("issues") \
                    and not any(j["title"] == "Fix review issues" for j in m.jobs):
                m.status = "reviewing"
                issues = review["issues"]
                await say(m, "mission.revision",
                          f"Compliance flagged {len(issues)} issue(s). Sending the work back for fixes.",
                          issues=issues)
                source = next((t for t in plan.tasks if t.capability == "site.generate"), None)
                if source:
                    fix = PlanTask(id=f"{task.id}.fix", title="Fix review issues",
                                   capability="site.generate", brief=source.brief,
                                   depends_on=[source.id, task.id])
                    again = PlanTask(id=f"{task.id}.recheck", title="Final review",
                                     capability="compliance.review", brief=task.brief,
                                     depends_on=[fix.id])
                    for extra in (fix, again):
                        plan.tasks.append(extra)
                        new_job = _job(extra.title, extra.capability, extra.brief,
                                       task_id=extra.id, depends_on=extra.depends_on)
                        m.jobs.append(new_job)
                        queue.append((extra, new_job))
                    m.status = "working"

        m.result_html = next((o["html"] for o in reversed(list(outputs.values()))
                              if isinstance(o, dict) and "html" in o), None)
        m.review = next((o for o in reversed(list(outputs.values()))
                         if isinstance(o, dict) and "approved" in o), None)
        m.status = "delivered"
        m.finished_at = now()
        orgs = sorted({h["org"] for h in m.hires})
        await say(m, "mission.delivered",
                  f"Delivered {m.business.get('name', 'the mission')}, built by {len(orgs)} verified agents",
                  stats=m.stats, orgs=orgs)
    except MissionFailed as exc:
        m.status, m.error, m.finished_at = "failed", str(exc), now()
        await say(m, "mission.failed", str(exc))
    except asyncio.CancelledError:
        m.status, m.finished_at = "cancelled", now()
        raise
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        m.status, m.error, m.finished_at = "failed", f"{type(exc).__name__}: {exc}", now()
        await say(m, "mission.failed", f"Mission crashed: {m.error}")


# --- API -------------------------------------------------------------------------------

class MissionBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    scenario: dict = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("text", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class DecisionBody(BaseModel):
    decision: str  # "approve" | "reject"


def _current() -> Mission | None:
    return MISSIONS.get(CURRENT["id"] or "")


@app.post("/missions")
async def start_mission(body: MissionBody):
    if body.idempotency_key and body.idempotency_key in SUBMISSIONS:
        previous = MISSIONS[SUBMISSIONS[body.idempotency_key]]
        if previous.text != body.text or previous.scenario != body.scenario:
            raise HTTPException(409, "Submission key already used for another objective")
        return previous.public()
    cur = _current()
    if cur and cur.status in ("planning", "working", "paused", "reviewing"):
        raise HTTPException(409, "A mission is already running")
    if not body.text.strip():
        raise HTTPException(400, "Describe the mission first")
    m = Mission(body.text.strip(), body.scenario)
    MISSIONS[m.id] = m
    if body.idempotency_key:
        SUBMISSIONS[body.idempotency_key] = m.id
    CURRENT["id"] = m.id
    m.task = asyncio.create_task(run(m))
    return m.public()


@app.get("/missions/current")
def current_mission():
    m = _current()
    return {"mission": m.public() if m else None}


@app.get("/missions/{mission_id}")
def get_mission(mission_id: str):
    m = MISSIONS.get(mission_id)
    if not m:
        raise HTTPException(404, "No such mission")
    return m.public()


@app.get("/missions/{mission_id}/result", response_class=HTMLResponse)
def mission_result(mission_id: str):
    m = MISSIONS.get(mission_id)
    if not m or not m.result_html:
        raise HTTPException(404, "No result yet")
    return m.result_html


@app.post("/missions/{mission_id}/decision")
def decide(mission_id: str, body: DecisionBody):
    m = MISSIONS.get(mission_id)
    if not m or not m.approval:
        raise HTTPException(409, "Nothing is waiting for approval")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be approve or reject")
    m.decision = body.decision
    m.approval_event.set()
    return {"ok": True}


@app.post("/admin/missions/reset")
def reset_missions():
    m = _current()
    if m and m.task and not m.task.done():
        m.task.cancel()
    CURRENT["id"] = None
    return {"ok": True}
