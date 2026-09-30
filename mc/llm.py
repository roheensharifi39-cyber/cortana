"""Agent brains: Gemini, with offline templates as a fallback.

LLM_MODE=auto     use Gemini when GEMINI_API_KEY is set, fall back to templates on any error
LLM_MODE=gemini   always use Gemini, fail loudly on errors
LLM_MODE=offline  never call an LLM (fast, free, deterministic — good for rehearsals)
"""
import json
import re
from typing import Callable

from .config import env, policy
from .events import emit


def mode() -> str:
    """The system-wide answer: is a model available at all?"""
    m = (env("LLM_MODE", "auto") or "auto").lower()
    if m == "auto":
        return "gemini" if env("GEMINI_API_KEY") else "offline"
    return m


def mode_for(capability: str | None = None) -> str:
    """Which brain this particular kind of work should use.

    Per-capability rather than global, because the right answer differs by job: planning wants
    a model, and the page a judge looks at wants the template that renders the same way every
    time. LLM_MODE still overrides everything, so a rehearsal can be made fully deterministic
    with one environment variable.
    """
    configured = (env("LLM_MODE", "auto") or "auto").lower()
    if configured in ("gemini", "offline"):
        return configured
    if not env("GEMINI_API_KEY"):
        return "offline"
    if not capability:
        return "gemini"
    return str((policy().get("llm") or {}).get(capability, "gemini")).lower()


def _parse_json(text: str) -> dict:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    return json.loads(text)


async def generate_json(system: str, prompt: str, fallback: Callable[[], dict], *, label: str,
                        capability: str | None = None, actor: str | None = None,
                        mission_id: str | None = None) -> tuple[dict, str]:
    """Returns (result, engine) where engine is "gemini" or "offline"."""
    configured = (env("LLM_MODE", "auto") or "auto").lower()
    if mode_for(capability) == "offline":
        return fallback(), "offline"
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=env("GEMINI_API_KEY"))
        resp = await client.aio.models.generate_content(
            model=env("GEMINI_MODEL", "gemini-3.6-flash"),
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                temperature=0.7,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        return _parse_json(resp.text or ""), "gemini"
    except Exception as exc:  # noqa: BLE001 — any LLM failure should fall back, not crash the demo
        if configured == "gemini":
            raise
        await emit("llm.fallback", f"{label}: Gemini unavailable ({type(exc).__name__}), used offline template",
                   actor=actor, mission_id=mission_id, error=str(exc)[:300])
        return fallback(), "offline"
