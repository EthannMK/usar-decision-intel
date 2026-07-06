"""Data access layer: BigQuery when credentials exist, local CSV/JSON fallback.

The fallback guarantees the demo never breaks live on stage.
"""
import json
import os

import pandas as pd

from src.config import GCP_PROJECT, BQ_DATASET, DATA_DIR


def _bq_available() -> bool:
    if os.environ.get("FORCE_LOCAL_DATA") == "1":
        return False
    try:
        from google.auth import default
        default()
        return True
    except Exception:
        return False


BQ_MODE = _bq_available()


def load_sites() -> pd.DataFrame:
    if BQ_MODE:
        try:
            from google.cloud import bigquery
            client = bigquery.Client(project=GCP_PROJECT)
            df = client.query(
                f"SELECT * EXCEPT(location) FROM `{GCP_PROJECT}.{BQ_DATASET}.sites`"
            ).to_dataframe()
            df["reported_at"] = pd.to_datetime(df["reported_at"], utc=True)
            return df
        except Exception as e:  # fall through to local
            print(f"[datastore] BigQuery failed ({e}), using local CSV")
    df = pd.read_csv(os.path.join(DATA_DIR, "sites.csv"))
    df["reported_at"] = pd.to_datetime(df["reported_at"], utc=True)
    return df


def load_teams() -> list[dict]:
    if BQ_MODE:
        try:
            from google.cloud import bigquery
            client = bigquery.Client(project=GCP_PROJECT)
            rows = client.query(
                f"SELECT * EXCEPT(base_location) FROM `{GCP_PROJECT}.{BQ_DATASET}.teams`"
            ).result()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"[datastore] BigQuery failed ({e}), using local JSON")
    with open(os.path.join(DATA_DIR, "teams.json"), encoding="utf-8") as f:
        return json.load(f)


def insert_site(row: dict) -> None:
    """Insert a newly triaged scout report. Best-effort BigQuery write."""
    if BQ_MODE:
        try:
            from google.cloud import bigquery
            client = bigquery.Client(project=GCP_PROJECT)
            r = dict(row)
            r["location"] = f"POINT({r['lon']} {r['lat']})"
            client.insert_rows_json(f"{GCP_PROJECT}.{BQ_DATASET}.sites", [r])
        except Exception as e:
            print(f"[datastore] BigQuery insert failed: {e}")
