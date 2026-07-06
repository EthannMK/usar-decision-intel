"""
Hybrid dataset generator for the USAR Decision Intelligence Platform.

Scoped to SAGAING REGION, Myanmar - the actual epicenter area of the real M7.7 2025 Mandalay
earthquake this project is themed around (see ai/earthquake_feed.py for the live USGS event
data). Incidents, rescue teams, and scouts are only generated within Sagaing region townships;
three national hub cities (Yangon, Mandalay, Naypyidaw) are included as base-only reference
points so the map still shows the full national response network, not just a cropped region.

Produces:
  1. incidents.csv       - 10,000+ synthetic collapsed-structure records (expanded USAR triage
                            fields), within Sagaing region townships only.
  2. rescue_teams.csv     - Heavy/Medium/Light teams, each affiliated with either the national
                            rescue department or an NGO (org_type).
  3. scouts.csv           - field scout personnel as trackable map entities (status + location).
  4. road_nodes.csv       - intersections of a CONNECTED synthetic road network (a local grid per
                            township + an inter-town + national-hub backbone).
  5. roads.csv            - road segments (edges) between road_nodes, paved/unpaved.
  6. bases.csv            - National Rescue Department + NGO hub locations per Sagaing township,
                            plus the 3 national hub cities (coordination-only, no incidents).
  7. road_status.csv      - a handful of seed scout-reported road blockages.

Runs on plain pandas by default. To prove the NVIDIA RAPIDS acceleration claim, run the
exact same script with:
    python -m cudf.pandas generate_synthetic_data.py
on a CUDA GPU (e.g. Google Colab) - cudf.pandas patches pandas transparently, no code changes.
"""

import json
import random
import uuid
from datetime import datetime, timedelta

import pandas as pd

random.seed(42)

# ---------------------------------------------------------------------------
# REAL Sagaing Region townships (approximate city-center coordinates) - this is the actual
# epicenter region of the 2025 M7.7 Mandalay earthquake. Incidents/teams/scouts are scattered
# around these. Weight roughly reflects population/urban density.
# ---------------------------------------------------------------------------
SAGAING_TOWNSHIPS = [
    {"name": "Sagaing",  "lat": 21.8800, "lon": 95.9800, "weight": 20},
    {"name": "Monywa",   "lat": 22.1167, "lon": 95.1333, "weight": 22},
    {"name": "Shwebo",   "lat": 22.5667, "lon": 95.7000, "weight": 15},
    {"name": "Kalay",    "lat": 23.1833, "lon": 94.0500, "weight": 12},
    {"name": "Katha",    "lat": 24.1833, "lon": 96.3333, "weight": 8},
    {"name": "Myinmu",   "lat": 22.0500, "lon": 95.6333, "weight": 10},
    {"name": "Ye-U",     "lat": 22.8000, "lon": 95.4200, "weight": 7},
    {"name": "Kanbalu",  "lat": 23.2000, "lon": 95.5800, "weight": 6},
]

# National coordination hubs - base/HQ locations only (no incidents or teams originate here),
# so the map still shows the full country and how national reinforcements would stage in.
NATIONAL_HUBS = [
    {"name": "Yangon",     "lat": 16.8409, "lon": 96.1735},
    {"name": "Mandalay",   "lat": 21.9588, "lon": 96.0891},
    {"name": "Naypyidaw",  "lat": 19.7633, "lon": 96.0785},
]

BUILDING_MATERIALS = ["reinforced_concrete", "unreinforced_masonry", "wood_frame", "steel_frame", "mixed_masonry"]
COLLAPSE_PATTERNS = ["pancake", "lean_to", "v_shape", "cantilever", "partial_collapse"]
BUILDING_USE = ["residential", "commercial", "school", "hospital", "mixed_use", "industrial", "government"]
HAZARDS = ["gas_leak", "fire", "electrical_hazard", "chemical_spill", "flooding", "unstable_secondary_structure"]
SIGNS_OF_LIFE = ["none_detected", "sound_heard", "visual_confirmed", "canine_alert", "family_confirmed_occupants"]
ACCESS_DIFFICULTY = ["clear", "partial_debris", "heavy_debris_impassable_for_heavy_vehicles"]

TEAM_TYPES = ["Heavy", "Medium", "Light"]
ORG_TYPES = ["national_rescue_dept", "ngo"]
EQUIPMENT_CATALOG = {
    "Heavy": [("50T Crane", 1), ("Hydraulic Spreader", 4), ("Concrete Breaker", 2), ("Pneumatic Lift Bags", 6)],
    "Medium": [("Hydraulic Spreader", 2), ("Cribbing Set", 3), ("Thermal Imaging Camera", 1), ("Rescue Struts", 4)],
    "Light": [("Hand Tools Kit", 5), ("Search Camera (Snake)", 1), ("Listening Device", 1), ("First Aid Kit", 3)],
}
CAPABILITIES_CATALOG = {
    "Heavy": ["structural_shoring", "heavy_lifting", "confined_space", "crane_operations"],
    "Medium": ["structural_shoring", "confined_space", "canine"],
    "Light": ["search_and_triage", "first_aid", "canine"],
}
NGO_NAMES = ["Red Cross", "Global Medic Relief", "Myanmar Community Rescue Alliance", "World Emergency Aid"]
STATUS_CHOICES = ["available", "en_route", "on_site", "resting"]  # idle / assigned-moving / operation / rest


def weighted_township():
    return random.choices(SAGAING_TOWNSHIPS, weights=[a["weight"] for a in SAGAING_TOWNSHIPS], k=1)[0]


def jitter(value, spread=0.05):
    return value + random.uniform(-spread, spread)


def haversine_km(lat1, lon1, lat2, lon2):
    import math
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Incidents (Sagaing region only)
# ---------------------------------------------------------------------------
def generate_incidents(n=10500):
    now = datetime.utcnow()
    rows = []
    for _ in range(n):
        anchor = weighted_township()
        reported_at = now - timedelta(hours=random.uniform(0, 48))
        stories = random.choices([1, 2, 3, 4, 5, 7, 10, 15], weights=[15, 25, 25, 15, 10, 5, 3, 2], k=1)[0]
        confirmed = random.choices([0, 1, 2, 3, 5], weights=[50, 25, 15, 7, 3], k=1)[0]
        estimated = confirmed + random.choices([0, 1, 2, 5, 10], weights=[40, 25, 20, 10, 5], k=1)[0]
        n_hazards = random.choices([0, 1, 2], weights=[60, 30, 10], k=1)[0]
        hazards = random.sample(HAZARDS, k=n_hazards) if n_hazards else []
        rows.append({
            "incident_id": str(uuid.uuid4()),
            "reported_at": reported_at.isoformat(),
            "lat": round(jitter(anchor["lat"]), 6),
            "lon": round(jitter(anchor["lon"]), 6),
            "nearest_township": anchor["name"],
            "building_material": random.choice(BUILDING_MATERIALS),
            "building_stories": stories,
            "building_use": random.choice(BUILDING_USE),
            "collapse_pattern": random.choice(COLLAPSE_PATTERNS),
            "confirmed_trapped_count": confirmed,
            "estimated_trapped_count": estimated,
            "trapped_count": max(confirmed, estimated),
            "signs_of_life": random.choice(SIGNS_OF_LIFE),
            "hazards_present": json.dumps(hazards),
            "access_difficulty": random.choices(ACCESS_DIFFICULTY, weights=[50, 35, 15], k=1)[0],
            "scout_notes": random.choice([
                "Multiple voices heard from second floor, heavy debris on north side.",
                "Structure unstable, visible cracking on load-bearing walls.",
                "No sound detected, dog signaled possible survivor near stairwell.",
                "Partial pancake collapse, gas leak reported nearby.",
                "Family confirms 3 occupants inside at time of quake.",
            ]),
            "golden_hour_deadline": (reported_at + timedelta(hours=72)).isoformat(),
            "status": random.choices(
                ["reported", "triaged", "dispatched", "in_progress", "resolved"],
                weights=[30, 25, 20, 15, 10], k=1
            )[0],
            "people_saved": 0,
            "bodies_found": 0,
            "synced_from_offline": random.random() < 0.15,
            "submitted_by": f"scout_{random.randint(1, 40):03d}",
        })
    return pd.DataFrame(rows)


def generate_rescue_teams(n=120):
    rows = []
    for i in range(n):
        team_type = random.choices(TEAM_TYPES, weights=[2, 3, 5], k=1)[0]
        org_type = random.choices(ORG_TYPES, weights=[60, 40], k=1)[0]
        anchor = weighted_township()
        equipment = [
            {"item_name": item, "quantity": qty, "condition": random.choices(
                ["operational", "damaged", "missing"], weights=[85, 10, 5], k=1)[0]}
            for item, qty in EQUIPMENT_CATALOG[team_type]
        ]
        rows.append({
            "team_id": f"team_{i:04d}",
            "team_type": team_type,
            "org_type": org_type,
            "lat": round(jitter(anchor["lat"], 0.1), 6),
            "lon": round(jitter(anchor["lon"], 0.1), 6),
            "home_township": anchor["name"],
            "status": random.choices(STATUS_CHOICES, weights=[45, 20, 25, 10], k=1)[0],
            "capabilities": json.dumps(CAPABILITIES_CATALOG[team_type]),
            "equipment": json.dumps(equipment),  # load as ARRAY<STRUCT> in BigQuery
        })
    return pd.DataFrame(rows)


def generate_scouts(n=40):
    rows = []
    for i in range(n):
        anchor = weighted_township()
        rows.append({
            "scout_id": f"scout_{i+1:03d}",
            "lat": round(jitter(anchor["lat"], 0.08), 6),
            "lon": round(jitter(anchor["lon"], 0.08), 6),
            "home_township": anchor["name"],
            "status": random.choices(STATUS_CHOICES, weights=[35, 25, 30, 10], k=1)[0],
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Connected road network: a local grid per Sagaing township + a backbone linking every
# Sagaing township to each other AND to the 3 national hub cities.
# ---------------------------------------------------------------------------
def generate_road_network(grid_size=4, spacing_deg=0.014):
    nodes = []
    edges = []
    node_counter = [0]
    road_counter = [0]

    def add_node(lat, lon, township):
        nid = f"n{node_counter[0]:05d}"
        node_counter[0] += 1
        nodes.append({"node_id": nid, "lat": round(lat, 6), "lon": round(lon, 6), "township": township})
        return nid

    def add_edge(n1, n2, road_type, node_lookup):
        rid = f"road_{road_counter[0]:04d}"
        road_counter[0] += 1
        p1, p2 = node_lookup[n1], node_lookup[n2]
        surface_weights = {"primary": [95, 5], "secondary": [65, 35], "track": [10, 90]}[road_type]
        surface = random.choices(["paved", "unpaved"], weights=surface_weights, k=1)[0]
        edges.append({
            "road_id": rid, "from_node": n1, "to_node": n2,
            "lat1": p1["lat"], "lon1": p1["lon"], "lat2": p2["lat"], "lon2": p2["lon"],
            "road_type": road_type, "surface": surface,
            "distance_km": round(haversine_km(p1["lat"], p1["lon"], p2["lat"], p2["lon"]), 3),
        })

    all_towns = SAGAING_TOWNSHIPS + NATIONAL_HUBS
    town_grid = {}
    town_center = {}
    for anchor in all_towns:
        gsize = grid_size if anchor in SAGAING_TOWNSHIPS else 2  # hubs get a minimal grid, they're context only
        grid = [[None] * gsize for _ in range(gsize)]
        offset = (gsize - 1) * spacing_deg / 2
        for r in range(gsize):
            for c in range(gsize):
                lat = anchor["lat"] - offset + r * spacing_deg + random.uniform(-0.003, 0.003)
                lon = anchor["lon"] - offset + c * spacing_deg + random.uniform(-0.003, 0.003)
                grid[r][c] = add_node(lat, lon, anchor["name"])
        town_grid[anchor["name"]] = grid
        town_center[anchor["name"]] = grid[gsize // 2][gsize // 2]

    node_lookup = {n["node_id"]: n for n in nodes}

    for anchor in SAGAING_TOWNSHIPS:  # only build local street grids inside Sagaing townships
        grid = town_grid[anchor["name"]]
        gsize = len(grid)
        for r in range(gsize):
            for c in range(gsize):
                if c + 1 < gsize:
                    add_edge(grid[r][c], grid[r][c + 1], "secondary", node_lookup)
                if r + 1 < gsize:
                    add_edge(grid[r][c], grid[r + 1][c], "secondary", node_lookup)
        for _ in range(3):
            r1, c1, r2, c2 = (random.randrange(gsize), random.randrange(gsize),
                               random.randrange(gsize), random.randrange(gsize))
            if grid[r1][c1] != grid[r2][c2]:
                add_edge(grid[r1][c1], grid[r2][c2], "track", node_lookup)

    # backbone: chain Sagaing townships together, then link the chain to the 3 national hubs
    sagaing_names = [a["name"] for a in SAGAING_TOWNSHIPS]
    for i in range(len(sagaing_names) - 1):
        add_edge(town_center[sagaing_names[i]], town_center[sagaing_names[i + 1]], "primary", node_lookup)
    hub_entry = town_center["Sagaing"]  # Sagaing city is the gateway to the national network
    for hub in NATIONAL_HUBS:
        add_edge(hub_entry, town_center[hub["name"]], "primary", node_lookup)

    return pd.DataFrame(nodes), pd.DataFrame(edges), town_center


def generate_bases(town_center_nodes, nodes_df):
    node_lookup = nodes_df.set_index("node_id")
    rows = []
    base_id = 0
    for anchor in SAGAING_TOWNSHIPS:
        center_node = town_center_nodes[anchor["name"]]
        base_lat, base_lon = node_lookup.loc[center_node, ["lat", "lon"]]
        rows.append({
            "base_id": f"base_{base_id:03d}", "name": f"{anchor['name']} National USAR HQ",
            "org_type": "national_rescue_dept", "township": anchor["name"],
            "lat": round(jitter(base_lat, 0.01), 6), "lon": round(jitter(base_lon, 0.01), 6),
        })
        base_id += 1
        ngo_name = random.choice(NGO_NAMES)
        rows.append({
            "base_id": f"base_{base_id:03d}", "name": f"{ngo_name} - {anchor['name']} Hub",
            "org_type": "ngo", "township": anchor["name"],
            "lat": round(jitter(base_lat, 0.015), 6), "lon": round(jitter(base_lon, 0.015), 6),
        })
        base_id += 1
    # national coordination hubs - context only, no incidents/teams originate here
    for hub in NATIONAL_HUBS:
        center_node = town_center_nodes[hub["name"]]
        base_lat, base_lon = node_lookup.loc[center_node, ["lat", "lon"]]
        rows.append({
            "base_id": f"base_{base_id:03d}", "name": f"{hub['name']} National Coordination Center",
            "org_type": "national_rescue_dept", "township": hub["name"],
            "lat": round(base_lat, 6), "lon": round(base_lon, 6),
        })
        base_id += 1
    return pd.DataFrame(rows)


def generate_road_status(edges_df, n_blocked=15):
    """Seed a handful of blocked/damaged roads so the routing demo has something to route
    around immediately. Scouts add more of these live from the Streamlit app."""
    sample = edges_df.sample(n=min(n_blocked, len(edges_df)), random_state=7)
    rows = []
    for i, (_, road) in enumerate(sample.iterrows()):
        rows.append({
            "report_id": f"rs_{i:03d}",
            "road_id": road["road_id"],
            "status": random.choices(["blocked", "damaged"], weights=[70, 30], k=1)[0],
            "blockage_type": random.choice(["debris", "bridge_collapse", "flooding", "landslide"]),
            "reported_at": (datetime.utcnow() - timedelta(hours=random.uniform(0, 24))).isoformat(),
            "reported_by": f"scout_{random.randint(1, 40):03d}",
            "notes": "Seed data for demo - reroute required.",
        })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    incidents = generate_incidents()
    teams = generate_rescue_teams()
    scouts = generate_scouts()
    nodes_df, roads_df, town_center_nodes = generate_road_network()
    bases = generate_bases(town_center_nodes, nodes_df)
    road_status = generate_road_status(roads_df)

    incidents.to_csv("incidents.csv", index=False)
    teams.to_csv("rescue_teams.csv", index=False)
    scouts.to_csv("scouts.csv", index=False)
    nodes_df.to_csv("road_nodes.csv", index=False)
    roads_df.to_csv("roads.csv", index=False)
    bases.to_csv("bases.csv", index=False)
    road_status.to_csv("road_status.csv", index=False)

    print(f"incidents:     {len(incidents):,} rows -> incidents.csv (Sagaing region only)")
    print(f"rescue_teams:  {len(teams):,} rows -> rescue_teams.csv (gov + NGO)")
    print(f"scouts:        {len(scouts):,} rows -> scouts.csv")
    print(f"road_nodes:    {len(nodes_df):,} rows -> road_nodes.csv")
    print(f"roads:         {len(roads_df):,} rows -> roads.csv (Sagaing grid + national backbone)")
    print(f"bases:         {len(bases):,} rows -> bases.csv (Sagaing HQs/NGOs + 3 national hubs)")
    print(f"road_status:   {len(road_status):,} rows -> road_status.csv (seed blockages)")
