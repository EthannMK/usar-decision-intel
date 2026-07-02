-- BigQuery schema for the USAR Decision Intelligence Platform
-- Run with: bq query --use_legacy_sql=false < schema.sql
-- (or Claude will run this via the BigQuery Python client once the service account key is available)

CREATE SCHEMA IF NOT EXISTS `usar_decision_intel`
OPTIONS (location = 'asia-southeast1'); -- Singapore region, closest to Myanmar/Thailand

-- 1. Incidents: collapsed-structure sites reported by Scout teams
CREATE TABLE IF NOT EXISTS `usar_decision_intel.incidents` (
  incident_id             STRING NOT NULL,
  reported_at             TIMESTAMP NOT NULL,
  location                GEOGRAPHY NOT NULL,          -- native spatial field, ST_DISTANCE / ST_DWITHIN ready
  building_material       STRING,                       -- e.g. reinforced_concrete, masonry, wood_frame
  building_stories        INT64,                        -- floor count BEFORE collapse (scout-reported)
  building_use            STRING,                       -- residential | commercial | school | hospital | mixed_use | industrial | government
  collapse_pattern        STRING,                       -- e.g. pancake, lean_to, v_shape, cantilever
  confirmed_trapped_count INT64,                        -- witness/family-confirmed occupants
  estimated_trapped_count INT64,                        -- scout's best-guess estimate
  trapped_count           INT64,                        -- working figure used by the optimizer (max of the two above)
  signs_of_life           STRING,                       -- none_detected | sound_heard | visual_confirmed | canine_alert | family_confirmed_occupants
  hazards_present         ARRAY<STRING>,                -- gas_leak, fire, electrical_hazard, chemical_spill, flooding, unstable_secondary_structure
  access_difficulty       STRING,                       -- clear | partial_debris | heavy_debris_impassable_for_heavy_vehicles
  scout_notes             STRING,                       -- free-text, fed to Gemini
  scout_image_uri         STRING,                       -- GCS path if a photo was attached
  priority_score          FLOAT64,                      -- 0.0-1.0, written by Gemini after evaluation
  priority_rationale      STRING,                       -- Gemini's structured JSON explanation
  golden_hour_deadline    TIMESTAMP,                     -- reported_at + 72h, countdown clock source
  status                  STRING,                       -- reported | triaged | dispatched | in_progress | resolved
  people_saved            INT64,                        -- scout outcome report: rescued alive
  bodies_found            INT64,                        -- scout outcome report: found deceased
  synced_from_offline     BOOL,                          -- true if this row came from the Scout offline queue
  submitted_by            STRING
)
PARTITION BY DATE(reported_at)
-- CLUSTER BY only supports certain column types. priority_score is FLOAT64, which BigQuery
-- rejects for clustering, so we cluster on the two STRING columns queried most often instead.
CLUSTER BY status, building_use;

-- 2. Rescue teams: Heavy/Medium/Light tactical units, gov or NGO affiliated
CREATE TABLE IF NOT EXISTS `usar_decision_intel.rescue_teams` (
  team_id           STRING NOT NULL,
  team_type          STRING,                            -- Heavy | Medium | Light
  org_type           STRING,                            -- national_rescue_dept | ngo (drives map icon)
  current_location   GEOGRAPHY,
  status             STRING,                            -- available(idle) | en_route(assigned) | on_site(operation) | resting
  capabilities        ARRAY<STRING>,                     -- e.g. ["structural_shoring","confined_space","canine"]
  equipment           ARRAY<STRUCT<
    item_name STRING,
    quantity INT64,
    condition STRING                                   -- operational | damaged | missing
  >>                                                     -- nested field: real-time equipment inventory per team
);

-- 2b. Scouts: field personnel as trackable map entities (same status model as rescue_teams)
CREATE TABLE IF NOT EXISTS `usar_decision_intel.scouts` (
  scout_id          STRING NOT NULL,
  current_location   GEOGRAPHY,
  status             STRING,                            -- available(idle) | en_route | on_site(operation) | resting
  home_township      STRING
);

-- 3. Deployments: output of the OR-Tools optimizer
CREATE TABLE IF NOT EXISTS `usar_decision_intel.deployments` (
  deployment_id             STRING NOT NULL,
  incident_id               STRING NOT NULL,
  team_id                   STRING NOT NULL,
  assigned_at                TIMESTAMP,
  estimated_travel_minutes   FLOAT64,
  route                     GEOGRAPHY,                  -- optional LINESTRING for the routing view
  tactical_plan             STRING,                     -- Gemini equipment-substitution output, if triggered
  optimizer_objective_value  FLOAT64
);

-- 4. Road nodes: intersections/endpoints of the routing graph (see optimization/routing_graph.py)
CREATE TABLE IF NOT EXISTS `usar_decision_intel.road_nodes` (
  node_id     STRING NOT NULL,
  location    GEOGRAPHY NOT NULL,
  township    STRING
);

-- 5. Roads: road network edges (paved/unpaved) for travel-time / shortest-path routing.
-- Google's Routes API can't dynamically avoid arbitrary scout-reported blockages, so routing
-- is computed with our own graph (optimization/routing_graph.py) instead of an external API.
CREATE TABLE IF NOT EXISTS `usar_decision_intel.roads` (
  road_id       STRING NOT NULL,
  from_node     STRING NOT NULL,                         -- FK -> road_nodes.node_id
  to_node       STRING NOT NULL,                          -- FK -> road_nodes.node_id
  geometry      GEOGRAPHY,                                -- LINESTRING
  road_type     STRING,                                    -- primary | secondary | track
  surface       STRING,                                    -- paved | unpaved
  distance_km   FLOAT64
);

-- 6. Road status: scout-reported blockages/damage - the live signal that reroutes teams.
CREATE TABLE IF NOT EXISTS `usar_decision_intel.road_status` (
  report_id       STRING NOT NULL,
  road_id         STRING NOT NULL,                         -- FK -> roads.road_id
  status          STRING,                                   -- blocked | damaged
  blockage_type   STRING,                                   -- debris | bridge_collapse | flooding | landslide | checkpoint
  reported_at     TIMESTAMP,
  reported_by     STRING,
  notes           STRING
);

-- 7. Bases: static National Rescue Department + NGO hub locations per township
CREATE TABLE IF NOT EXISTS `usar_decision_intel.bases` (
  base_id     ST