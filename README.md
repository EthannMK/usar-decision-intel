# USAR Decision Intelligence Platform

AI-powered decision intelligence for earthquake search-and-rescue, built for the "AI for Better
Living and Smarter Communities" hackathon (Disaster Response & Recovery track). Scoped to
**Sagaing Region, Myanmar** — the real epicenter area of the 2025 M7.7 Mandalay earthquake.

## Status
Data layer, routing engine, and the full Streamlit app (3 persona views) are working end to end
against local synthetic data. See `SETUP_GUIDE.md` for the GCP steps needed to connect real
BigQuery + Vertex AI Gemini.

## Structure
```
bigquery/
  schema.sql              BigQuery DDL: incidents, rescue_teams, scouts, deployments, road_nodes,
                           roads, road_status, bases
data/
  generate_synthetic_data.py   Generates the hybrid dataset: 8 real Sagaing region townships +
                                3 national hub cities (context only), a CONNECTED road network,
                                incidents, gov/NGO teams, scouts, bases, seed road blockages
  incidents.csv / rescue_teams.csv / scouts.csv / road_nodes.csv / roads.csv / bases.csv / road_status.csv
ai/
  gemini_priority_scoring.py   Vertex AI Gemini: priority scoring, equipment substitution,
                                satellite/aerial image damage assessment (% impact area)
  earthquake_feed.py       Live USGS earthquake feed - real main shock + aftershock timeline,
                            falls back to the actual 2025 M7.7 event if offline
optimization/
  routing_graph.py         Custom NetworkX routing engine (Dijkstra) - real road-network travel
                            time that automatically reroutes around scout-reported blockages.
                            Google's Routes API can't do this (no support for avoiding arbitrary
                            dynamic road segments), so this is a from-scratch graph instead.
  or_tools_optimizer.py    CP-SAT team-to-site assignment, using routing_graph for travel time
app/
  streamlit_app.py         Scout (site report / road status / outcome reporting) / Command Center
                            (live Folium map with icons+status colors, search & filter, seismic
                            timeline, satellite assessment, CPU vs GPU benchmark, AI Optimizer) /
                            Rescue Team (routed orders with simulated live movement)
benchmark/                 (next) standalone CPU vs GPU (pandas vs cudf.pandas) script for Colab
requirements.txt
SETUP_GUIDE.md             Step-by-step GCP console setup + budget notes
```

## Architecture
- **Data layer**: BigQuery only — `incidents` (GEOGRAPHY, expanded USAR triage fields incl.
  people saved/bodies found), `rescue_teams` + `scouts` (gov/NGO affiliated, GEOGRAPHY, status),
  `bases` (static National Rescue Dept / NGO HQs), `road_nodes` + `roads` (routable network),
  `road_status` (live blockages), `deployments` (OR-Tools output).
- **Routing**: a custom graph over the road network, not Google's Routes API — dynamic
  scout-reported blockages simply remove/penalize edges, and every route recalculates around
  them. Rendered as an animated AntPath line. At scale this is exactly the workload NVIDIA's
  cuGraph (RAPIDS family) accelerates on GPU.
- **Acceleration layer**: same pandas code run standard vs. patched with `cudf.pandas` on a GPU
  (Colab), timed side by side for the dashboard's "Time to Insight" panel — a required judging
  criterion for this hackathon's NVIDIA track, not optional.
- **Decision layer**: Gemini 1.5 Flash scores incident priority (0.0-1.0), generates equipment
  substitution plans, and independently estimates % damage from an uploaded satellite/aerial
  image; OR-Tools assigns teams to sites minimizing real road travel time.
- **Situational awareness**: live USGS earthquake feed identifies the real main shock + aftershock
  sequence for the region; scout outcome reports (saved/deceased) roll up into Command Center
  scorecards; team/scout positions can be advanced along their route for a live-movement demo
  (real deployment would use periodic GPS pings from field devices instead of simulation).
- **App layer**: Streamlit, three views for Scout / Command Center / Rescue Team, with a live
  Folium map (distinct icons for gov HQ / NGO hub / gov team / NGO team / scout, colored by
  status, formatted HTML tooltips), search & filter, and Looker Studio for auxiliary read-only
  executive reporting.

## Next steps
1. Complete `SETUP_GUIDE.md` (GCP project, APIs, service account key).
2. Once the key is available, load `data/*.csv` into BigQuery via `bigquery/schema.sql`.
3. Point `app/streamlit_app.py` at BigQuery instead of local CSVs.
4. Build `benchmark/cpu_vs_gpu_benchmark.py` and run it on Colab for the real GPU number.
