"""
Custom road-network routing engine.

Google's Routes API can only bias away from broad categories (tolls, highways, ferries) - it has
no supported way to dynamically avoid a specific scout-reported blocked road. So this builds a
real graph from data/road_nodes.csv + data/roads.csv (a connected local grid per township plus an
inter-town highway backbone) and runs Dijkstra shortest-path over it. Blocked roads
(data/road_status.csv, plus anything reported live from the Streamlit app this session) are
removed as edges, so every route recalculates around them automatically - something a fixed
external routing API cannot do for arbitrary points.

At national scale this exact workload (many-to-many shortest paths over a road graph) is what
NVIDIA's cuGraph (part of the RAPIDS family, same acceleration story as the cudf.pandas
benchmark elsewhere in this repo) is built to accelerate on GPU.
"""

import math
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

PAVED_KMH = 45
UNPAVED_KMH = 20
DAMAGED_SLOWDOWN = 3  # damaged-but-passable roads take 3x longer (partial debris clearance)


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def load_road_network(nodes_path=None, roads_path=None, road_status_path=None, extra_blocked_road_ids=None):
    """Builds a NetworkX graph of the road network with current blockages applied.

    extra_blocked_road_ids: an iterable of road_ids to treat as blocked *in addition to*
    data/road_status.csv - this is how the Streamlit app feeds in blockages a scout reported
    live this session, without needing to rewrite the CSV.
    """
    nodes_path = nodes_path or DATA_DIR / "road_nodes.csv"
    roads_path = roads_path or DATA_DIR / "roads.csv"
    road_status_path = road_status_path or DATA_DIR / "road_status.csv"
    extra_blocked_road_ids = set(extra_blocked_road_ids or [])

    nodes = pd.read_csv(nodes_path)
    roads = pd.read_csv(roads_path)
    try:
        status = pd.read_csv(road_status_path)
    except FileNotFoundError:
        status = pd.DataFrame(columns=["road_id", "status"])

    blocked_ids = set(status.loc[status["status"] == "blocked", "road_id"]) | extra_blocked_road_ids
    damaged_ids = set(status.loc[status["status"] == "damaged", "road_id"]) - blocked_ids

    G = nx.Graph()
    for _, n in nodes.iterrows():
        G.add_node(n["node_id"], lat=n["lat"], lon=n["lon"], township=n["township"])

    n_blocked_skipped = 0
    for _, r in roads.iterrows():
        if r["road_id"] in blocked_ids:
            n_blocked_skipped += 1
            continue  # blocked road -> not traversable, simply omit the edge
        speed = PAVED_KMH if r["surface"] == "paved" else UNPAVED_KMH
        travel_time_min = (r["distance_km"] / speed) * 60
        if r["road_id"] in damaged_ids:
            travel_time_min *= DAMAGED_SLOWDOWN
        G.add_edge(
            r["from_node"], r["to_node"],
            road_id=r["road_id"], distance_km=r["distance_km"],
            travel_time_min=travel_time_min, surface=r["surface"],
            damaged=r["road_id"] in damaged_ids,
        )

    G.graph["n_blocked_removed"] = n_blocked_skipped
    return G, nodes


def nearest_node(nodes_df, lat, lon):
    """Vectorized nearest-node snap for a single point."""
    lats, lons = nodes_df["lat"].to_numpy(), nodes_df["lon"].to_numpy()
    p1, p2 = np.radians(lat), np.radians(lats)
    dphi = np.radians(lats - lat)
    dlambda = np.radians(lons - lon)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlambda / 2) ** 2
    dist_km = 2 * 6371.0 * np.arcsin(np.sqrt(a))
    return nodes_df.iloc[int(np.argmin(dist_km))]["node_id"]


def travel_time_matrix(G, nodes_df, origins: pd.DataFrame, destinations: pd.DataFrame):
    """Efficient many-to-many travel time (minutes). origins/destinations need lat/lon columns.

    Runs one Dijkstra per unique *origin* node (not one per origin x destination pair) - for
    ~50 teams x ~60 incidents that's ~50 graph traversals instead of ~3,000 individual shortest
    path calls. Returns a (len(origins) x len(destinations)) numpy array; unreachable pairs
    (fully cut off by blockages) are np.inf.
    """
    origin_nodes = [nearest_node(nodes_df, r.lat, r.lon) for r in origins.itertuples()]
    dest_nodes = [nearest_node(nodes_df, r.lat, r.lon) for r in destinations.itertuples()]

    matrix = np.full((len(origins), len(destinations)), np.inf)
    dijkstra_cache = {}
    for i, onode in enumerate(origin_nodes):
        if onode not in dijkstra_cache:
            dijkstra_cache[onode] = nx.single_source_dijkstra_path_length(G, onode, weight="travel_time_min")
        distances = dijkstra_cache[onode]
        for j, dnode in enumerate(dest_nodes):
            if dnode in distances:
                matrix[i, j] = distances[dnode]
    return matrix


def route(G, nodes_df, lat1, lon1, lat2, lon2):
    """Full route between two arbitrary points (for displaying a single assignment's path).
    Returns None if no path exists (destination fully cut off by blockages)."""
    n1 = nearest_node(nodes_df, lat1, lon1)
    n2 = nearest_node(nodes_df, lat2, lon2)
    try:
        path = nx.shortest_path(G, n1, n2, weight="travel_time_min")
        travel_minutes = nx.shortest_path_length(G, n1, n2, weight="travel_time_min")
    except nx.NetworkXNoPath:
        return None

    distance_km = sum(G[path[k]][path[k + 1]]["distance_km"] for k in range(len(path) - 1))
    polyline = [[lat1, lon1]] + [[G.nodes[n]["lat"], G.nodes[n]["lon"]] for n in path] + [[lat2, lon2]]
    return {
        "travel_minutes": round(travel_minutes, 1),
        "distance_km": round(distance_km, 2),
        "polyline": polyline,
        "n_road_segments": len(path) - 1,
    }


if __name__ == "__main__":
    G, nodes_df = load_road_network()
    print(f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} usable edges "
          f"({G.graph['n_blocked_removed']} blocked roads removed)")

    result = route(G, nodes_df, 16.84, 96.17, 21.96, 96.09)
    if result:
        print(f"Sample route (Yangon area -> Mandalay area): {result['travel_minutes']:.0f} min, "
              f"{result['distance_km']:.1f} km, {result['n_road_segments']} segments")
    else:
        print("No path found (fully blocked)")
