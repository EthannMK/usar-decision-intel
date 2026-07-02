"""
CPU (pandas) vs GPU (cudf.pandas) benchmark for the USAR platform's "Time to Insight" panel.

This computes the exact same incident x rescue-team haversine distance matrix as the Streamlit
Command Center's "Run CPU benchmark now" button (app/streamlit_app.py), at full scale - 10,500
synthetic incidents x 120 rescue teams (1.26M pairs) over the Sagaing region hybrid dataset.
This is the hackathon's required NVIDIA RAPIDS acceleration baseline: "Time to Insight" for
processing a massive distance matrix, CPU vs GPU.

cudf.pandas (RAPIDS) accelerates pandas transparently with ZERO code changes: run this exact
script through `python -m cudf.pandas` on a CUDA GPU and every pandas/numpy call underneath is
routed to the GPU automatically. There is no separate "GPU code path" in this file - that's the
whole point of cudf.pandas, and why the same haversine_matrix() function below is used for both
runs unmodified.

This machine/sandbox has no GPU, so this script is written to run on Google Colab instead:

  1. Runtime -> Change runtime type -> T4 GPU (or better)
  2. !pip install cudf-cu12 --extra-index-url=https://pypi.nvidia.com
  3. Upload this repo (or just data/incidents.csv + data/rescue_teams.csv + this file, keeping
     the benchmark/ and data/ folder structure)
  4. CPU baseline:     !python benchmark/cpu_vs_gpu_benchmark.py
  5. GPU (RAPIDS):     !python -m cudf.pandas benchmark/cpu_vs_gpu_benchmark.py
  6. Download benchmark/benchmark_result.json (it accumulates both runs into one file - run
     both commands before downloading) and drop it into your local benchmark/ folder. The
     Streamlit dashboard reads it automatically on next launch and replaces the "run on Colab"
     placeholder with the real GPU number and the CPU-vs-GPU speedup.

NOTE: written and tested for correctness/timing logic on CPU (this sandbox has no GPU to
execution-test the cudf.pandas path against) - if cudf.pandas behaves unexpectedly on Colab,
paste the output and we'll fix it together.
"""

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RESULT_PATH = Path(__file__).resolve().parent / "benchmark_result.json"


def haversine_matrix(lat_i, lon_i, lat_t, lon_t):
    """Vectorized haversine distance matrix (n_incidents x n_teams) using only pandas/numpy
    ops - the exact computation cudf.pandas is designed to accelerate transparently, and the
    same one app/streamlit_app.py's live CPU benchmark button runs."""
    lat_i = lat_i.to_numpy()[:, None]
    lon_i = lon_i.to_numpy()[:, None]
    lat_t = lat_t.to_numpy()[None, :]
    lon_t = lon_t.to_numpy()[None, :]
    p1, p2 = np.radians(lat_i), np.radians(lat_t)
    dphi = np.radians(lat_t - lat_i)
    dlambda = np.radians(lon_t - lon_i)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlambda / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))


def run_benchmark():
    incidents = pd.read_csv(DATA_DIR / "incidents.csv")
    teams = pd.read_csv(DATA_DIR / "rescue_teams.csv")

    # cudf.pandas.install() (triggered by `python -m cudf.pandas`) replaces the pandas module
    # itself with a GPU-backed drop-in, so this reports whether THIS invocation actually ran
    # GPU-accelerated or the plain CPU baseline, by checking what `incidents` really is.
    is_gpu = "cudf" in type(incidents).__module__

    t0 = time.perf_counter()
    matrix = haversine_matrix(incidents["lat"], incidents["lon"], teams["lat"], teams["lon"])
    elapsed = time.perf_counter() - t0

    result = {
        "mode": "GPU (cudf.pandas)" if is_gpu else "CPU (pandas)",
        "is_gpu": is_gpu,
        "n_incidents": len(incidents),
        "n_teams": len(teams),
        "n_pairs": len(incidents) * len(teams),
        "elapsed_seconds": elapsed,
        "pandas_module": type(incidents).__module__,
        "matrix_shape": list(matrix.shape),
    }
    print(f"[{result['mode']}] {result['n_incidents']:,} incidents x {result['n_teams']:,} teams "
          f"= {result['n_pairs']:,} pairs computed in {elapsed * 1000:.1f} ms")

    # Merge into the shared result file instead of overwriting, so a CPU run and a GPU run
    # (two separate invocations - see module docstring) both land in one file for comparison.
    existing = {}
    if RESULT_PATH.exists():
        try:
            existing = json.loads(RESULT_PATH.read_text())
        except json.JSONDecodeError:
            existing = {}
    existing["gpu" if is_gpu else "cpu"] = result
    RESULT_PATH.write_text(json.dumps(existing, indent=2))
    print(f"Saved to {RESULT_PATH}")

    if "cpu" in existing and "gpu" in existing:
        cpu_s = existing["cpu"]["elapsed_seconds"]
        gpu_s = existing["gpu"]["elapsed_seconds"]
        if gpu_s > 0:
            print(f"\nSpeedup: {cpu_s / gpu_s:.1f}x faster on GPU (cudf.pandas) vs CPU (pandas)")


if __name__ == "__main__":
    run_benchmark()
