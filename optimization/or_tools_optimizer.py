"""
OR-Tools CP-SAT optimizer: assigns rescue teams to priority incidents, minimizing transit time
while respecting the Golden Hour deadline and team-type suitability.

Runs standalone against the synthetic CSVs in data/ so it can be built and demoed before
BigQuery is wired up. Swap load_incidents()/load_teams() for BigQuery reads later - the
optimize() function itself doesn't change.
"""

import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from ortools.sat.python import cp_model

sys.path.append(str(Path(__file__).resolve().parent.parent))
from optimization.routing_graph import load_road_network, travel_time_matrix  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
AVG_SPEED_KMH = 35  # fallback only, used if the road graph can't be loaded
EARTH_RADIUS_KM = 6371.0

# Only these team types are considered suitable for each collapse pattern (hackathon heuristic -
# Gemini's recommended_team_type from priority scoring can override this per incident).
SUITABLE_TEAMS = {
    "pancake": {"Heavy", "Medium"},
    "v_shape": {"Heavy", "Medium"},
    "lean_to": {"Medium", "Light"},
    "cantilever": {"Heavy"},
    "partial_collapse": {"Medium", "Light"},
}


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def travel_minutes(lat1, lon1, lat2, lon2):
    return (haversine_km(lat1, lon1, lat2, lon2) / AVG_SPEED_KMH) * 60


def load_incidents(csv_path=None, top_n=60):
    csv_path = csv_path or DATA_DIR / "incidents.csv"
    df = pd.read_csv(csv_path, parse_dates=["reported_at", "golden_hour_deadline"])
    df = df[df["status"].isin(["reported", "triaged"])].copy()
    if "priority_score" not in df.columns or df["priority_score"].isna().all():
        # No Gemini scores yet - use trapped_count as a cheap proxy so the optimizer is runnable today
        df["priority_score"] = (df["trapped_count"].clip(upper=20) / 20).round(2)
    return df.sort_values("priority_score", ascending=False).head(top_n).reset_index(drop=True)


def load_teams(csv_path=None):
    csv_path = csv_path or DATA_DIR / "rescue_teams.csv"
    df = pd.read_csv(csv_path)
    return df[df["status"] == "available"].reset_index(drop=True)


def optimize(incidents: pd.DataFrame, teams: pd.DataFrame, now: datetime = None,
             extra_blocked_road_ids=None, road_graph=None):
    """Maximize covered priority weight while respecting deadlines and team suitability.

    Travel time comes from the real road-network graph (optimization/routing_graph.py), which
    honors scout-reported blockages - not straight-line distance. Pass extra_blocked_road_ids
    (e.g. from the current Streamlit session) to route around blockages reported live, without
    needing to reload the CSVs. Pass a pre-built road_graph=(G, nodes_df) to skip reloading it
    for repeated calls (the Streamlit app does this).

    Returns a DataFrame of assignments: incident_id, team_id, travel_minutes, priority_score.
    """
    now = now or datetime.now(timezone.utc)
    model = cp_model.CpModel()

    n_inc, n_team = len(incidents), len(teams)

    if road_graph is not None:
        G, nodes_df = road_graph
    else:
        try:
            G, nodes_df = load_road_network(extra_blocked_road_ids=extra_blocked_road_ids)
        except FileNotFoundError:
            G, nodes_df = None, None  # falls back to haversine below

    if G is not None:
        # teams = origins, incidents = destinations; one Dijkstra per team instead of per pair
        matrix = travel_time_matrix(G, nodes_df, teams, incidents)  # shape (n_team, n_inc)
    else:
        matrix = np.array([
            [travel_minutes(incidents.iloc[i]["lat"], incidents.iloc[i]["lon"],
                             teams.iloc[j]["lat"], teams.iloc[j]["lon"])
             for i in range(n_inc)] for j in range(n_team)
        ])

    x = {}
    travel = {}
    feasible_pairs = []

    for i in range(n_inc):
        inc = incidents.iloc[i]
        deadline = inc["golden_hour_deadline"]
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        minutes_remaining = (deadline - now).total_seconds() / 60
        collapse_pattern = inc.get("collapse_pattern", "")
        allowed_types = SUITABLE_TEAMS.get(collapse_pattern, {"Heavy", "Medium", "Light"})

        for j in range(n_team):
            team = teams.iloc[j]
            if team["team_type"] not in allowed_types:
                continue
            t = matrix[j, i]
            if not np.isfinite(t) or t > max(minutes_remaining, 0):
                continue  # unreachable (blocked roads) or can't make it before Golden Hour expires
            travel[i, j] = t
            x[i, j] = model.NewBoolVar(f"x_{i}_{j}")
            feasible_pairs.append((i, j))

    # Each incident gets at most one team; each team dispatched to at most one incident
    for i in range(n_inc):
        model.Add(sum(x[i, j] for j in range(n_team) if (i, j) in x) <= 1)
    for j in range(n_team):
        model.Add(sum(x[i, j] for i in range(n_inc) if (i, j) in x) <= 1)

    # Objective: maximize priority coverage, tie-break on minimizing travel time
    # (priority scaled up so it dominates; travel_minutes penalty keeps assignments efficient)
    objective_terms = []
    for (i, j) in feasible_pairs:
        priority = incidents.iloc[i]["priority_score"]
        score = int(round(priority * 10000 - travel[i, j]))
        objective_terms.append(score * x[i, j])
    model.Maximize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 15
    status = solver.Solve(model)

    assignments = []
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for (i, j) in feasible_pairs:
            if solver.Value(x[i, j]):
                assignments.append({
                    "incident_id": incidents.iloc[i]["incident_id"],
                    "team_id": teams.iloc[j]["team_id"],
                    "team_type": teams.iloc[j]["team_type"],
                    "priority_score": incidents.iloc[i]["priority_score"],
                    "travel_minutes": round(travel[i, j], 1),
                    "objective_value": solver.ObjectiveValue(),
                })
    return pd.DataFrame(assignments), status


if __name__ == "__main__":
    incidents = load_incidents()
    teams = load_teams()
    print(f"Optimizing over {len(incidents)} priority incidents and {len(teams)} available teams...")

    assignments, status = optimize(incidents, teams)
    print(f"Solver status: {cp_model.CpSolver().StatusName(status)}")
    print(f"Assigned {len(assignments)} of {len(incidents)} incidents")
    if not assignments.empty:
        print(assignments.sort_values("priority_score", ascending=False).to_string(index=False))
