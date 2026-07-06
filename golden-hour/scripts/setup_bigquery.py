"""One-time BigQuery setup: create dataset + tables and load synthetic data.

Schema highlights (judging criteria):
  - sites.location  : native GEOGRAPHY field for spatial queries
  - teams.equipment : ARRAY<STRUCT<...>> nested equipment inventory

Prereq: set GOOGLE_APPLICATION_CREDENTIALS to your service-account key, or run
`gcloud auth application-default login`.

Run:  python scripts/setup_bigquery.py
"""
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.config import GCP_PROJECT, BQ_DATASET, DATA_DIR  # noqa: E402

from google.cloud import bigquery  # noqa: E402

client = bigquery.Client(project=GCP_PROJECT)
ds_id = f"{GCP_PROJECT}.{BQ_DATASET}"

# ---- dataset -------------------------------------------------------------
ds = bigquery.Dataset(ds_id)
ds.location = "US"
client.create_dataset(ds, exists_ok=True)
print(f"Dataset ready: {ds_id}")

# ---- sites table (GEOGRAPHY) ----------------------------------------------
sites_schema = [
    bigquery.SchemaField("site_id", "STRING"),
    bigquery.SchemaField("site_name", "STRING"),
    bigquery.SchemaField("township", "STRING"),
    bigquery.SchemaField("lat", "FLOAT64"),
    bigquery.SchemaField("lon", "FLOAT64"),
    bigquery.SchemaField("location", "GEOGRAPHY"),
    bigquery.SchemaField("building_type", "STRING"),
    bigquery.SchemaField("collapse_pattern", "STRING"),
    bigquery.SchemaField("est_trapped", "INT64"),
    bigquery.SchemaField("priority_score", "FLOAT64"),
    bigquery.SchemaField("road_type", "STRING"),
    bigquery.SchemaField("status", "STRING"),
    bigquery.SchemaField("reported_at", "TIMESTAMP"),
    bigquery.SchemaField("scout_notes", "STRING"),
]

# ---- teams table (ARRAY<STRUCT>) -------------------------------------------
teams_schema = [
    bigquery.SchemaField("team_id", "STRING"),
    bigquery.SchemaField("team_name", "STRING"),
    bigquery.SchemaField("team_type", "STRING"),
    bigquery.SchemaField("base_lat", "FLOAT64"),
    bigquery.SchemaField("base_lon", "FLOAT64"),
    bigquery.SchemaField("base_location", "GEOGRAPHY"),
    bigquery.SchemaField("personnel", "INT64"),
    bigquery.SchemaField("status", "STRING"),
    bigquery.SchemaField(
        "equipment", "RECORD", mode="REPEATED",
        fields=[
            bigquery.SchemaField("item", "STRING"),
            bigquery.SchemaField("qty", "INT64"),
            bigquery.SchemaField("operational", "BOOL"),
        ],
    ),
]

for name, schema in [("sites", sites_schema), ("teams", teams_schema)]:
    table_id = f"{ds_id}.{name}"
    client.delete_table(table_id, not_found_ok=True)
    client.create_table(bigquery.Table(table_id, schema=schema))
    print(f"Table created: {table_id}")

# ---- load sites ------------------------------------------------------------
with open(os.path.join(DATA_DIR, "sites.csv"), encoding="utf-8") as f:
    site_rows = []
    for r in csv.DictReader(f):
        r["lat"], r["lon"] = float(r["lat"]), float(r["lon"])
        r["est_trapped"] = int(r["est_trapped"])
        r["priority_score"] = float(r["priority_score"])
        r["location"] = f"POINT({r['lon']} {r['lat']})"
        site_rows.append(r)
errors = client.insert_rows_json(f"{ds_id}.sites", site_rows)
print(f"Loaded {len(site_rows)} sites, errors: {errors or 'none'}")

# ---- load teams ------------------------------------------------------------
with open(os.path.join(DATA_DIR, "teams.json"), encoding="utf-8") as f:
    team_rows = json.load(f)
for t in team_rows:
    t["base_location"] = f"POINT({t['base_lon']} {t['base_lat']})"
errors = client.insert_rows_json(f"{ds_id}.teams", team_rows)
print(f"Loaded {len(team_rows)} teams, errors: {errors or 'none'}")

# ---- demo spatial query ------------------------------------------------------
q = f"""
SELECT site_id, site_name, priority_score,
       ROUND(ST_DISTANCE(location, ST_GEOGPOINT(96.0891, 21.9588))/1000, 1) AS km_from_epicenter
FROM `{ds_id}.sites`
ORDER BY priority_score DESC LIMIT 5
"""
print("\nTop-5 priority sites (GEOGRAPHY query):")
for row in client.query(q).result():
    print(f"  {row.site_id} {row.site_name:<28} p={row.priority_score} {row.km_from_epicenter} km")
print("\nBigQuery setup complete.")
