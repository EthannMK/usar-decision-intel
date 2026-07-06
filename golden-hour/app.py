"""GOLDEN HOUR - Earthquake USAR Decision Intelligence Platform.

Streamlit front-end with three operational roles:
  1. Scout Team    - field reporting with simulated offline queue
  2. Command Center - live map, golden-hour countdowns, AI optimizer
  3. Rescue Team   - deployment orders + Gemini tactical plans

Run:  streamlit run app.py
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pydeck as pdk
import streamlit as st

from src import datastore
from src.config import EPICENTER, GOLDEN_HOUR_LIMIT_H
from src.optimizer import optimize_deployment
from src.triage import triage_report, substitution_plan

st.set_page_config(page_title="Golden Hour | USAR Decision Intelligence",
                   page_icon="⛑️", layout="wide")

# ---------------------------------------------------------------- state ----
if "sites" not in st.session_state:
    st.session_state.sites = datastore.load_sites()
if "teams" not in st.session_state:
    st.session_state.teams = datastore.load_teams()
if "queue" not in st.session_state:
    st.session_state.queue = []          # offline scout submissions
if "result" not in st.session_state:
    st.session_state.result = None       # last optimizer run
if "event_time" not in st.session_state:
    st.session_state.event_time = datetime.now(timezone.utc) - timedelta(hours=6)

sites: pd.DataFrame = st.session_state.sites
teams: list = st.session_state.teams
EVENT_TIME = st.session_state.event_time


def hours_since_event() -> float:
    return (datetime.now(timezone.utc) - EVENT_TIME).total_seconds() / 3600


def golden_hour_left(reported_at) -> float:
    deadline = EVENT_TIME + timedelta(hours=GOLDEN_HOUR_LIMIT_H)
    return max(0.0, (deadline - datetime.now(timezone.utc)).total_seconds() / 3600)


def process_report(rep: dict):
    """Triage a scout report with Gemini and add it to the site table."""
    tri = triage_report(rep["notes"], rep["building_type"], rep["est_trapped"],
                        rep.get("image_bytes"), rep.get("mime", "image/jpeg"))
    new_id = f"S{len(st.session_state.sites) + 1:04d}"
    row = {
        "site_id": new_id, "site_name": rep["site_name"], "township": rep["township"],
        "lat": rep["lat"], "lon": rep["lon"], "building_type": rep["building_type"],
        "collapse_pattern": tri["collapse_pattern"], "est_trapped": tri["est_trapped"],
        "priority_score": tri["priority_score"], "road_type": rep["road_type"],
        "status": "awaiting_rescue",
        "reported_at": datetime.now(timezone.utc).isoformat(),
        "scout_notes": rep["notes"],
    }
    st.session_state.sites = pd.concat(
        [st.session_state.sites, pd.DataFrame([row])], ignore_index=True)
    datastore.insert_site(row)
    return new_id, tri


# ---------------------------------------------------------------- sidebar --
st.sidebar.title("⛑️ GOLDEN HOUR")
st.sidebar.caption("Earthquake USAR Decision Intelligence")
role = st.sidebar.radio("Operational View", ["🥾 Scout Team", "🎯 Command Center", "🚒 Rescue Team"])
elapsed = hours_since_event()
left = max(0.0, GOLDEN_HOUR_LIMIT_H - elapsed)
st.sidebar.metric("⏱️ Golden-hour window left", f"{left:.1f} h",
                  f"-{elapsed:.1f} h since M7.7 event", delta_color="inverse")
st.sidebar.caption(f"Data source: {'BigQuery ✅' if datastore.BQ_MODE else 'Local demo files'}")

# ============================================================ SCOUT VIEW ===
if role.startswith("🥾"):
    st.header("🥾 Scout Team — Field Report")
    online = st.toggle("📶 Network connection", value=True,
                       help="Toggle OFF to simulate a dead zone. Reports queue locally and sync when back online.")

    with st.form("scout_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            site_name = st.text_input("Site name / landmark", "Collapsed building near market")
            township = st.text_input("Township", "Chanayethazan")
            building_type = st.selectbox("Building material", [
                "reinforced_concrete", "brick_masonry", "timber", "steel_frame", "mixed"])
            road_type = st.selectbox("Access road condition", ["paved", "unpaved", "damaged"])
        with c2:
            lat = st.number_input("Latitude", value=float(EPICENTER[0]), format="%.5f")
            lon = st.number_input("Longitude", value=float(EPICENTER[1]), format="%.5f")
            est_trapped = st.number_input("Estimated people trapped", 1, 200, 4)
        notes = st.text_area("Field notes (free text — AI will triage)",
                             "Three-story concrete building, pancake collapse. Voices heard "
                             "under second slab. Strong gas smell. Road partially blocked.")
        photo = st.file_uploader("Site photo (optional, analyzed by Gemini)", type=["jpg", "jpeg", "png"])
        submitted = st.form_submit_button("🚨 Submit report", use_container_width=True)

    if submitted:
        rep = {"site_name": site_name, "township": township, "building_type": building_type,
               "road_type": road_type, "lat": lat, "lon": lon,
               "est_trapped": int(est_trapped), "notes": notes}
        if photo:
            rep["image_bytes"] = photo.getvalue()
            rep["mime"] = photo.type
        if online:
            with st.spinner("Gemini triaging report..."):
                new_id, tri = process_report(rep)
            st.success(f"Report {new_id} triaged and sent to Command Center")
            a, b, c = st.columns(3)
            a.metric("AI Priority score", f"{tri['priority_score']:.2f}")
            b.metric("Collapse pattern", tri["collapse_pattern"])
            c.metric("Team required", tri["required_team_type"])
            st.info(f"**AI reasoning:** {tri['reasoning']}  \n_source: {tri['source']}_")
        else:
            st.session_state.queue.append(rep)
            st.warning(f"📴 No network — report stored locally. Queue: {len(st.session_state.queue)} pending")

    if st.session_state.queue:
        st.divider()
        st.subheader(f"📥 Offline queue — {len(st.session_state.queue)} report(s) pending")
        st.dataframe(pd.DataFrame([{k: v for k, v in r.items() if k not in ("image_bytes", "mime")}
                                   for r in st.session_state.queue]),
                     use_container_width=True, hide_index=True)
        if online and st.button("🔄 Sync queue to Command Center", type="primary"):
            prog = st.progress(0.0, "Syncing...")
            n = len(st.session_state.queue)
            for i, rep in enumerate(list(st.session_state.queue)):
                process_report(rep)
                prog.progress((i + 1) / n, f"Synced {i + 1}/{n}")
            st.session_state.queue = []
            st.success("All queued reports triaged and synced ✅")

# ======================================================== COMMAND CENTER ===
elif role.startswith("🎯"):
    st.header("🎯 Command Center — Executive Dashboard")
    active = sites[sites["status"] == "awaiting_rescue"]
    critical = active[active["priority_score"] >= 0.8]
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Active sites", len(active))
    m2.metric("Critical (p ≥ 0.8)", len(critical))
    m3.metric("Est. people trapped", int(active["est_trapped"].sum()))
    m4.metric("Teams available", sum(1 for t in teams if t["status"] == "available"))
    m5.metric("⏱️ Window left", f"{left:.1f} h")

    # ---- map ----
    layer_sites = pdk.Layer(
        "ScatterplotLayer", data=active,
        get_position=["lon", "lat"],
        get_fill_color="[255, 255*(1-priority_score), 40, 190]",
        get_radius="300 + priority_score * 900", pickable=True)
    layers = [layer_sites]
    tdf = pd.DataFrame(teams)
    layers.append(pdk.Layer("ScatterplotLayer", data=tdf,
                            get_position=["base_lon", "base_lat"],
                            get_fill_color="[30, 100, 255, 220]", get_radius=600))
    if st.session_state.result:
        arcs = pd.DataFrame(st.session_state.result["assignments"])
        layers.append(pdk.Layer("ArcLayer", data=arcs,
                                get_source_position=["team_lon", "team_lat"],
                                get_target_position=["site_lon", "site_lat"],
                                get_source_color=[30, 100, 255], get_target_color=[255, 60, 40],
                                get_width=3))
    st.pydeck_chart(pdk.Deck(
        map_style=None,
        initial_view_state=pdk.ViewState(latitude=EPICENTER[0], longitude=EPICENTER[1], zoom=9),
        layers=layers,
        tooltip={"text": "{site_name}\nPriority: {priority_score} | Trapped: {est_trapped}"}))
    st.caption("🔴 collapse sites (darker red = higher AI priority) · 🔵 rescue team bases · arcs = optimized deployments")

    # ---- optimizer ----
    st.divider()
    cA, cB = st.columns([1, 2])
    with cA:
        st.subheader("🧠 AI Deployment Optimizer")
        st.caption("Google OR-Tools CP-SAT · maximizes expected survivors reached inside the golden-hour window")
        if st.button("▶️ RUN OPTIMIZER", type="primary", use_container_width=True):
            with st.spinner("Solving constrained assignment..."):
                st.session_state.result = optimize_deployment(sites, teams, hours_since_event())
            st.rerun()
        if st.session_state.result:
            s = st.session_state.result["stats"]
            st.metric("Expected survivors reached", s["total_expected_survivors"])
            st.metric("Solve time", f"{s['solve_seconds']} s ({s['solver_status']})")
            st.metric("Teams deployed / sites waiting", f"{s['teams_deployed']} / {s['sites_waiting']}")
    with cB:
        if st.session_state.result:
            st.subheader("📋 Deployment orders")
            adf = pd.DataFrame(st.session_state.result["assignments"])
            st.dataframe(adf[["team_name", "team_type", "site_name", "collapse_pattern",
                              "priority_score", "est_trapped", "travel_time_h",
                              "eta_hours_after_event", "expected_survivors"]],
                         use_container_width=True, hide_index=True)
        else:
            st.subheader("⏳ Top-priority sites (golden-hour countdown)")
            top = active.nlargest(10, "priority_score").copy()
            top["window_left_h"] = top["reported_at"].apply(lambda r: round(golden_hour_left(r), 1))
            st.dataframe(top[["site_id", "site_name", "collapse_pattern", "est_trapped",
                              "priority_score", "road_type", "window_left_h"]],
                         use_container_width=True, hide_index=True)

# =========================================================== RESCUE VIEW ===
else:
    st.header("🚒 Rescue Team — Ground Operations")
    if not st.session_state.result:
        st.info("No deployment orders yet. Command Center must run the AI Optimizer first.")
    else:
        names = [a["team_name"] for a in st.session_state.result["assignments"]]
        pick = st.selectbox("Select your team", names)
        a = next(x for x in st.session_state.result["assignments"] if x["team_name"] == pick)
        team = next(t for t in teams if t["team_id"] == a["team_id"])

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("🎯 Target site", a["site_name"])
        c2.metric("🚚 Travel time", f"{a['travel_time_h']} h", f"{a['road_type']} road")
        c3.metric("⏱️ ETA post-event", f"{a['eta_hours_after_event']} h")
        c4.metric("👥 Est. trapped", a["est_trapped"])

        st.map(pd.DataFrame([{"lat": a["site_lat"], "lon": a["site_lon"]}]), zoom=11)

        st.subheader("🧰 Your equipment")
        st.dataframe(pd.DataFrame(team["equipment"]), use_container_width=True, hide_index=True)

        st.subheader("📐 AI Tactical Rescue Plan (Gemini)")
        if st.button("Generate tactical plan", type="primary"):
            with st.spinner("Gemini engineering officer drafting plan..."):
                plan = substitution_plan(a, team)
            if plan.get("missing"):
                st.warning(f"⚠️ Missing equipment: {', '.join(plan['missing'])} — substitution plan generated")
            st.success(plan["substitution_summary"])
            for step in plan["plan_steps"]:
                st.markdown(f"- {step}")
            st.error(f"**Risk note:** {plan['risk_note']}")
            st.caption(f"source: {plan.get('source', 'standard kit')}")
