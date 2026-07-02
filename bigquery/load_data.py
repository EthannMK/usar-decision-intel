"""
Loads the local synthetic CSVs (data/*.csv) into BigQuery, matching bigquery/schema.sql.

NOTE: this script has NOT been execution-tested against real BigQuery - the sandbox used to
build this project has no network access to googleapis.com at all, so this was written
carefully against the documented BigQuery load-job behavior but not run live. If you hit an
error, paste the full traceback back and we'll fix it together - don't assume it's your fault.

Requires:
    GOOGLE_APPLICATION_CREDENTIALS=<path to gcp-service-account.json>
    GCP_PROJECT_ID=usar-decision-intel   (or your actual project id)

Run from the project root:
    python bigquery/load_data.py
"""

import io
import json
import os
from pathlib import Path

import pandas as pd
from google.cloud import bigquery

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "usar-decision-intel")
DATASET = "usar_decision_intel"
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SCHEMA_SQL = ROOT / "bigquery" / "schema.sql"


def run_ddl(client: bigquery.Client):
    """Creates the dataset + all tables from schema.sql (idempotent - IF NOT EXISTS everywhere)."""
    ddl = SCHEMA_SQL.read_text()
    statements = [s.strip() for s in ddl.split(";") if s.strip() and not s.strip().startswith("--")]
    for stmt in statements:
        client.query(stmt).result()
    print(f"Schema created/verified in {PROJECT_ID}.{DATASET}")


def wkt_point(lon, lat) -> str:
    """WKT is the safest, best-documented way to load a GEOGRAPHY value via NDJSON."""
    return f"POINT({float(lon)} {float(lat)})"


def load_ndjson(client: bigquery.Client, table_name: str, records: list, schema: list):
    table_ref = f"{PROJECT_ID}.{DATASET}.{table_name}"
    buf = io.BytesIO("\n".join(json.dumps(r) for r in records).encode("utf-8"))
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        schema=schema,
    )
    job = client.load_table_from_file(buf, table_ref, job_config=job_config)
    job.result()  # raises with a real error message if the load fails
    print(f"Loaded {len(records):,} rows -> {table_name}")


def load_incidents(client):
    df = pd.read_csv(DATA_DIR / "incidents.csv")
    records = [{
        "incident_id": r["incident_id"], "reported_at": r["reported_at"],
        "location": wkt_point(r["lon"], r["lat"]),
        "building_material": r["building_material"], "building_stories": int(r["building_stories"]),
        "building_use": r["building_use"], "collapse_pattern": r["collapse_pattern"],
        "confirmed_trapped_count": int(r["confirmed_trapped_count"]),
        "estimated_trapped_count": int(r["estimated_trapped_count"]),
        "trapped_count": int(r["trapped_count"]), "signs_of_life": r["signs_of_life"],
        "hazards_present": json.loads(r["hazards_present"]), "access_difficulty": r["access_difficulty"],
        "scout_notes": r["scout_notes"], "golden_hour_deadline": r["golden_hour_deadline"],
        "status": r["status"], "people_saved": int(r.get("people_saved", 0) or 0),
        "bodies_found": int(r.get("bodies_found", 0) or 0),
        "synced_from_offline": bool(r["synced_from_offline"]), "submitted_by": r["submitted_by"],
    } for _, r in df.iterrows()]
    schema = [
        bigquery.SchemaField("incident_id", "STRING"), bigquery.SchemaField("reported_at", "TIMESTAMP"),
        bigquery.SchemaField("location", "GEOGRAPHY"), bigquery.SchemaField("building_material", "STRING"),
        bigquery.SchemaField("building_stories", "INT64"), bigquery.SchemaField("building_use", "STRING"),
        bigquery.SchemaField("collapse_pattern", "STRING"), bigquery.SchemaField("confirmed_trapped_count", "INT64"),
        bigquery.SchemaField("estimated_trapped_count", "INT64"), bigquery.SchemaField("trapped_count", "INT64"),
        bigquery.SchemaField("signs_of_life", "STRING"),
        bigquery.SchemaField("hazards_present", "STRING", mode="REPEATED"),
        bigquery.SchemaField("access_difficulty", "STRING"), bigquery.SchemaField("scout_notes", "STRING"),
        bigquery.SchemaField("golden_hour_deadline", "TIMESTAMP"), bigquery.SchemaField("status", "STRING"),
        bigquery.SchemaField("people_saved", "INT64"), bigquery.SchemaField("bodies_found", "INT64"),
        bigquery.SchemaField("synced_from_offline", "BOOL"), bigquery.SchemaField("submitted_by", "STRING"),
    ]
    load_ndjson(client, "incidents", records, schema)


def load_rescue_teams(client):
    df = pd.read_csv(DATA_DIR / "rescue_teams.csv")
    records = [{
        "team_id": r["team_id"], "team_type": r["team_type"], "org_type": r["org_type"],
        "current_location": wkt_point(r["lon"], r["lat"]), "status": r["status"],
        "capabilities": json.loads(r["capabilities"]), "equipment": json.loads(r["equipment"]),
    } for _, r in df.iterrows()]
    schema = [
        bigquery.SchemaField("team_id", "STRING"), bigquery.SchemaField("team_type", "STRING"),
        bigquery.SchemaField("org_type", "STRING"), bigquery.SchemaField("current_location", "GEOGRAPHY"),
        bigquery.SchemaField("status", "STRING"), bigquery.SchemaField("capabilities", "STRING", mode="REPEATED"),
        bigquery.SchemaField("equipment", "RECORD", mode="REPEATED", fields=[
            bigquery.SchemaField("item_name", "STRING"), bigquery.SchemaField("quantity", "INT64"),
            bigquery.SchemaField("condition", "STRING"),
        ]),
    ]
    load_ndjson(client, "rescue_teams", records, schema)


def load_scouts(client):
    df = pd.read_csv(DATA_DIR / "scouts.csv")
    records = [{
        "scout_id": r["scout_id"], "current_location": wkt_point(r["lon"], r["lat"]),
        "status": r["status"], "home_township": r["home_township"],
    } for _, r in df.iterrows()]
    schema = [
        bigquery.SchemaField("scout_id", "STRING"), bigquery.SchemaField("current_location", "GEOGRAPHY"),
        bigquery.SchemaField("status", "STRING"), bigquery.SchemaField("home_township", "STRING"),
    ]
    load_ndjson(client, "scouts", records, schema)


def load_road_nodes(client):
    df = pd.read_csv(DATA_DIR / "road_nodes.csv")
    records = [{
        "node_id": r["node_id"], "location": wkt_point(r["lon"], r["lat"]), "township": r["township"],
    } for _, r in df.iterrows()]
    schema = [
        bigquery.SchemaField("node_id", "STRING"), bigquery.SchemaField("location", "GEOGRAPHY"),
        bigquery.SchemaField("township", "STRING"),
    ]
    load_ndjson(client, "road_nodes", records, schema)


def load_roads(client):
    df = pd.read_csv(DATA_DIR / "roads.csv")
    records = [{
        "road_id": r["road_id"], "from_node": r["from_node"], "to_node": r["to_node"],
        "geometry": f"LINESTRING({r['lon1']} {r['lat1']}, {r['lon2']} {r['lat2']})",
        "road_type": r["road_type"], "surface": r["surface"], "distance_km": float(r["distance_km"]),
    } for _, r in df.iterrows()]
    schema = [
        bigquery.SchemaField("road_id", "STRING"), bigquery.SchemaField("from_node", "STRING"),
        bigquery.SchemaField("to_node", "STRING"), bigquery.SchemaField("geometry", "GEOGRAPHY"),
        bigquery.SchemaField("road_type", "STRING"), bigquery.SchemaField("surface", "STRING"),
        bigquery.SchemaField("distance_km", "FLOAT64"),
    ]
    load_ndjson(client, "roads", records, schema)


def load_road_status(client):
    df = pd.read_csv(DATA_DIR / "road_status.csv")
    records = [{
        "report_id": r["report_id"], "road_id": r["road_id"], "status": r["status"],
        "blockage_type": r["blockage_type"], "reported_at": r["reported_at"],
        "reported_by": r["reported_by"], "notes": r["notes"],
    } for _, r in df.iterrows()]
    schema = [
        bigquery.SchemaField("report_id", "STRING"), bigquery.SchemaField("road_id", "STRING"),
        bigquery.SchemaField("status", "STRING"), bigquery.SchemaField("blockage_type", "STRING"),
        bigquery.SchemaField("reported_at", "TIMESTAMP"), bigquery.SchemaField("reported_by", "STRING"),
        bigquery.SchemaField("notes", "STRING"),
    ]
    load_ndjson(client, "road_status", records, schema)


def load_bases(client):
    df = pd.read_csv(DATA_DIR / "bases.csv")
    records = [{
        "base_id": r["base_id"], "name": r["name"], "org_type": r["org_type"],
        "township": r["township"], "location": wkt_point(r["lon"], r["lat"]),
    } for _, r in df.iterrows()]
    schema = [
        bigquery.SchemaField("base_id", "STRING"), bigquery.SchemaField("name", "STRING"),
        bigquery.SchemaField("org_type", "STRING"), bigquery.SchemaField("township", "STRING"),
        bigquery.SchemaField("location", "GEOGRAPHY"),
    ]
    load_ndjson(client, "bases", records, schema)


if __name__ == "__main__":
    client = bigquery.Client(project=PROJECT_ID)
    print(f"Connected to project: {client.project}")
    run_ddl(client)
    load_incidents(client)
    load_rescue_teams(client)
    load_scouts(client)
    load_road_nodes(client)
    load_roads(client)
    load_road_status(client)
    load_bases(client)
    print("\nAll tables loaded. Verify in the BigQuery console under the usar_decision_intel dataset.")
