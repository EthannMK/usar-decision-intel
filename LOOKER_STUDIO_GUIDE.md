# Looker Studio Dashboard — Step-by-Step Guide

This builds the **read-only executive dashboard** on top of your BigQuery tables, satisfying
the hackathon's Looker requirement. It's separate from the Streamlit app: Streamlit is the
interactive tool scouts/command center/rescue teams *use*; Looker Studio is the polished
judge-facing report that shows the data is really flowing through BigQuery.

There's no code or API for this part — Looker Studio is a web console
(lookerstudio.google.com), so every step below is a click-through. Budget about 30–40 minutes.

## Before you start

- Your BigQuery tables must already be loaded (`python bigquery\load_data.py` — you've done this).
- Looker Studio needs a Google Cloud **billing account** attached to query BigQuery. You should
  already have this from GCP project setup, since BigQuery itself requires it.
- Your project ID: `usar-decision-intel`
- Your dataset: `usar_decision_intel`

## Step 1 — Create the incidents data source

1. Go to **[lookerstudio.google.com](https://lookerstudio.google.com)** and sign in with the
   same Google account you used for GCP.
2. Click **Create** (top left) → **Data source**.
3. Search for and select the **BigQuery** connector.
4. Choose **CUSTOM QUERY** (not "My Projects" table picker) — this lets us flatten the
   GEOGRAPHY column into plain lat/lon, same trick the Streamlit app uses.
5. Under **Project**, pick `usar-decision-intel`.
6. Paste this into the query box:
   ```sql
   SELECT
     incident_id, status, building_use, building_material, collapse_pattern,
     building_stories, trapped_count, confirmed_trapped_count, estimated_trapped_count,
     signs_of_life, access_difficulty, priority_score, people_saved, bodies_found,
     reported_at, golden_hour_deadline,
     ST_Y(location) AS lat, ST_X(location) AS lon
   FROM `usar-decision-intel.usar_decision_intel.incidents`
   ```
7. Click **RUN QUERY**, then **ADD** (top right) to confirm the field list looks right, then
   **ADD TO REPORT**.
8. Rename this data source to **Incidents** (click the name at the top).

## Step 2 — Create the rescue_teams and bases data sources

Repeat Step 1 two more times (**Create → Data source → BigQuery → CUSTOM QUERY**), once each
for these two queries:

**Rescue Teams:**
```sql
SELECT team_id, team_type, org_type, status,
       ST_Y(current_location) AS lat, ST_X(current_location) AS lon
FROM `usar-decision-intel.usar_decision_intel.rescue_teams`
```

**Bases:**
```sql
SELECT base_id, name, org_type, township,
       ST_Y(location) AS lat, ST_X(location) AS lon
FROM `usar-decision-intel.usar_decision_intel.bases`
```

Name them **Rescue Teams** and **Bases** respectively.

## Step 3 — Build the report

Back in the report editor (if it didn't open automatically, go to your new report from the
Looker Studio home page), add these charts. For each: click **Insert** → pick the chart type →
draw a box on the canvas → in the right panel, set **Data source** to the one named in
brackets, then set the fields listed.

1. **Scorecards row** (4 of them) — Insert → Scorecard — *[Incidents]*
   - "Open Incidents": Metric = `incident_id` (Count), Filter: `status` ≠ `resolved`
   - "Total Trapped": Metric = `trapped_count` (Sum), same filter
   - "People Saved": Metric = `people_saved` (Sum)
   - "Bodies Found": Metric = `bodies_found` (Sum)

   Duplicate one more scorecard from *[Rescue Teams]*: "Available Teams" — Metric =
   `team_id` (Count), Filter: `status` = `available`.

2. **Incidents by Status** — Insert → Pie chart — *[Incidents]* — Dimension: `status`,
   Metric: `incident_id` (Count).

3. **Trapped Count by Collapse Pattern** — Insert → Bar chart — *[Incidents]* — Dimension:
   `collapse_pattern`, Metric: `trapped_count` (Sum).

4. **Incidents by Building Use** — Insert → Column chart — *[Incidents]* — Dimension:
   `building_use`, Metric: `incident_id` (Count).

5. **Incident Map** — Insert → Google Maps (bubble map) — *[Incidents]* — Location: use `lat`
   and `lon` as a composite geo field (Looker Studio will prompt you to combine them), Size:
   `trapped_count`, Color: `status`.

6. **Teams & Bases Map** — Insert → Google Maps — *[Rescue Teams]* — same lat/lon setup,
   Color: `org_type`. Optionally blend in *[Bases]* the same way as a second layer, or make it
   a second map chart if blending feels fiddly at this stage.

7. **Top Priority Incidents Table** — Insert → Table — *[Incidents]* — Dimensions:
   `incident_id`, `status`, `building_use`, `trapped_count`, `golden_hour_deadline` — Sort by
   `trapped_count` descending — Rows: 10.

## Step 4 — Style and title it

- Report name (top left): **USAR Decision Intelligence — Executive Overview**.
- Optional: Theme → pick a clean preset (File → Theme and layout) so it doesn't look default.
- Add a text box at the top with a one-line description, e.g. "Live BigQuery data — Sagaing
  Region earthquake response."

## Step 5 — Share (only when you're ready)

Looker Studio reports are private until you explicitly share them. Click **Share** (top right)
and either:
- Add specific people (teammates, judges) by email, or
- **File → Share → Publish as web page** if the hackathon wants a public link.

I won't do this step for you even in Chrome-assisted mode — changing sharing/access settings
is something you should do yourself.

## Notes on data freshness

- `people_saved` / `bodies_found` will show `0` until scout outcome reports are written back to
  BigQuery — right now the Scout "Report Outcome" tab only stores them in the Streamlit session
  (see the caption in the app about this being a demo simplification). If you want this to show
  live numbers, that's a small follow-up: add an `INSERT`/`UPDATE` call in
  `app/streamlit_app.py`'s outcome-report handler.
- `priority_score` will be blank/null until the Gemini scoring step has actually run against an
  incident and written a score back — currently the optimizer computes a fallback score in
  memory (`trapped_count`-based) rather than persisting it to BigQuery. Worth flagging in your
  demo narrative as a "next iteration" item if a judge asks.
