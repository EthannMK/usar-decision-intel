"""Gemini-powered triage of unstructured scout reports (Vertex AI).

Two AI capabilities:
  1. triage_report(): scout notes (+ optional photo) -> structured JSON with
     priority score 0.0-1.0, trapped estimate, required team type, hazards.
  2. substitution_plan(): if a team is missing primary equipment for a site,
     generate an alternative tactical engineering plan.

Both fall back to deterministic heuristics when Vertex AI is unreachable,
so the demo never stalls.
"""
import json
import os

from src.config import GCP_PROJECT, GCP_LOCATION, GEMINI_MODEL

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "priority_score": {"type": "number", "description": "0.0-1.0 rescue urgency"},
        "est_trapped": {"type": "integer"},
        "collapse_pattern": {"type": "string", "enum": ["pancake", "lean-to", "v-shape", "cantilever"]},
        "required_team_type": {"type": "string", "enum": ["Heavy", "Medium", "Light"]},
        "hazards": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string", "description": "1-2 sentence justification"},
    },
    "required": ["priority_score", "est_trapped", "collapse_pattern",
                 "required_team_type", "hazards", "reasoning"],
}

TRIAGE_PROMPT = """You are a USAR (Urban Search and Rescue) structural triage specialist
operating after a major earthquake in Myanmar. Analyze the scout field report below
(and photo if provided) and produce a triage assessment.

Scoring guidance:
- pancake collapse of reinforced concrete with confirmed life signs -> 0.85-1.0
- confirmed voices/tapping raises score; gas leak / flooding / aftershock risk raises score
- low trapped count + light structure -> lower score
- required_team_type: pancake/reinforced concrete -> Heavy; masonry lean-to/v-shape -> Medium; timber/light -> Light

SCOUT REPORT:
{notes}

Building type reported: {building_type}
Scout's trapped estimate: {est_trapped}
"""


def _client():
    from google import genai
    return genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)


def triage_report(notes: str, building_type: str = "unknown",
                  est_trapped: int = 0, image_bytes: bytes | None = None,
                  mime_type: str = "image/jpeg") -> dict:
    """Return structured triage JSON. Falls back to heuristic if API fails."""
    try:
        from google.genai import types
        client = _client()
        parts = [TRIAGE_PROMPT.format(notes=notes, building_type=building_type,
                                      est_trapped=est_trapped)]
        if image_bytes:
            parts.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=parts,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=TRIAGE_SCHEMA,
                temperature=0.2,
            ),
        )
        out = json.loads(resp.text)
        out["source"] = "gemini"
        return out
    except Exception as e:
        return _heuristic_triage(notes, building_type, est_trapped, str(e))


def _heuristic_triage(notes: str, building_type: str, est_trapped: int, err: str) -> dict:
    n = notes.lower()
    score = 0.4
    pattern = "lean-to"
    for p in ["pancake", "lean-to", "v-shape", "cantilever"]:
        if p in n:
            pattern = p
    score += {"pancake": 0.35, "v-shape": 0.2, "lean-to": 0.15, "cantilever": 0.0}[pattern]
    if any(k in n for k in ["voices", "tapping", "alive", "heard"]):
        score += 0.15
    if any(k in n for k in ["gas", "flood", "aftershock", "leaning"]):
        score += 0.1
    team = "Heavy" if (pattern == "pancake" or "concrete" in building_type) else (
        "Light" if building_type == "timber" else "Medium")
    return {
        "priority_score": round(min(score, 1.0), 2),
        "est_trapped": max(est_trapped, 1),
        "collapse_pattern": pattern,
        "required_team_type": team,
        "hazards": [h for h in ["gas leak", "flooding", "aftershock risk"] if h.split()[0] in n],
        "reasoning": "Heuristic fallback triage (Gemini unavailable).",
        "source": f"fallback ({err[:80]})",
    }


SUBSTITUTION_PROMPT = """You are a USAR heavy-rescue engineering officer. A rescue team has been
assigned to a collapse site but is MISSING primary equipment. Produce an alternative
tactical plan using ONLY the equipment the team actually carries.

SITE: {site_name} - {collapse_pattern} collapse of {building_type} structure,
~{est_trapped} people trapped. Hazards: {hazards}.

STANDARD EQUIPMENT REQUIRED for this collapse type: {required}
TEAM'S ACTUAL EQUIPMENT: {available}
MISSING: {missing}

Respond in JSON with:
  plan_steps: array of 4-6 concise numbered tactical steps using available equipment
  substitution_summary: one sentence stating what replaces the missing tool(s)
  risk_note: one sentence on added risk and mitigation
"""

SUB_SCHEMA = {
    "type": "object",
    "properties": {
        "plan_steps": {"type": "array", "items": {"type": "string"}},
        "substitution_summary": {"type": "string"},
        "risk_note": {"type": "string"},
    },
    "required": ["plan_steps", "substitution_summary", "risk_note"],
}

# Standard kit expected per collapse pattern
REQUIRED_KIT = {
    "pancake": ["50T mobile crane", "concrete cutter", "shoring kit", "search camera"],
    "lean-to": ["shoring kit", "pneumatic lift bags", "search camera"],
    "v-shape": ["concrete cutter", "cribbing kit", "search camera"],
    "cantilever": ["shoring kit", "hand tools", "rope rescue kit"],
}


def substitution_plan(site: dict, team: dict) -> dict:
    required = REQUIRED_KIT.get(site.get("collapse_pattern", "lean-to"), [])
    available = [e["item"] for e in team.get("equipment", [])]
    missing = [r for r in required if r not in available]
    if not missing:
        return {
            "plan_steps": [
                "1. Establish site safety perimeter and shut off utilities.",
                "2. Deploy search camera / acoustic devices to localize victims.",
                "3. Shore access points per standard cribbing procedure.",
                "4. Execute vertical/lateral extrication with full standard kit.",
            ],
            "substitution_summary": "No substitution needed - team carries full standard kit.",
            "risk_note": "Standard operating risk profile.",
        }
    try:
        from google.genai import types
        client = _client()
        prompt = SUBSTITUTION_PROMPT.format(
            site_name=site.get("site_name"), collapse_pattern=site.get("collapse_pattern"),
            building_type=site.get("building_type"), est_trapped=site.get("est_trapped"),
            hazards=site.get("hazards", "unknown"), required=", ".join(required),
            available=", ".join(available), missing=", ".join(missing))
        resp = client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=SUB_SCHEMA,
                temperature=0.3),
        )
        out = json.loads(resp.text)
        out["missing"] = missing
        out["source"] = "gemini"
        return out
    except Exception as e:
        return {
            "plan_steps": [
                "1. Establish perimeter; confirm victim location acoustically.",
                f"2. Substitute {missing[0]} with staged pneumatic lift bags / mechanical advantage rigging.",
                "3. Build box cribbing at each lift point; lift-and-crib in 2 cm increments.",
                "4. Insert progressive shoring as void opens; extract via smallest safe void.",
            ],
            "substitution_summary": f"Missing {', '.join(missing)}; compensate with incremental lift-and-crib technique.",
            "risk_note": "Slower extrication; monitor secondary collapse during each lift cycle.",
            "missing": missing,
            "source": f"fallback ({str(e)[:80]})",
        }
