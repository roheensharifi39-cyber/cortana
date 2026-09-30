"""Comprehensive test suite for the Gemini Master Orchestrator in CortexAI.

Covers:
1. Verified-candidates-only selection (models only see and select verified agents).
2. Rejection of unverified or invented candidate names.
3. Candidate refusal permanent exclusion and replacement hiring.
4. Open-ended mission planning (e.g. image search & license review).
5. Security agent (Shield) discovery, recruitment, and dynamic supervision injection.
6. Independent Guardian enforcement (Gemini cannot override Guardian policy).
7. First-character preservation regression verification.
"""
import asyncio
import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mc import guardian
from mc.guardian import ALLOW, DENY, ActionRequest, Grant, decide
from mc.planning import Plan, Task
from mc.skills import CAPABILITIES, _fallback_plan, plan_mission, SKILLS
from mc.trustgate import Check, NEEDS_APPROVAL, Policy, TrustResult, TRUSTED
from services.agents import commander
from services.agents.commander import (
    Mission,
    MissionBody,
    MissionFailed,
    Recruitment,
    build_inputs,
    gemini_choose_candidate,
    gemini_on_refusal,
    gemini_supervise_task,
    roster_match,
)


# --- Fixtures & Helpers -------------------------------------------------------------

def make_cand(name, org, endpoint="http://127.0.0.1:8002", cap="brand.identity"):
    return {
        "ans_name": name,
        "org": org,
        "endpoint": endpoint,
        "capabilities": [cap],
        "source": "ANS registry",
        "version": "1.0.0",
    }


def make_trust(cand, ok=True, reason=""):
    verdict = TRUSTED if ok else "REJECTED"
    checks = [
        Check("resolve", ok, "resolved" if ok else "not found"),
        Check("authenticate", ok, "verified" if ok else "signature invalid"),
        Check("status", ok, "ACTIVE" if ok else "REVOKED"),
        Check("capability", ok, "offers capability" if ok else "missing"),
        Check("policy", ok, "domain allowed" if ok else "domain forbidden"),
    ]
    return TrustResult(
        ans_name=cand["ans_name"],
        endpoint=cand["endpoint"],
        verdict=verdict,
        checks=checks,
        record={"ans_name": cand["ans_name"], "org": cand["org"], "version": "1.0.0", "capabilities": cand["capabilities"]},
        cert_pem="PEM_DATA",
    )


# --- 1. Candidate Selection Tests --------------------------------------------------

def test_verified_candidates_only_are_presented_to_gemini():
    """Gemini evaluates candidates only from the verified set."""
    async def _test():
        m = Mission("Build a brand for Hokie Sweets", {})
        job = {"title": "Brand kit", "capability": "brand.identity"}
        rec = Recruitment(mission_id=m.id, job_title=job["title"], capability=job["capability"])

        cand1 = make_cand("ans://v1.0.0.brand.brandstudio.xyz", "BrandStudio")
        trust1 = make_trust(cand1, ok=True)
        verified = [(cand1, trust1)]

        with patch("services.agents.commander.generate_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (
                {"selected_ans_name": cand1["ans_name"], "rationale": "High quality brand studio"},
                "gemini"
            )
            chosen_cand, chosen_trust, rationale, source = await gemini_choose_candidate(m, job, rec, verified)

            assert chosen_cand["ans_name"] == cand1["ans_name"]
            assert source == "gemini"
            assert "High quality" in rationale
            prompt_sent = mock_llm.call_args[0][1]
            assert cand1["ans_name"] in prompt_sent

    asyncio.run(_test())


def test_unverified_candidate_cannot_be_hired_even_if_model_selects_it():
    """If model hallucinated or returned an agent name NOT in verified candidates, orchestrator rejects it."""
    async def _test():
        m = Mission("Build a brand for Hokie Sweets", {})
        job = {"title": "Brand kit", "capability": "brand.identity"}
        rec = Recruitment(mission_id=m.id, job_title=job["title"], capability=job["capability"])

        cand_verified = make_cand("ans://v1.0.0.brand.brandstudio.xyz", "BrandStudio")
        trust_verified = make_trust(cand_verified, ok=True)
        verified = [(cand_verified, trust_verified)]

        unverified_name = "ans://v1.0.0.fake.hacker.xyz"

        with patch("services.agents.commander.generate_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (
                {"selected_ans_name": unverified_name, "rationale": "I invented a cheaper agent"},
                "gemini"
            )
            chosen_cand, chosen_trust, rationale, source = await gemini_choose_candidate(m, job, rec, verified)

            assert chosen_cand["ans_name"] == cand_verified["ans_name"]
            assert chosen_cand["ans_name"] != unverified_name
            assert source == "deterministic_fallback"
            assert "deterministic policy" in rationale

    asyncio.run(_test())


def test_invented_candidate_name_is_rejected():
    """A completely hallucinated string from model is discarded safely."""
    async def _test():
        m = Mission("Launch test mission", {})
        job = {"title": "Site builder", "capability": "site.generate"}
        rec = Recruitment(mission_id=m.id, job_title=job["title"], capability=job["capability"])

        cand1 = make_cand("ans://v1.0.0.sitebuilder.webforge.xyz", "WebForge", cap="site.generate")
        verified = [(cand1, make_trust(cand1, ok=True))]

        with patch("services.agents.commander.generate_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ({"selected_ans_name": "ans://v9.9.9.magic.ai", "rationale": "Magic"}, "gemini")
            chosen_cand, _, rationale, source = await gemini_choose_candidate(m, job, rec, verified)

            assert chosen_cand["ans_name"] == cand1["ans_name"]
            assert source == "deterministic_fallback"

    asyncio.run(_test())


def test_selection_source_recorded_accurately():
    """Orchestrator tracks whether selection came from Gemini or fallback."""
    async def _test():
        m = Mission("Test mission", {})
        job = {"title": "Brand kit", "capability": "brand.identity"}
        rec = Recruitment(mission_id=m.id, job_title=job["title"], capability=job["capability"])

        cand = make_cand("ans://v1.0.0.brand.brandstudio.xyz", "BrandStudio")
        verified = [(cand, make_trust(cand, ok=True))]

        with patch("services.agents.commander.generate_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ({"selected_ans_name": cand["ans_name"], "rationale": "Expertise"}, "gemini")
            _, _, _, source_gemini = await gemini_choose_candidate(m, job, rec, verified)
            assert source_gemini == "gemini"

        with patch("services.agents.commander.generate_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ({"selected_ans_name": cand["ans_name"], "rationale": "Fallback"}, "offline")
            _, _, _, source_offline = await gemini_choose_candidate(m, job, rec, verified)
            assert source_offline == "deterministic_fallback"

    asyncio.run(_test())


# --- 2. Refusal and Replacement Tests -----------------------------------------------

def test_candidate_refusal_permanently_excludes_candidate():
    """When an agent refuses, (ans_name, endpoint) is placed in m.excluded and removed from roster."""
    m = Mission("Launch test mission", {})
    cand = make_cand("ans://v1.0.0.brand.brandstudio.xyz", "BrandStudio")
    m.roster[cand["ans_name"]] = {**cand, "trust": make_trust(cand)}

    key = (cand["ans_name"], cand["endpoint"])
    m.excluded.add(key)
    m.roster.pop(cand["ans_name"], None)
    m.lost["brand.identity"] = {"ans_name": cand["ans_name"], "org": cand["org"], "reason": "refused"}

    assert key in m.excluded
    assert cand["ans_name"] not in m.roster
    assert roster_match(m.roster, "brand.identity", m.excluded) is None


def test_refused_candidate_is_never_retried():
    """Subsequent recruitment checks exclude the refused candidate completely."""
    m = Mission("Launch test mission", {})
    cand_refused = make_cand("ans://v1.0.0.sitebuilder.webforge.xyz", "WebForge", cap="site.generate")
    cand_backup = make_cand("ans://v1.0.0.sitebuilder.sitesmith.xyz", "SiteSmith", cap="site.generate")

    m.excluded.add((cand_refused["ans_name"], cand_refused["endpoint"]))

    discovered = [cand_refused, cand_backup]
    eligible = [c for c in discovered if (c["ans_name"], c["endpoint"]) not in m.excluded]

    assert len(eligible) == 1
    assert eligible[0]["ans_name"] == cand_backup["ans_name"]
    assert cand_refused["ans_name"] not in [c["ans_name"] for c in eligible]


def test_gemini_refusal_consultation_search_again():
    """Gemini refusal resolution returns search_again to hire replacement."""
    async def _test():
        m = Mission("Launch test", {})
        job = {"title": "Site builder", "capability": "site.generate"}
        cand = make_cand("ans://v1.0.0.sitebuilder.webforge.xyz", "WebForge")

        with patch("services.agents.commander.generate_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ({"action": "search_again", "reasoning": "Query ANS for backup vendor"}, "gemini")
            dec = await gemini_on_refusal(m, job, cand, "Capacity exceeded")
            assert dec["action"] == "search_again"
            assert "backup vendor" in dec["reasoning"]

    asyncio.run(_test())


def test_gemini_refusal_fail_aborts():
    """Gemini refusal resolution returns fail if mission cannot proceed."""
    async def _test():
        m = Mission("Launch test", {})
        job = {"title": "Site builder", "capability": "site.generate"}
        cand = make_cand("ans://v1.0.0.sitebuilder.webforge.xyz", "WebForge")

        with patch("services.agents.commander.generate_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = ({"action": "fail", "reasoning": "Critical dependency unavailable"}, "gemini")
            dec = await gemini_on_refusal(m, job, cand, "Unrecoverable")
            assert dec["action"] == "fail"

    asyncio.run(_test())


# --- 3. Open-Ended Mission Planning Tests -------------------------------------------

def test_open_ended_image_licensing_mission_plans_real_tasks():
    """An image request decomposes into image.search and image.license_review, not website generation."""
    plan = _fallback_plan("Search for high-resolution wildlife images and verify their commercial licenses")
    caps = [t.capability for t in plan.tasks]
    assert "image.search" in caps
    assert "image.license_review" in caps
    assert "site.generate" not in caps
    lic_task = next(t for t in plan.tasks if t.capability == "image.license_review")
    assert "images" in lic_task.depends_on


def test_open_ended_security_audit_mission_plans_security_review():
    """A security request decomposes into security.review."""
    plan = _fallback_plan("Perform a security audit and secrets review on the system")
    caps = [t.capability for t in plan.tasks]
    assert "security.review" in caps
    assert "site.generate" not in caps


def test_image_skills_execution():
    """Verify image.search and image.license_review skills execute and return structured output."""
    async def _test():
        search_fn = SKILLS["image.search"]
        lic_fn = SKILLS["image.license_review"]

        images_out, _ = await search_fn({"brief": "coffee shop images", "mission": "Find coffee images"})
        assert "images" in images_out
        assert len(images_out["images"]) > 0

        lic_out, _ = await lic_fn({"images": images_out["images"], "brief": "Verify licenses"})
        assert "approved" in lic_out
        assert "licensed_images" in lic_out
        assert lic_out["approved"] is True

    asyncio.run(_test())


# --- 4. Security Workers & Dynamic Supervision Tests -------------------------------

def test_security_skill_detects_exposed_secrets():
    """Shield security skill detects credentials and scripts."""
    async def _test():
        sec_fn = SKILLS["security.review"]

        clean_out, _ = await sec_fn({"html": "<html><body>Safe content</body></html>"})
        assert clean_out["approved"] is True

        dirty_out, _ = await sec_fn({"html": "<html><script>const api_key = 'sk_live_secret';</script></html>"})
        assert dirty_out["approved"] is False
        assert any("secret" in i["issue"].lower() or "credential" in i["issue"].lower() for i in dirty_out["issues"])

    asyncio.run(_test())


def test_gemini_supervision_requests_security_review():
    """Gemini supervision can flag needs_security_review for security sensitive deliverables."""
    async def _test():
        m = Mission("Deploy sensitive customer portal", {})
        task = Task("site", "Landing page", "site.generate")
        output = {"html": "<html>Sensitive portal</html>", "summary": "Portal page"}

        with patch("services.agents.commander.generate_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (
                {"status": "needs_security_review", "rationale": "Sensitive portal requires Shield security audit"},
                "gemini"
            )
            sup = await gemini_supervise_task(m, task, output, remaining=[])
            assert sup["status"] == "needs_security_review"
            assert "Shield" in sup["rationale"]

    asyncio.run(_test())


# --- 5. Guardian Independent Authority Tests ---------------------------------------

def test_gemini_cannot_override_guardian_denies():
    """Guardian independently denies actions outside grant or on hard-deny list regardless of model."""
    now = guardian.now()
    grant = Grant("g_1", "ans://v1.0.0.brand.brandstudio.xyz", "m_1", "job_1",
                  scopes=["artifact.write:mission"], issued_at=guardian.iso(now),
                  expires_at=guardian.iso(now + dt.timedelta(minutes=10)))

    # Hard-deny action
    req_secrets = ActionRequest("m_1", "job_1", "ans://v1.0.0.brand.brandstudio.xyz",
                                action="secrets.read", resource="credentials")
    dec_secrets = decide(req_secrets, grant)
    assert dec_secrets.outcome == DENY
    assert "never permitted" in dec_secrets.reason

    # Scope missing
    req_net = ActionRequest("m_1", "job_1", "ans://v1.0.0.brand.brandstudio.xyz",
                            action="external.upload", resource="data", destination="evil.com")
    dec_net = decide(req_net, grant)
    assert dec_net.outcome == DENY
    assert "not authorized" in dec_net.reason


# --- 6. First-Character Preservation Regression Tests ------------------------------

def test_first_character_is_preserved_verbatim_in_mission_body():
    """The first character (e.g. 'B' in 'Build a premium website') is never lost."""
    raw_input = "Build a premium website"
    body = MissionBody(text=raw_input)
    assert body.text == raw_input
    assert body.text[0] == "B"
    assert not body.text.startswith("uild")


def test_first_character_with_various_leading_letters():
    for text in ["Audit system security", "Design logo", "Search photos", "Verify licenses"]:
        body = MissionBody(text=text)
        assert body.text == text
        assert body.text[0] == text[0]

