"""Generate synthetic earthquake incident dataset around Mandalay, Myanmar.

Produces:
  data/sites.csv   - collapsed building sites reported by scout teams
  data/teams.json  - USAR rescue teams with nested equipment inventories

Run:  python data/generate_data.py
"""
import csv
import json
import os
import random
from datetime import datetime, timedelta, timezone

random.seed(42)

HERE = os.path.dirname(os.path.abspath(__file__))
EPICENTER = (21.9588, 96.0891)  # Mandalay
N_SITES = 300
EVENT_TIME = datetime.now(timezone.utc) - timedelta(hours=6)  # quake hit 6h ago

BUILDING_TYPES = ["reinforced_concrete", "brick_masonry", "timber", "steel_frame", "mixed"]
COLLAPSE_PATTERNS = ["pancake", "lean-to", "v-shape", "cantilever"]
TOWNSHIPS = [
    "Chanayethazan", "Mahaaungmye", "Chanmyathazi", "Pyigyidagun", "Aungmyethazan",
    "Amarapura", "Patheingyi", "Sagaing", "Tada-U", "Myitnge", "Kyaukse", "Singu",
]
NOTES_TEMPLATES = [
    "Multi-story {b} building, {c} collapse. Voices heard from rubble. {n} people believed inside. Dust and debris blocking main road.",
    "{b} structure fully down, {c} pattern. Neighbors report {n} residents missing. Strong smell of gas nearby.",
    "Partial {c} collapse of {b} apartment block. Around {n} trapped, tapping sounds confirmed on second floor slab.",
    "School building ({b}) collapsed in {c} configuration. Estimated {n} children and staff unaccounted for. Access road cracked.",
    "Market hall {b}, {c} failure of roof. Vendors estimate {n} people under the structure. Aftershock risk, wall leaning.",
    "Residential {b} home, {c} collapse. Family of {n} not evacuated. Water pipe burst flooding the basement void.",
]


def _rand_point(max_km: float):
    """Random offset from epicenter within max_km (rough deg conversion)."""
    dlat = random.uniform(-1, 1) * max_km / 111.0
    dlon = random.uniform(-1, 1) * max_km / (111.0 * 0.93)
    return EPICENTER[0] + dlat, EPICENTER[1] + dlon


def make_sites():
    rows = []
    for i in range(1, N_SITES + 1):
        urban = random.random() < 0.7
        lat, lon = _rand_point(12 if urban else 45)
        btype = random.choices(BUILDING_TYPES, weights=[30, 35, 15, 10, 10])[0]
        pattern = random.choices(COLLAPSE_PATTERNS, weights=[25, 30, 25, 20])[0]
        trapped = max(1, int(random.expovariate(1 / 6)))
        # crude prior severity (Gemini refines this later for new reports)
        sev = {"pancake": 0.9, "lean-to": 0.65, "v-shape": 0.7, "cantilever": 0.45}[pattern]
        priority = round(min(1.0, sev * (0.6 + min(trapped, 25) / 40) + random.uniform(-0.05, 0.05)), 2)
        reported = EVENT_TIME + timedelta(minutes=random.randint(20, 330))
        road = random.choices(["paved", "unpaved", "damaged"], weights=[55 if urban else 20, 30 if urban else 55, 15 if urban else 25])[0]
        notes = random.choice(NOTES_TEMPLATES).format(b=btype.replace("_", " "), c=pattern, n=trapped)
        rows.append({
            "site_id": f"S{i:04d}",
            "site_name": f"{random.choice(TOWNSHIPS)} Site {i}",
            "township": random.choice(TOWNSHIPS),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "building_type": btype,
            "collapse_pattern": pattern,
            "est_trapped": trapped,
            "priority_score": max(0.05, priority),
            "road_type": road,
            "status": "awaiting_rescue",
            "reported_at": reported.isoformat(),
            "scout_notes": notes,
        })
    return rows


def make_teams():
    specs = [
        ("Heavy", 3, 60, ["50T mobile crane", "concrete cutter", "hydraulic breaker",
                          "search camera", "thermal imager", "K9 unit", "shoring kit"]),
        ("Medium", 5, 35, ["pneumatic lift bags", "rotary rescue saw", "search camera",
                           "cribbing kit", "jackhammer", "K9 unit"]),
        ("Light", 4, 18, ["hand tools", "pry bars", "rope rescue kit", "first aid station",
                          "acoustic listening device"]),
    ]
    teams, tid = [], 1
    for ttype, count, personnel, base_equipment in specs:
        for j in range(count):
            lat, lon = _rand_point(20)
            equipment = []
            for item in base_equipment:
                # randomly knock out one item sometimes -> triggers Gemini substitution logic
                if random.random() < 0.15:
                    continue
                equipment.append({"item": item, "qty": random.randint(1, 3), "operational": True})
            teams.append({
                "team_id": f"T{tid:02d}",
                "team_name": f"{ttype}-{j + 1} USAR",
                "team_type": ttype,
                "base_lat": round(lat, 6),
                "base_lon": round(lon, 6),
                "personnel": personnel + random.randint(-4, 4),
                "status": "available",
                "equipment": equipment,
            })
            tid += 1
    return teams


if __name__ == "__main__":
    sites = make_sites()
    with open(os.path.join(HERE, "sites.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(sites[0].keys()))
        w.writeheader()
        w.writerows(sites)
    teams = make_teams()
    with open(os.path.join(HERE, "teams.json"), "w", encoding="utf-8") as f:
        json.dump(teams, f, indent=2)
    print(f"Wrote {len(sites)} sites -> data/sites.csv")
    print(f"Wrote {len(teams)} teams -> data/teams.json")
