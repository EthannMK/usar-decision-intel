"""
Live earthquake event feed via the USGS Earthquake Hazards Program API (free, no API key,
public GeoJSON, CORS-permissive - https://earthquake.usgs.gov/fdsnws/event/1/).

Identifies a main shock + its aftershock sequence for a region, so the dashboard can show a
real seismic event timeline instead of a single made-up disaster time. For the Sagaing region
demo, this naturally surfaces the real M7.7 2025 Mandalay earthquake and its real M6.7
aftershock (~11 minutes later) - i.e. the actual event this project is modeled on.

Falls back to that same real event (hardcoded) if the live API is unreachable, so the demo
never breaks due to network issues.
"""

from datetime import datetime, timezone

import requests

USGS_QUERY_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"

# Sagaing region bounding box (generous margin around the township cluster)
SAGAING_BBOX = {"minlatitude": 20.5, "maxlatitude": 25.5, "minlongitude": 93.0, "maxlongitude": 97.5}

# Real fallback data: the actual 2025 M7.7 Mandalay/Sagaing earthquake + its real M6.7 aftershock,
# from USGS (event ids us7000pn9s and us7000pn9z). Used if the live API call fails.
FALLBACK_EVENTS = [
    {"id": "us7000pn9s", "mag": 7.7, "place": "2025 Mandalay, Burma (Myanmar) Earthquake",
     "time_ms": 1743142852715, "lat": 22.011, "lon": 95.9363},
    {"id": "us7000pn9z", "mag": 6.7, "place": "Burma (Myanmar) - aftershock",
     "time_ms": 1743143524777, "lat": 21.6975, "lon": 95.969},
]


def fetch_events(bbox=None, min_magnitude=4.0, start_time="2025-01-01", limit=50):
    """Pulls real earthquake events for the region from USGS. Returns a list of dicts sorted by
    time. Raises on network/parse failure - callers should catch and use FALLBACK_EVENTS."""
    bbox = bbox or SAGAING_BBOX
    params = {
        "format": "geojson",
        "starttime": start_time,
        "minmagnitude": min_magnitude,
        "orderby": "time-asc",
        "limit": limit,
        **bbox,
    }
    resp = requests.get(USGS_QUERY_URL, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    events = []
    for f in data.get("features", []):
        props, geom = f["properties"], f["geometry"]
        events.append({
            "id": f["id"], "mag": props["mag"], "place": props["place"],
            "time_ms": props["time"], "lon": geom["coordinates"][0], "lat": geom["coordinates"][1],
        })
    return sorted(events, key=lambda e: e["time_ms"])


def build_event_timeline(events=None):
    """Labels the largest-magnitude event as the Main Shock and everything else (in time order)
    as Aftershock 1, 2, ... Returns a list of dicts with a human-readable phase label and
    minutes-since-main-shock, ready to render in a table."""
    if events is None:
        try:
            events = fetch_events()
            if not events:
                events = FALLBACK_EVENTS
        except Exception:
            events = FALLBACK_EVENTS

    main_shock = max(events, key=lambda e: e["mag"])
    main_time = main_shock["time_ms"]

    timeline = []
    aftershock_n = 0
    for e in events:
        is_main = e["id"] == main_shock["id"]
        label = "Main Shock" if is_main else f"Aftershock {aftershock_n + 1}"
        if not is_main:
            aftershock_n += 1
        minutes_after = (e["time_ms"] - main_time) / 60000
        timeline.append({
            "phase": label,
            "magnitude": e["mag"],
            "place": e["place"],
            "time_utc": datetime.fromtimestamp(e["time_ms"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "minutes_after_main_shock": round(minutes_after, 1),
            "lat": e["lat"], "lon": e["lon"],
        })
    return timeline, main_shock


if __name__ == "__main__":
    timeline, main_shock = build_event_timeline()
    print(f"Main shock: M{main_shock['mag']} - {main_shock['place']}")
    for row in timeline:
        print(f"  {row['phase']:14s} M{row['magnitude']:<4} {row['time_utc']}  "
              f"(+{row['minutes_after_main_shock']:.0f} min)  {row['place']}")
