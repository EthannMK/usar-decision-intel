"""Central configuration for Golden Hour platform."""
import os

GCP_PROJECT = os.environ.get("GCP_PROJECT", "usar-decision-intel")
GCP_LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
BQ_DATASET = os.environ.get("BQ_DATASET", "golden_hour")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Epicenter for the demo scenario: Mandalay, Myanmar (Sagaing Fault)
EPICENTER = (21.9588, 96.0891)

# Golden-hour window (hours after event) after which survival odds collapse
GOLDEN_HOUR_LIMIT_H = 72

# Travel speeds km/h by road type
ROAD_SPEEDS = {"paved": 40.0, "unpaved": 15.0, "damaged": 8.0}

# Which team types can handle which collapse severity
# Heavy = reinforced concrete pancake; Medium = mixed masonry; Light = timber/light
TEAM_CAPABILITY = {
    "Heavy": {"pancake", "lean-to", "v-shape", "cantilever"},
    "Medium": {"lean-to", "v-shape", "cantilever"},
    "Light": {"cantilever"},
}

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
