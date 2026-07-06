"""
Vertex AI Gemini 1.5 Flash integration for the USAR Decision Intelligence Platform.

Three jobs:
  1. score_incident_priority()      - unstructured scout notes/image -> structured priority JSON
  2. generate_equipment_substitution() - missing/damaged primary tool -> alternative tactical plan
  3. assess_satellite_damage()      - satellite/aerial image -> estimated % impact area

Requires (once you've completed SETUP_GUIDE.md):
  - GOOGLE_APPLICATION_CREDENTIALS env var pointing at gcp-service-account.json
  - GCP_PROJECT_ID env var (your project id, e.g. "usar-decision-intel")
  - Vertex AI API enabled on that project

Until credentials exist, run this file directly - it falls back to a clearly-labeled mock
response so the rest of the pipeline (OR-Tools, Streamlit) can be built and demoed today.
"""

import json
import os

PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
LOCATION = os.environ.get("GCP_LOCATION", "asia-southeast1")
MODEL_NAME = "gemini-1.5-flash"

PRIORITY_SCHEMA = {
    "type": "object",
    "properties": {
        "priority_score": {"type": "number", "description": "0.0 (low) to 1.0 (critical) urgency"},
        "trapped_estimate_confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "structural_risk": {"type": "string", "enum": ["low", "medium", "high", "imminent_collapse"]},
        "key_factors": {"type": "array", "items": {"type": "string"}},
        "recommended_team_type": {"type": "string", "enum": ["Heavy", "Medium", "Light"]},
        "rationale": {"type": "string"},
    },
    "required": ["priority_score", "structural_risk", "recommended_team_type", "rationale"],
}

SATELLITE_DAMAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "estimated_impact_area_pct": {"type": "number", "description": "0-100, % of the visible area showing collapse/severe damage"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "damage_severity": {"type": "string", "enum": ["minimal", "moderate", "severe", "catastrophic"]},
        "affected_zone_description": {"type": "string", "description": "Which part of the image is most affected"},
        "visible_indicators": {"type": "array", "items": {"type": "string"}, "description": "e.g. collapsed roofs, rubble, road cracking, debris fields"},
        "rationale": {"type": "string"},
    },
    "required": ["estimated_impact_area_pct", "damage_severity", "rationale"],
}

SUBSTITUTION_SCHEMA = {
    "type": "object",
    "properties": {
        "missing_item": {"type": "string"},
        "substitution_plan": {"type": "string", "description": "Step-by-step alternative tactical/engineering plan"},
        "alternative_equipment": {"type": "array", "items": {"type": "string"}},
        "added_risk": {"type": "string", "enum": ["none", "low", "medium", "high"]},
        "estimated_extra_minutes": {"type": "number"},
    },
    "required": ["missing_item", "substitution_plan", "alternative_equipment"],
}


def _get_model():
    import vertexai
    from vertexai.generative_models import GenerativeModel

    if not PROJECT_ID:
        raise RuntimeError("GCP_PROJECT_ID not set - see SETUP_GUIDE.md")
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    return GenerativeModel(MODEL_NAME)


def score_incident_priority(scout_notes: str, building_material: str = "", collapse_pattern: str = "",
                             trapped_count: int = None, image_path: str = None) -> dict:
    """Evaluate a scout report and return a structured priority score."""
    prompt = f"""You are triaging earthquake collapse sites for a search-and-rescue command center.
Evaluate this site report and score its rescue priority.

Scout notes: {scout_notes}
Building material: {building_material or "unknown"}
Collapse pattern: {collapse_pattern or "unknown"}
Reported trapped count: {trapped_count if trapped_count is not None else "unknown"}

Return your assessment as JSON matching the required schema. Weigh structural instability and
survivor signals (voices, sounds, confirmed occupants) most heavily - a small trapped count in an
imminently-collapsing structure can outrank a larger count in a stable one."""

    try:
        from vertexai.generative_models import GenerationConfig, Part, Image

        model = _get_model()
        parts = [prompt]
        if image_path:
            parts.append(Part.from_image(Image.load_from_file(image_path)))

        response = model.generate_content(
            parts,
            generation_config=GenerationConfig(
                response_mime_type="application/json",
                response_schema=PRIORITY_SCHEMA,
                temperature=0.2,
            ),
        )
        return json.loads(response.text)

    except Exception as e:  # noqa: BLE001 - fall back to mock so the rest of the app keeps working
        return {
            "priority_score": 0.7,
            "structural_risk": "high",
            "trapped_estimate_confidence": "medium",
            "key_factors": ["MOCK RESPONSE - Vertex AI not yet connected", str(e)[:120]],
            "recommended_team_type": "Heavy",
            "rationale": "Mock fallback: connect GOOGLE_APPLICATION_CREDENTIALS + GCP_PROJECT_ID "
                         "to get real Gemini scoring (see SETUP_GUIDE.md).",
        }


def generate_equipment_substitution(missing_item: str, available_equipment: list, site_context: str) -> dict:
    """When a primary tool is missing/damaged, ask Gemini for an alternative tactical plan."""
    equipment_list = ", ".join(
        f"{e['item_name']} (x{e['quantity']}, {e['condition']})" for e in available_equipment
    )
    prompt = f"""A rescue team is missing or has a damaged "{missing_item}" needed for this site.

Site context: {site_context}
Equipment actually available on this team: {equipment_list}

Generate a safe, practical alternative engineering/tactical plan using ONLY the available
equipment (e.g. pneumatic lift bags + cribbing instead of a crane). Return JSON matching the
required schema."""

    try:
        from vertexai.generative_models import GenerationConfig

        model = _get_model()
        response = model.generate_content(
            prompt,
            generation_config=GenerationConfig(
                response_mime_type="application/json",
                response_schema=SUBSTITUTION_SCHEMA,
                temperature=0.3,
            ),
        )
        return json.loads(response.text)

    except Exception as e:  # noqa: BLE001
        return {
            "missing_item": missing_item,
            "substitution_plan": "MOCK RESPONSE - Vertex AI not yet connected. Once live, Gemini "
                                  "will propose e.g. pneumatic lift bags + timber cribbing as a "
                                  "50T crane substitute.",
            "alternative_equipment": [e["item_name"] for e in available_equipment],
            "added_risk": "medium",
            "estimated_extra_minutes": 20,
            "_error": str(e)[:120],
        }


def assess_satellite_damage(image_path: str, region_name: str = "") -> dict:
    """Independent of scout-submitted data: feeds a satellite/aerial image straight to Gemini's
    vision capability and asks it to estimate the % of the visible area showing collapse damage.
    This is a real, cheap use of Gemini multimodal input (a few hundred tokens per image, well
    under a cent) - not a dedicated CV/change-detection model, but works today with the same
    Vertex AI setup already used for scout note scoring, and needs no new account or free-tier
    satellite imagery pipeline to demo."""
    prompt = f"""You are a disaster-response damage assessment analyst reviewing a satellite or
aerial image of {region_name or "an earthquake-affected area"} taken after a major earthquake.

Estimate what percentage of the visible built-up area shows collapse or severe structural
damage (collapsed roofs, rubble piles, flattened structures, visible debris fields, cracked or
blocked roads). Be conservative - only count damage you can actually see, and say so if the
image quality or resolution limits your confidence. Return JSON matching the required schema."""

    try:
        from vertexai.generative_models import GenerationConfig, Part, Image

        model = _get_model()
        response = model.generate_content(
            [prompt, Part.from_image(Image.load_from_file(image_path))],
            generation_config=GenerationConfig(
                response_mime_type="application/json",
                response_schema=SATELLITE_DAMAGE_SCHEMA,
                temperature=0.1,
            ),
        )
        return json.loads(response.text)

    except Exception as e:  # noqa: BLE001
        return {
            "estimated_impact_area_pct": 0,
            "confidence": "low",
            "damage_severity": "unknown",
            "affected_zone_description": "MOCK RESPONSE - Vertex AI not yet connected",
            "visible_indicators": [],
            "rationale": "Connect GOOGLE_APPLICATION_CREDENTIALS + GCP_PROJECT_ID to get a real "
                         "assessment (see SETUP_GUIDE.md).",
            "_error": str(e)[:200],
        }


if __name__ == "__main__":
    result = score_incident_priority(
        scout_notes="Multiple voices heard from second floor, heavy debris on north side, "
                     "visible cracking on load-bearing walls.",
        building_material="reinforced_concrete",
        collapse_pattern="pancake",
        trapped_count=3,
    )
    print(json.dumps(result, indent=2))

    sub = generate_equipment_substitution(
        missing_item="50T Crane",
        available_equipment=[
            {"item_name": "Pneumatic Lift Bags", "quantity": 6, "condition": "operational"},
            {"item_name": "Cribbing Set", "quantity": 3, "condition": "operational"},
        ],
        site_context="Pancake-collapsed 3-story reinforced concrete building, survivors on 2nd floor.",
    )
    print(json.dumps(sub, indent=2))
