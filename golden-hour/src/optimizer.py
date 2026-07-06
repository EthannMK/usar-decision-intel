"""OR-Tools deployment optimizer.

Assignment model (CP-SAT): match available USAR teams to the highest-value
collapse sites, respecting capability constraints and the shrinking
golden-hour window. Objective = maximize expected survivors reached in time.

Expected-survivor value of an assignment decays with arrival time:
survival probability roughly halves every 24h after the event (literature-
informed simplification for the prototype).
"""
import math
from datetime import datetime, timezone

import pandas as pd
from ortools.sat.python import cp_model

from src.config import ROAD_SPEEDS, TEAM_CAPABILITY, GOLDEN_HOUR_LIMIT_H


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def travel_time_h(team: dict, site: pd.Series) -> float:
    km = haversine_km(team["base_lat"], team["base_lon"], site["lat"], site["lon"])
    speed = ROAD_SPEEDS.get(site.get("road_type", "paved"), 30.0)
    return km / speed


def optimize_deployment(sites: pd.DataFrame, teams: list[dict],
                        event_hours_ago: float = 6.0,
                        max_sites_per_team: int = 1) -> dict:
    """Returns dict with 'assignments' (list) and 'stats'."""
    start = datetime.now(timezone.utc)
    active = sites[sites["status"] == "awaiting_rescue"].reset_index(drop=True)
    avail = [t for t in teams if t.get("status") == "available"]

    model = cp_model.CpModel()
    x, value, meta = {}, {}, {}

    for ti, team in enumerate(avail):
        cap = TEAM_CAPABILITY.get(team["team_type"], set())
        for si, site in active.iterrows():
            if site["collapse_pattern"] not in cap:
                continue
            tt = travel_time_h(team, site)
            arrival_h = event_hours_ago + tt          # hours after quake on arrival
            if arrival_h >= GOLDEN_HOUR_LIMIT_H:
                continue                               # arrives too late to matter
            # survival decay: halves every 24h post-event
            survival = 0.5 ** (arrival_h / 24.0)
            expected = site["priority_score"] * site["est_trapped"] * survival
            v = model.NewBoolVar(f"x_{ti}_{si}")
            x[(ti, si)] = v
            value[(ti, si)] = int(expected * 1000)
            meta[(ti, si)] = {"travel_h": tt, "arrival_h": arrival_h,
                              "expected_survivors": expected}

    for ti in range(len(avail)):
        model.Add(sum(x[k] for k in x if k[0] == ti) <= max_sites_per_team)
    for si in range(len(active)):
        model.Add(sum(x[k] for k in x if k[1] == si) <= 1)

    model.Maximize(sum(value[k] * x[k] for k in x))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0
    status = solver.Solve(model)

    assignments = []
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for (ti, si), var in x.items():
            if solver.Value(var):
                team, site = avail[ti], active.iloc[si]
                m = meta[(ti, si)]
                assignments.append({
                    "team_id": team["team_id"], "team_name": team["team_name"],
                    "team_type": team["team_type"],
                    "team_lat": team["base_lat"], "team_lon": team["base_lon"],
                    "site_id": site["site_id"], "site_name": site["site_name"],
                    "site_lat": site["lat"], "site_lon": site["lon"],
                    "collapse_pattern": site["collapse_pattern"],
                    "priority_score": float(site["priority_score"]),
                    "est_trapped": int(site["est_trapped"]),
                    "road_type": site["road_type"],
                    "travel_time_h": round(m["travel_h"], 2),
                    "eta_hours_after_event": round(m["arrival_h"], 1),
                    "expected_survivors": round(m["expected_survivors"], 1),
                })
    assignments.sort(key=lambda a: -a["expected_survivors"])
    solve_s = (datetime.now(timezone.utc) - start).total_seconds()
    return {
        "assignments": assignments,
        "stats": {
            "solver_status": solver.StatusName(status),
            "solve_seconds": round(solve_s, 2),
            "teams_deployed": len(assignments),
            "sites_covered": len(assignments),
            "sites_waiting": int(len(active) - len(assignments)),
            "total_expected_survivors": round(sum(a["expected_survivors"] for a in assignments), 1),
        },
    }
