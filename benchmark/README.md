# CPU vs GPU (NVIDIA RAPIDS cudf.pandas) Benchmark

Required NVIDIA acceleration baseline for the hackathon: how much faster the incident x
rescue-team distance-matrix computation runs on GPU (via `cudf.pandas`) versus plain CPU
pandas, at the full ~10,500 incident x 120 team scale used throughout this project.

This machine has no GPU, so the benchmark is designed to run on **Google Colab** instead.

## Steps

1. Open a new Colab notebook.
2. **Runtime -> Change runtime type -> T4 GPU** (or any CUDA GPU).
3. Install RAPIDS cudf:
   ```
   !pip install cudf-cu12 --extra-index-url=https://pypi.nvidia.com
   ```
4. Upload this project (or just `data/incidents.csv`, `data/rescue_teams.csv`, and
   `benchmark/cpu_vs_gpu_benchmark.py`, keeping the same folder structure) to the Colab
   filesystem, e.g. via the Files sidebar or `git clone` of your GitHub repo.
5. Run the **CPU baseline** first:
   ```
   !python benchmark/cpu_vs_gpu_benchmark.py
   ```
6. Then run the **GPU (RAPIDS) version** of the exact same script:
   ```
   !python -m cudf.pandas benchmark/cpu_vs_gpu_benchmark.py
   ```
   `python -m cudf.pandas` is what activates the GPU acceleration - it monkey-patches the
   `pandas` module itself, so `cpu_vs_gpu_benchmark.py` doesn't need (and doesn't have) any
   GPU-specific code. Same function, same file, two different accelerations.
7. Both runs write into the same `benchmark/benchmark_result.json` (CPU and GPU results are
   merged, not overwritten) and the script prints the speedup once both are present.
8. Download `benchmark/benchmark_result.json` from Colab and place it in this same folder in
   your local project. The Streamlit dashboard's "Time to Insight" panel reads this file
   automatically on next launch and shows the real GPU number + speedup instead of the
   "run on Colab" placeholder.

## Why this matters for judging

`cudf.pandas` is the RAPIDS "zero code change" acceleration path - the same pandas code that
already powers the app's routing/optimization data prep is what gets accelerated, not a
separate GPU-only rewrite. That's the point being demonstrated here: the exact same
`haversine_matrix()` function in `cpu_vs_gpu_benchmark.py` runs on both CPU and GPU.
