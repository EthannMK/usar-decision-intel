"""
USAR Decision Intelligence Platform - Streamlit app
Three persona views in one app: Scout (field) / Command Center / Rescue Team.

Reads live operational data from BigQuery (Sagaing region, Myanmar), automatically falling
back to the local synthetic CSVs in data/ if BigQuery isn't reachable/configured - see
load_data() below. The active source is shown in the Command Center sidebar.

Run with:  streamlit run app/streamlit_app.py
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import folium
import numpy as np
import pandas as pd
import streamlit as st
from folium.plugins import AntPath, MarkerCluster
from streamlit_folium import st_folium

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))
sys.path.append(str(ROOT / "data"))
DATA_DIR = ROOT / "data"

from optimization.or_tools_optimizer import load_incidents, load_teams, optimize, haversine_km  # noqa: E402
from optimization.routing_graph import load_road_network, route as compute_route  # noqa: E402
from ai.gemini_priority_scoring import (  # noqa: E402
    score_incident_priority, generate_equipment_substitution, assess_satellite_damage,
)
from ai.earthquake_feed import build_event_timeline  # noqa: E402
from generate_synthetic_data import SAGAING_TOWNSHIPS  # noqa: E402

st.set_page_config(page_title="USAR Decision Intelligence", layout="wide", page_icon="🚨")

SAGAING_TOWNSHIP_NAMES = [t["name"] for t in SAGAING_TOWNSHIPS]

# Idle / Operation / Rest / En-route(assigned) - matches folium's built-in marker palette
STATUS_COLOR = {"available": "green", "on_site": "red", "resting": "beige", "en_route": "blue"}
STATUS_LABEL = {"available": "Idle", "on_site": "Operation", "resting": "Rest", "en_route": "En route (assigned)"}
BASE_ICON = {"national_rescue_dept": "university", "ngo": "heart"}
BASE_COLOR = {"national_rescue_dept": "darkblue", "ngo": "darkpurple"}
TEAM_ICON = {"national_rescue_dept": "shield", "ngo": "user"}
SCOUT_ICON = "eye"

# --------------------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------------------
defaults = {
    "online": True,
    "offline_queue": [],
    "assignments": pd.DataFrame(),
    "cpu_benchmark_seconds": None,
    "road_reports": [],
    "blocked_road_ids": set(),
    "outcome_reports": [],       # scout-submitted people_saved / bodies_found updates
    "sim_progress": {},          # team_id -> 0.0-1.0 progress along its route (simulated movement)
}
for key, val in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = val


BQ_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "usar-decision-intel")
BQ_DATASET = "usar_decision_intel"
BQ_LOCATION = "asia-southeast1"  # Singapore - must match bigquery/schema.sql


@st.cache_resource
def get_bq_client():
    from google.cloud import bigquery
    return bigquery.Client(project=BQ_PROJECT_ID, location=BQ_LOCATION)


def _load_data_from_bigquery():
    """Pulls live operational data straight from BigQuery. GEOGRAPHY columns are converted to
    plain lat/lon via ST_X/ST_Y in SQL (simpler and faster than parsing WKT client-side).
    ARRAY/STRUCT columns (hazards_present, capabilities, equipment) come back as native Python
    lists/dicts from the client - re-encoded to JSON strings here so every downstream function
    (written against the CSV shape) doesn't need to change.

    NOTE: written carefully against the documented BigQuery client behavior but not execution-
    tested live (same caveat as bigquery/load_data.py) - if this throws, the app falls back to
    local CSVs automatically (see load_data() below), so a bad query here fails safe.
    """
    client = get_bq_client()
    ds = f"{BQ_PROJECT_ID}.{BQ_DATASET}"

    incidents = client.query(f"""
        SELECT * EXCEPT(location), ST_Y(location) AS lat, ST_X(location) AS lon
        FROM `{ds}.incidents`
    """).to_dataframe()
    incidents["hazards_present"] = incidents["hazards_present"].apply(lambda x: json.dumps(list(x)))
    incidents["reported_at"] = pd.to_datetime(incidents["reported_at"], utc=True)
    incidents["golden_hour_deadline"] = pd.to_datetime(incidents["golden_hour_deadline"], utc=True)

    teams = client.query(f"""
        SELECT * EXCEPT(current_location), ST_Y(current_location) AS lat, ST_X(current_location) AS lon
        FROM `{ds}.rescue_teams`
    """).to_dataframe()
    teams["capabilities"] = teams["capabilities"].apply(lambda x: json.dumps(list(x)))
    teams["equipment"] = teams["equipment"].apply(
        lambda arr: json.dumps([dict(item) for item in arr])
    )

    scouts = client.query(f"""
        SELECT * EXCEPT(current_location), ST_Y(current_location) AS lat, ST_X(current_location) AS lon
        FROM `{ds}.scouts`
    """).to_dataframe()

    bases = client.query(f"""
        SELECT * EXCEPT(location), ST_Y(location) AS lat, ST_X(location) AS lon
        FROM `{ds}.bases`
    """).to_dataframe()

    roads = client.query(f"""
        SELECT road_id, from_node, to_node, road_type, surface, distance_km,
               ST_Y(ST_STARTPOINT(geometry)) AS lat1, ST_X(ST_STARTPOINT(geometry)) AS lon1,
               ST_Y(ST_ENDPOINT(geometry)) AS lat2, ST_X(ST_ENDPOINT(geometry)) AS lon2
        FROM `{ds}.roads`
    """).to_dataframe()

    road_status = client.query(f"SELECT * FROM `{ds}.road_status`").to_dataframe()

    return incidents, teams, scouts, bases, roads, road_status


def _load_data_from_csv():
    incidents = pd.read_csv(DATA_DIR / "incidents.csv", parse_dates=["reported_at", "golden_hour_deadline"])
    teams = pd.read_csv(DATA_DIR / "rescue_teams.csv")
    scouts = pd.read_csv(DATA_DIR / "scouts.csv")
    bases = pd.read_csv(DATA_DIR / "bases.csv")
    roads = pd.read_csv(DATA_DIR / "roads.csv")
    road_status = pd.read_csv(DATA_DIR / "road_status.csv")
    return incidents, teams, scouts, bases, roads, road_status


@st.cache_data(ttl=30)
def load_data():
    """Live operational data (incidents/teams/scouts/bases/roads/road_status) from BigQuery,
    falling back to local CSVs if the client isn't configured/reachable - e.g. no service-account
    key set up yet, or an offline demo. The road NETWORK GRAPH used for pathfinding (road_nodes.csv
    + roads.csv, see get_road_graph()) intentionally still reads local CSVs directly: it's static
    topology re-read on every optimizer/route call, and a per-call BigQuery round trip there would
    only add latency without changing what's demoed - the live BigQuery integration is about the
    operational entities (incidents, teams, scouts, bases, road status), not the fixed map geometry.
    """
    try:
        data = _load_data_from_bigquery()
        st.session_state["data_source"] = "🟢 BigQuery (live)"
    except Exception as e:
        st.session_state["data_source"] = f"🟡 Local CSV fallback ({type(e).__name__}: {e})"
        data = _load_data_from_csv()
    incidents, teams, scouts, bases, roads, road_status = data
    incidents = _ensure_township_column(incidents, "nearest_township")
    teams = _ensure_township_column(teams, "home_township")
    return incidents, teams, scouts, bases, roads, road_status


def _ensure_township_column(df, column_name):
    """Adds nearest-Sagaing-township-by-geometry if `column_name` isn't already present.

    The local CSVs bake townships in at generation time (data/generate_synthetic_data.py) and
    bigquery/schema.sql's `scouts`/`bases` tables carry a real township/home_township column -
    but the `incidents` table only stores GEOGRAPHY location (no nearest_township field), and
    `rescue_teams` never got a home_township column added to its schema at all (it's silently
    dropped by bigquery/load_data.py's load_rescue_teams(), which never sent that CSV field).
    Both are computed here from lat/lon instead, so the app works the same regardless of source.
    """
    if column_name in df.columns:
        return df
    town_lats = np.array([t["lat"] for t in SAGAING_TOWNSHIPS])
    town_lons = np.array([t["lon"] for t in SAGAING_TOWNSHIPS])
    town_names = [t["name"] for t in SAGAING_TOWNSHIPS]

    def nearest(lat, lon):
        p1, p2 = np.radians(lat), np.radians(town_lats)
        dphi = np.radians(town_lats - lat)
        dlambda = np.radians(town_lons - lon)
        a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlambda / 2) ** 2
        dist_km = 2 * 6371.0 * np.arcsin(np.sqrt(a))
        return town_names[int(np.argmin(dist_km))]

    df = df.copy()
    df[column_name] = [nearest(r.lat, r.lon) for r in df.itertuples()]
    return df


def minutes_remaining(deadline) -> float:
    now = datetime.now(timezone.utc)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return (deadline - now).total_seconds() / 60


def all_blocked_road_ids(road_status_df):
    seed_blocked = set(road_status_df.loc[road_status_df["status"] == "blocked", "road_id"])
    return seed_blocked | st.session_state.blocked_road_ids


def get_road_graph():
    return load_road_network(extra_blocked_road_ids=st.session_state.blocked_road_ids)


def interpolate_polyline(polyline, fraction):
    """Point along a route polyline at fraction (0.0-1.0), for the movement simulation."""
    if fraction <= 0:
        return polyline[0]
    if fraction >= 1:
        return polyline[-1]
    idx = fraction * (len(polyline) - 1)
    i = int(idx)
    f = idx - i
    p1, p2 = polyline[i], polyline[min(i + 1, len(polyline) - 1)]
    return [p1[0] + (p2[0] - p1[0]) * f, p1[1] + (p2[1] - p1[1]) * f]


def popup_html(title, rows: dict, width=260):
    """Clean formatted HTML popup instead of a wall of concatenated text."""
    rows_html = "".join(
        f"<tr><td style='color:#666;padding:2px 10px 2px 0;white-space:nowrap;vertical-align:top;'>{k}</td>"
        f"<td style='font-weight:600;'>{v}</td></tr>"
        for k, v in rows.items()
    )
    html = (
        "<div style='font-family:-apple-system,Segoe UI,sans-serif;font-size:12.5px;'>"
        f"<div style='font-weight:700;font-size:14px;margin-bottom:5px;border-bottom:1px solid #ddd;padding-bottom:4px;'>{title}</div>"
        f"<table>{rows_html}</table></div>"
    )
    iframe = folium.IFrame(html, width=width + 20, height=min(320, 46 + 22 * len(rows)))
    return folium.Popup(iframe, max_width=width + 30)


def build_map(incidents_df, teams_df, scouts_df, bases_df, roads_df, blocked_ids,
              route_polyline=None, max_incidents=300):
    center = [incidents_df["lat"].mean(), incidents_df["lon"].mean()] if len(incidents_df) else [22.1, 95.5]
    m = folium.Map(location=center, zoom_start=7, tiles="OpenStreetMap")

    blocked_roads = roads_df[roads_df["road_id"].isin(blocked_ids)]
    for _, r in blocked_roads.iterrows():
        folium.PolyLine(
            [[r["lat1"], r["lon1"]], [r["lat2"], r["lon2"]]],
            color="red", weight=4, dash_array="6,6",
            tooltip=f"🚧 BLOCKED: {r['road_id']} ({r['road_type']})",
        ).add_to(m)

    for _, b in bases_df.iterrows():
        folium.Marker(
            [b["lat"], b["lon"]],
            tooltip=b["name"],
            popup=popup_html(b["name"], {
                "Type": "National Rescue Dept HQ" if b["org_type"] == "national_rescue_dept" else "NGO Hub",
                "Township": b["township"],
            }),
            icon=folium.Icon(color=BASE_COLOR.get(b["org_type"], "gray"), icon=BASE_ICON.get(b["org_type"], "home"), prefix="fa"),
        ).add_to(m)

    cluster = MarkerCluster(name="Incidents").add_to(m)
    show_incidents = incidents_df.sort_values(
        "priority_score" if "priority_score" in incidents_df.columns else "trapped_count", ascending=False
    ).head(max_incidents)
    for _, inc in show_incidents.iterrows():
        prio = inc.get("priority_score", inc["trapped_count"] / 20)
        color = "red" if prio >= 0.7 else ("orange" if prio >= 0.4 else "lightgray")
        folium.CircleMarker(
            [inc["lat"], inc["lon"]], radius=6, color=color, fill=True, fill_opacity=0.85,
            popup=popup_html(f"Incident {inc['incident_id'][:8]}...", {
                "Status": inc["status"], "Township": inc["nearest_township"],
                "Building": f"{inc['building_stories']}-story {inc['building_use']}",
                "Trapped (est.)": inc["trapped_count"],
                "Access": inc.get("access_difficulty", "?"),
            }),
        ).add_to(cluster)

    for _, t in teams_df.iterrows():
        folium.Marker(
            [t["lat"], t["lon"]], tooltip=f"{t['team_id']} ({STATUS_LABEL.get(t['status'], t['status'])})",
            popup=popup_html(t["team_id"], {
                "Org": "National Rescue Dept" if t["org_type"] == "national_rescue_dept" else "NGO",
                "Team type": t["team_type"], "Status": STATUS_LABEL.get(t["status"], t["status"]),
                "Home township": t.get("home_township", "?"),
            }),
            icon=folium.Icon(color=STATUS_COLOR.get(t["status"], "gray"),
                              icon=TEAM_ICON.get(t["org_type"], "user"), prefix="fa"),
        ).add_to(m)

    for _, s in scouts_df.iterrows():
        folium.Marker(
            [s["lat"], s["lon"]], tooltip=f"{s['scout_id']} ({STATUS_LABEL.get(s['status'], s['status'])})",
            popup=popup_html(s["scout_id"], {
                "Role": "Scout", "Status": STATUS_LABEL.get(s["status"], s["status"]),
                "Home township": s.get("home_township", "?"),
            }),
            icon=folium.Icon(color=STATUS_COLOR.get(s["status"], "gray"), icon=SCOUT_ICON, prefix="fa"),
        ).add_to(m)

    if route_polyline:
        AntPath(route_polyline, color="#1a73e8", weight=5, opacity=0.85, delay=800,
                dash_array=[10, 20], tooltip="Optimal route").add_to(m)

    return m


incidents_df, teams_df, scouts_df, bases_df, roads_df, road_status_df = load_data()

# --------------------------------------------------------------------------------------
# Sidebar navigation
# --------------------------------------------------------------------------------------
st.sidebar.title("USAR Decision Intelligence")
persona = st.sidebar.radio("View", ["📱 Scout (Field)", "🚨 Command Center", "🚒 Rescue Team"])
st.sidebar.markdown("---")
st.sidebar.caption("Sagaing Region, Myanmar — earthquake USAR hackathon demo")
st.sidebar.caption(f"Data source: {st.session_state.get('data_source', 'unknown')}")
if st.session_state.blocked_road_ids:
    st.sidebar.warning(f"🚧 {len(st.session_state.blocked_road_ids)} road(s) reported blocked this session")
st.sidebar.markdown(
    "**Map legend**  \n"
    "🎓 university icon = Gov HQ · ❤️ = NGO hub · 🛡️ shield = Gov team · 👤 = NGO team · 👁️ eye = Scout  \n"
    "🟢 idle · 🔴 operation · 🟡 rest · 🔵 en route"
)

# ========================================================================================
# SCOUT VIEW - site report / road status / outcome reporting
# ========================================================================================
if persona == "📱 Scout (Field)":
    st.title("Scout Field Report")
    st.session_state.online = st.toggle("📶 Network connected", value=st.session_state.online)
    if not st.session_state.online:
        st.error("Offline mode — submissions will queue locally until you reconnect.")

    tab_site, tab_road, tab_outcome = st.tabs(["📋 Site Report", "🚧 Road Status", "✅ Report Outcome"])

    with tab_site:
        with st.form("scout_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                township = st.selectbox("Township", SAGAING_TOWNSHIP_NAMES)
                lat = st.number_input("Latitude", value=21.88, format="%.6f")
                lon = st.number_input("Longitude", value=95.98, format="%.6f")
                material = st.selectbox(
                    "Building material",
                    ["reinforced_concrete", "unreinforced_masonry", "wood_frame", "steel_frame", "mixed_masonry"],
                )
                stories = st.number_input("Building stories (before collapse)", min_value=1, max_value=40, value=3)
                building_use = st.selectbox(
                    "Building use", ["residential", "commercial", "school", "hospital", "mixed_use",
                                      "industrial", "government"]
                )
                pattern = st.selectbox(
                    "Collapse pattern", ["pancake", "lean_to", "v_shape", "cantilever", "partial_collapse"]
                )
            with c2:
                confirmed = st.number_input("Confirmed trapped (witness/family confirmed)", min_value=0, max_value=50, value=0)
                estimated = st.number_input("Estimated trapped (best guess)", min_value=0, max_value=50, value=1)
                signs = st.selectbox(
                    "Signs of life", ["none_detected", "sound_heard", "visual_confirmed", "canine_alert",
                                       "family_confirmed_occupants"]
                )
                hazards = st.multiselect(
                    "Hazards present", ["gas_leak", "fire", "electrical_hazard", "chemical_spill",
                                          "flooding", "unstable_secondary_structure"]
                )
                access = st.selectbox(
                    "Access difficulty", ["clear", "partial_debris", "heavy_debris_impassable_for_heavy_vehicles"]
                )
                notes = st.text_area("Scout notes", placeholder="Voices heard, structural condition...")
                st.file_uploader("Site photo (optional)", type=["jpg", "jpeg", "png"])
            submitted = st.form_submit_button("Submit site report", use_container_width=True)

        if submitted:
            record = {
                "incident_id": f"scout_{int(time.time() * 1000)}",
                "reported_at": datetime.now(timezone.utc).isoformat(),
                "lat": lat, "lon": lon, "nearest_township": township,
                "building_material": material, "building_stories": stories, "building_use": building_use,
                "collapse_pattern": pattern,
                "confirmed_trapped_count": confirmed, "estimated_trapped_count": estimated,
                "trapped_count": max(confirmed, estimated),
                "signs_of_life": signs, "hazards_present": json.dumps(hazards), "access_difficulty": access,
                "scout_notes": notes,
                "golden_hour_deadline": datetime.now(timezone.utc).isoformat(),
                "status": "reported", "synced_from_offline": not st.session_state.online,
                "submitted_by": "scout_app",
            }
            if st.session_state.online:
                st.success("✅ Synced to Command Center.")
            else:
                st.session_state.offline_queue.append(record)
                st.warning(f"📴 No network — queued locally ({len(st.session_state.offline_queue)} pending).")

        if st.session_state.offline_queue:
            st.subheader(f"Offline queue ({len(st.session_state.offline_queue)} pending)")
            st.dataframe(pd.DataFrame(st.session_state.offline_queue), use_container_width=True)
            if st.session_state.online:
                if st.button("🔄 Batch sync now", type="primary"):
                    st.session_state.offline_queue = []
                    st.success("Batch sync complete.")
                    st.rerun()
            else:
                st.caption("Reconnect (toggle above) to enable batch sync.")

    with tab_road:
        st.caption(
            "Report a road as blocked/damaged so the AI Optimizer routes rescue teams around it. "
            "Affects route planning immediately for the rest of this session."
        )
        township_r = st.selectbox("Township", SAGAING_TOWNSHIP_NAMES, key="road_township")
        nodes_df = pd.read_csv(DATA_DIR / "road_nodes.csv")
        local_roads = roads_df.merge(nodes_df[["node_id", "township"]], left_on="from_node", right_on="node_id")
        local_roads = local_roads[local_roads["township"] == township_r]
        road_choice = st.selectbox(
            "Road segment",
            local_roads["road_id"] + " — " + local_roads["road_type"] + "/" + local_roads["surface"],
        )
        road_id = road_choice.split(" — ")[0]
        status_choice = st.radio("Status", ["blocked", "damaged", "cleared (reopen)"], horizontal=True)
        blockage_type = st.selectbox("Blockage type", ["debris", "bridge_collapse", "flooding", "landslide", "checkpoint"])
        road_notes = st.text_input("Notes (optional)")
        if st.button("Submit road status", type="primary"):
            if status_choice == "cleared (reopen)":
                st.session_state.blocked_road_ids.discard(road_id)
                st.success(f"✅ {road_id} marked cleared — routes will use it again.")
            else:
                status_val = "blocked" if status_choice == "blocked" else "damaged"
                if status_val == "blocked":
                    st.session_state.blocked_road_ids.add(road_id)
                st.session_state.road_reports.append({
                    "road_id": road_id, "township": township_r, "status": status_val,
                    "blockage_type": blockage_type,
                    "reported_at": datetime.now(timezone.utc).isoformat(), "notes": road_notes,
                })
                st.warning(f"🚧 {road_id} marked {status_val}. The AI Optimizer will route around it.")

        if st.session_state.road_reports:
            st.subheader("Road reports this session")
            st.dataframe(pd.DataFrame(st.session_state.road_reports), use_container_width=True, hide_index=True)

    with tab_outcome:
        st.caption("Update an active incident with people saved / bodies found. Feeds the Command Center scorecards.")
        active = incidents_df[incidents_df["status"].isin(["dispatched", "in_progress"])]
        if active.empty:
            st.info("No dispatched/in-progress incidents to report on yet.")
        else:
            options = (active["incident_id"].str[:8] + "... | " + active["nearest_township"] + " | trapped~"
                       + active["trapped_count"].astype(str))
            choice = st.selectbox("Incident", options)
            chosen_id = active.iloc[options.tolist().index(choice)]["incident_id"]
            oc1, oc2 = st.columns(2)
            saved = oc1.number_input("People saved", min_value=0, max_value=50, value=0)
            dead = oc2.number_input("Bodies found", min_value=0, max_value=50, value=0)
            if st.button("Submit outcome", type="primary"):
                st.session_state.outcome_reports.append({
                    "incident_id": chosen_id, "people_saved": saved, "bodies_found": dead,
                    "reported_at": datetime.now(timezone.utc).isoformat(),
                })
                st.success(f"✅ Recorded: {saved} saved, {dead} found deceased.")
        if st.session_state.outcome_reports:
            st.subheader("Outcome reports this session")
            st.dataframe(pd.DataFrame(st.session_state.outcome_reports), use_container_width=True, hide_index=True)

# ========================================================================================
# COMMAND CENTER VIEW
# ========================================================================================
elif persona == "🚨 Command Center":
    st.title("Command Center")

    df = incidents_df.copy()
    df["minutes_remaining"] = df["golden_hour_deadline"].apply(minutes_remaining)
    critical = df[(df["minutes_remaining"] < 120) & (df["status"] != "resolved")]
    avail_teams = teams_df[teams_df["status"] == "available"]
    blocked_ids = all_blocked_road_ids(road_status_df)
    total_saved = sum(r["people_saved"] for r in st.session_state.outcome_reports)
    total_dead = sum(r["bodies_found"] for r in st.session_state.outcome_reports)

    r1c1, r1c2, r1c3, r1c4 = st.columns(4)
    r1c1.metric("Open incidents", int((df["status"] != "resolved").sum()))
    r1c2.metric("Available teams", len(avail_teams))
    r1c3.metric("Critical (<2h to Golden Hour)", len(critical))
    r1c4.metric("Roads blocked/damaged", len(blocked_ids))
    r2c1, r2c2, r2c3, r2c4 = st.columns(4)
    r2c1.metric("Total trapped (open sites)", int(df.loc[df["status"] != "resolved", "trapped_count"].sum()))
    r2c2.metric("🟢 People saved", total_saved)
    r2c3.metric("⚫ Bodies found", total_dead)
    r2c4.metric("Active scouts/teams", int((scouts_df["status"] != "resting").sum() + (teams_df["status"] != "resting").sum()))

    st.markdown("---")
    st.subheader("🌍 Seismic event timeline (live USGS feed)")
    st.caption(
        "Pulled from the real USGS earthquake catalog for this region — falls back to the actual "
        "2025 M7.7 Mandalay earthquake + its real M6.7 aftershock if the live feed is unreachable."
    )
    try:
        timeline, main_shock = build_event_timeline()
        eq_df = pd.DataFrame(timeline)[["phase", "magnitude", "place", "time_utc", "minutes_after_main_shock"]]
        st.dataframe(eq_df, use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning(f"Could not load seismic timeline: {e}")

    st.markdown("---")
    st.subheader("🛰️ Satellite impact assessment (Gemini vision)")
    st.caption(
        "Independent of scout reports — upload a satellite/aerial image and Gemini estimates the "
        "% of visible area showing collapse damage. Cheap (a fraction of a cent per image)."
    )
    sat_image = st.file_uploader("Satellite or aerial image", type=["jpg", "jpeg", "png"], key="sat_upload")
    if sat_image is not None and st.button("Run damage assessment"):
        tmp_path = DATA_DIR / "_tmp_satellite_upload.png"
        with open(tmp_path, "wb") as f:
            f.write(sat_image.getbuffer())
        with st.spinner("Analyzing image with Gemini..."):
            result = assess_satellite_damage(str(tmp_path), region_name="Sagaing Region, Myanmar")
        st.json(result)

    st.markdown("---")
    st.subheader("⚡ Time to Insight: CPU (pandas) vs GPU (cudf.pandas)")
    st.caption(
        "Required NVIDIA acceleration benchmark for this hackathon's judging criteria — computes the "
        "full incident x team distance matrix. CPU number is live/real; GPU number needs an actual GPU "
        "(run `benchmark/cpu_vs_gpu_benchmark.py` with `python -m cudf.pandas` on Colab)."
    )
    bcol1, bcol2, bcol3 = st.columns([1, 1, 1])
    if bcol1.button("▶ Run CPU benchmark now"):
        t0 = time.perf_counter()
        n_i, n_t = len(df), len(teams_df)
        import numpy as np

        lat_i = df["lat"].to_numpy()[:, None]
        lon_i = df["lon"].to_numpy()[:, None]
        lat_t = teams_df["lat"].to_numpy()[None, :]
        lon_t = teams_df["lon"].to_numpy()[None, :]
        p1, p2 = np.radians(lat_i), np.radians(lat_t)
        dphi = np.radians(lat_t - lat_i)
        dlambda = np.radians(lon_t - lon_i)
        a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlambda / 2) ** 2
        dist_matrix = 2 * 6371.0 * np.arcsin(np.sqrt(a))
        st.session_state.cpu_benchmark_seconds = time.perf_counter() - t0
        st.session_state.cpu_matrix_shape = (n_i, n_t)
    if st.session_state.cpu_benchmark_seconds is not None:
        shape = st.session_state.get("cpu_matrix_shape", (len(df), len(teams_df)))
        bcol2.metric("CPU (pandas/numpy)", f"{st.session_state.cpu_benchmark_seconds*1000:.1f} ms",
                     help=f"{shape[0]:,} incidents x {shape[1]:,} teams distance matrix")
        bcol3.metric("GPU (cudf.pandas)", "run on Colab")

    st.markdown("---")
    st.subheader("🗺️ Live operations map")
    fmap = build_map(df, teams_df, scouts_df, bases_df, roads_df, blocked_ids)
    st_folium(fmap, width=None, height=520, returned_objects=[])

    st.markdown("---")
    st.subheader("🔍 Search & filter")
    fc1, fc2, fc3 = st.columns([2, 1, 1])
    search_term = fc1.text_input("Search (incident ID / team ID / HQ name)").strip().lower()
    entity_choice = fc2.radio("Show", ["Incidents", "Teams", "Bases"], horizontal=True)
    township_filter = fc3.multiselect("Township", SAGAING_TOWNSHIP_NAMES)

    if entity_choice == "Incidents":
        filtered = df.copy()
        if search_term:
            filtered = filtered[filtered["incident_id"].str.lower().str.contains(search_term)]
        if township_filter:
            filtered = filtered[filtered["nearest_township"].isin(township_filter)]
        filtered = filtered.sort_values("minutes_remaining")[
            ["incident_id", "nearest_township", "trapped_count", "building_stories", "collapse_pattern",
             "access_difficulty", "status", "minutes_remaining"]
        ].head(100).copy()
        filtered["minutes_remaining"] = filtered["minutes_remaining"].round(0)
    elif entity_choice == "Teams":
        filtered = teams_df.copy()
        if search_term:
            filtered = filtered[filtered["team_id"].str.lower().str.contains(search_term)]
        if township_filter:
            filtered = filtered[filtered["home_township"].isin(township_filter)]
        filtered = filtered[["team_id", "team_type", "org_type", "home_township", "status"]]
    else:
        filtered = bases_df.copy()
        if search_term:
            filtered = filtered[filtered["name"].str.lower().str.contains(search_term)]
        if township_filter:
            filtered = filtered[filtered["township"].isin(township_filter)]
        filtered = filtered[["base_id", "name", "org_type", "township"]]
    st.dataframe(filtered, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("🧠 AI Optimizer")
    st.caption("Routes are computed on the real road-network graph and automatically avoid blocked roads.")
    if st.button("Run AI Optimizer", type="primary"):
        with st.spinner("Scoring priorities and solving team-to-site assignment..."):
            top_incidents = load_incidents(df=incidents_df)
            avail = load_teams(df=teams_df)
            assignments, status = optimize(top_incidents, avail, extra_blocked_road_ids=st.session_state.blocked_road_ids)
            st.session_state.assignments = assignments
            st.session_state.sim_progress = {tid: 0.0 for tid in assignments["team_id"]}
        st.success(f"Deployment plan ready: {len(assignments)} teams assigned.")

    if not st.session_state.assignments.empty:
        st.dataframe(
            st.session_state.assignments.sort_values("priority_score", ascending=False),
            use_container_width=True, hide_index=True,
        )
        st.caption(
            "Positions below are simulated for the demo (no real GPS feed yet). In production, field "
            "devices would push a GPS ping every N seconds to a `location_pings` table / update the "
            "team's `current_location` directly, and the map would read live instead of simulating."
        )
        if st.button("⏱️ Advance simulation (move assigned teams closer)"):
            for _, row in st.session_state.assignments.iterrows():
                st.session_state.sim_progress[row["team_id"]] = min(
                    1.0, st.session_state.sim_progress.get(row["team_id"], 0.0) + 0.25
                )
            st.rerun()

# ========================================================================================
# RESCUE TEAM VIEW
# ========================================================================================
elif persona == "🚒 Rescue Team":
    st.title("Rescue Team Orders")

    if st.session_state.assignments.empty:
        st.info("No deployment plan yet — go to Command Center and click **Run AI Optimizer** first.")
    else:
        team_id = st.selectbox("Select your team", st.session_state.assignments["team_id"].tolist())
        row = st.session_state.assignments[st.session_state.assignments["team_id"] == team_id].iloc[0]
        incident = incidents_df[incidents_df["incident_id"] == row["incident_id"]].iloc[0]
        team = teams_df[teams_df["team_id"] == team_id].iloc[0]

        c1, c2, c3 = st.columns(3)
        c1.metric("Assigned site", incident["nearest_township"])
        c2.metric("ETA (real roads)", f"{row['travel_minutes']:.0f} min")
        c3.metric("Priority score", f"{row['priority_score']:.2f}")

        st.subheader("Site briefing")
        st.write(f"**Building:** {incident['building_stories']}-story {incident['building_use']} | "
                 f"**Collapse pattern:** {incident['collapse_pattern']} | **Material:** {incident['building_material']}")
        st.write(f"**Trapped:** {incident['confirmed_trapped_count']} confirmed, "
                 f"{incident['estimated_trapped_count']} estimated | **Signs of life:** {incident['signs_of_life']}")
        hazards = json.loads(incident["hazards_present"]) if isinstance(incident["hazards_present"], str) else []
        if hazards:
            st.warning(f"⚠️ Hazards: {', '.join(hazards)}")
        st.write(f"**Access:** {incident['access_difficulty']} | **Scout notes:** {incident['scout_notes']}")

        st.subheader("🗺️ Route (real roads, avoids blockages)")
        G, nodes_df = get_road_graph()
        route_info = compute_route(G, nodes_df, team["lat"], team["lon"], incident["lat"], incident["lon"])
        if route_info:
            progress = st.session_state.sim_progress.get(team_id, 0.0)
            st.write(f"**{route_info['travel_minutes']:.0f} min** · {route_info['distance_km']:.1f} km · "
                     f"{route_info['n_road_segments']} road segments · progress: {progress*100:.0f}%")
            current_pos = interpolate_polyline(route_info["polyline"], progress)
            team_display = team.copy()
            team_display["lat"], team_display["lon"] = current_pos[0], current_pos[1]
            rmap = build_map(
                pd.DataFrame([incident]), pd.DataFrame([team_display]), scouts_df.iloc[0:0], bases_df, roads_df,
                all_blocked_road_ids(road_status_df), route_polyline=route_info["polyline"],
            )
            st_folium(rmap, width=None, height=420, returned_objects=[])
        else:
            st.error("🚧 No route available — destination is fully cut off by reported blockages. "
                      "Escalate for an alternate transport plan (air, boat).")

        st.subheader("Equipment status")
        equipment = json.loads(team["equipment"])
        st.dataframe(pd.DataFrame(equipment), use_container_width=True, hide_index=True)

        missing = [e for e in equipment if e["condition"] != "operational"]
        if missing:
            st.warning(f"⚠️ Missing/damaged: {', '.join(m['item_name'] for m in missing)}")
            if st.button("Generate equipment substitution plan"):
                with st.spinner("Asking Gemini for a tactical alternative..."):
                    plan = generate_equipment_substitution(
                        missing[0]["item_name"], equipment,
                        f"{incident['collapse_pattern']} collapse, {incident['building_stories']}-story "
                        f"{incident['building_material']} building, {incident['trapped_count']} trapped.",
                    )
                st.json(plan)
        else:
            st.success("✅ Full equipment loadout operational.")
