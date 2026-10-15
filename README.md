# 3D Asset Quality and Optimization Engine

A geometry-processing tool that answers: **How much smaller can we make a 3D model while preserving its shape?**

The intended workflow starts with a model generated in **Meshy AI**, simplifies its triangle mesh using a custom **C++ Quadric Error Metrics (QEM)** implementation, and measures geometric changes using **NVIDIA Warp**. Reports compare triangle reduction, file size, runtime, and surface error across multiple triangle budgets.

## Methods and technologies

| Component | Method / technology | Role |
|---|---|---|
| Asset creation | Meshy AI, manual OBJ export | Supply AI-generated meshes for optimization |
| Mesh inspection | C++17 | Validate input and report counts, bounds, and geometry issues |
| Mesh simplification | Custom C++ QEM edge collapse | Reduce triangles while minimizing accumulated plane error |
| Surface evaluation | Python, NumPy, NVIDIA Warp | Sample surfaces and compute bidirectional closest-point distances |
| Benchmarking | Python and Matplotlib | Compare assets, budgets, and sampling seeds; generate tables and plots |

Meshy AI is an upstream source of assets: this repository does **not** call the Meshy API or generate models itself. Manually exported triangulated OBJ files are accepted, including models from other tools. The current reference experiments use a downloaded Stanford Armadillo model and synthetic meshes; they do not establish performance across Meshy-generated assets.

### QEM simplification

For each triangle, the optimizer computes a unit face plane and accumulates its outer product into the quadrics of its vertices. An edge's combined quadric estimates the cost of replacing its endpoints with a single vertex:

```text
Q_edge = Q_u + Q_v
cost(p) = [p.x, p.y, p.z, 1] × Q_edge × [p.x, p.y, p.z, 1]^T
```

The implementation solves the corresponding 3×3 system for a candidate position and compares its cost with the endpoints and midpoint. Singular systems use those fallback positions. A priority queue selects low-cost edges; revision stamps discard stale entries as local connectivity changes.

Before accepting a collapse, the optimizer checks the edge link condition, duplicate faces, near-zero triangle areas, and changes to surviving face normals. All boundary vertices are locked. Working coordinates are centered and scaled to a unit bounding-box diagonal for numerical stability, then restored for export.

Each budget starts from the original mesh. Budgets default to retaining 75%, 50%, and 25% of the original triangles. Invalid collapses are rejected, so some targets remain unattainable. Reports record the actual achieved count and stop reason; the optimizer never removes more triangles than the requested budget permits.

### NVIDIA Warp evaluation

The evaluator samples points uniformly by surface area using area-weighted triangle selection and square-root barycentric sampling. NVIDIA Warp performs unsigned closest-point queries against the target triangle surface in both directions:

- **Original → simplified:** measures how closely the candidate represents the original surface.
- **Simplified → original:** measures candidate surface displaced from the original.

Both meshes use the original model's center and bounding-box diagonal. Candidates are not independently aligned or rescaled, so displacement and shrinkage contribute to the measured error.

The evaluator reports mean, RMS, P95, P99, and sampled maximum distances for each direction and an equally weighted pooled distribution. Normalized errors are fractions of the original bounding-box diagonal; original-coordinate-unit distances are also included. Fixed seeds make sampling reproducible, with identical original sample points across budgets.

Warp is the geometry-query backend; the QEM optimizer is implemented separately in C++. CPU evaluation is the default and was tested on Apple Silicon. `--device cuda:0` selects CUDA on a supported NVIDIA system.

### Tradeoff selection

Benchmarks compare actual triangle reduction against the **larger directional P95**, taking the maximum across requested seeds. An optional error threshold selects the smallest tested version that passes for every requested seed. This is a sampled geometric tolerance, not an accuracy percentage or a guarantee about untested budgets.

## Build and install

Requires a C++17 compiler and Python 3.10+. With CMake 3.16 or later:

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```

Without CMake, on macOS or Linux:

```sh
mkdir -p build
c++ -std=c++17 -O2 -Wall -Wextra -Wpedantic -Icpp/include cpp/src/mesh.cpp cpp/src/main.cpp -o build/assetq-inspect
c++ -std=c++17 -O2 -Wall -Wextra -Wpedantic -Icpp/include cpp/src/mesh.cpp cpp/src/simplify.cpp cpp/src/simplify_main.cpp -o build/assetq-simplify
```

Install the Python package and evaluation/benchmark dependencies:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[benchmark]'
```

Inspection and simplification wrappers have no runtime Python dependencies. The `evaluation` extra installs NumPy and Warp; `benchmark` also installs Matplotlib. On Windows, activate the virtual environment and use its `python` command instead of `.venv/bin/python`.

The CLI locates C++ executables in `build/` or on PATH. Use `--binary` to select another executable and `--inspector` where supported to select the inspector. Multi-config builds may place executables under `build/Release/`.

## Usage

Run commands from the repository root. Replace `assets/model.obj` with the actual path to your triangulated Meshy export or another OBJ file; it is a placeholder, not a bundled model. Use `tests/fixtures/tetrahedron.obj` for a bundled smoke test, although its four faces cannot be reduced further under the topology checks.

### Inspect

```sh
mkdir -p outputs
.venv/bin/assetq inspect assets/model.obj --report outputs/inspection.json
```

To export a centered mesh with bounding-box diagonal 1, add `--export outputs/normalized.obj --normalize`. Reports always describe the original input. Inspection includes vertex/triangle counts, source file bytes, bounds, degenerate and duplicate triangles, boundary and non-manifold edges, inconsistent winding, and isolated vertices. It diagnoses problems without repairing them.

### Simplify

```sh
.venv/bin/assetq simplify assets/model.obj --output-dir outputs/model-versions --retain 0.75 0.5 0.25
```

This produces `mesh-N.obj`, optimizer JSON reports, an original inspection report, and `summary.json`. `N` is the requested triangle count; achieved counts may differ. Fractional budgets are rounded down, clamped to at least one triangle, and deduplicated.

For a single integer budget:

```sh
build/assetq-simplify assets/model.obj --target 1000 --export outputs/simplified.obj --report outputs/simplification.json
```

### Evaluate

```sh
.venv/bin/assetq evaluate assets/model.obj outputs/simplified.obj --report outputs/evaluation.json --samples 100000 --seed 42
```

Supply multiple simplified OBJ paths to evaluate several versions in one command. Samples are counted **per direction per candidate**. JSON records input hashes, paths, triangle counts, library versions, device, seed, metrics, and elapsed time.

### Benchmark multiple assets

```sh
.venv/bin/assetq benchmark assets/model.obj assets/another-model.obj --output-dir outputs/benchmark-run --retain 0.75 0.5 0.25 --samples 100000 --seeds 42 43 --max-p95 0.0005
```

Choose a new or empty directory for each benchmark. `--max-p95 0.0005` means **0.05% of the original diagonal**. Omit it to run comparisons without selecting a version. Partially evaluated budgets cannot qualify; if none passes, selection is null.

The runner processes assets sequentially, records failures, and continues other experiments. Exit status is 1 if an experiment fails. A valid simplification stopping above its budget is still a successful measurement, marked with `target_reached=false`.

| Output | Contents |
|---|---|
| `benchmark.json` | Configuration, environment, source/executable hashes, per-seed rows, selections, failures |
| `results.csv` | Counts, reductions, file sizes, surface metrics, and timings per asset/budget/seed |
| `tradeoffs.png`, `tradeoffs.svg` | Triangle reduction versus worst-direction, worst-seed P95 |
| `report.md` | Readable comparison table, chart, threshold selections, and failures |
| Per-asset folders | Exported meshes, inspections, optimizer reports, evaluations, and command logs |

CSV/JSON errors are normalized fractions; chart/report errors are percentages. Charts exclude budgets with failed seeds. Connecting points does not establish error between tested budgets or imply monotonic error.

Optimizer time includes validation, initialization, and compaction, but excludes OBJ loading/export. It is repeated in each seed's CSV row, so do not sum it across seeds. Evaluation time includes loading, sampling, BVH construction, queries, and first-use JIT compilation. It is not a warmed-up kernel benchmark.

## Supported inputs and limitations

- Triangle OBJ geometry only: XYZ vertices, positive one-based or negative vertex indices, comments, and slash-form face tokens. UVs, normals, materials, groups, and textures are discarded on export; preserve the source asset.
- Polygons, forward vertex references, weighted/colored vertices, and non-finite coordinates are rejected. OBJ byte counts exclude external textures and material files.
- Simplification requires non-degenerate, non-duplicate triangles, consistently wound manifold edges, and connected face fans at each vertex. Isolated vertices are removed during output compaction. Automatic repair is not implemented.
- Boundary locking limits reduction on open meshes. Only the lowest-QEM candidate position for each edge is validated; other valid positions may remain unexplored. Sharp creases and self-intersections are not explicitly constrained.
- Degeneracy uses double area normalized by squared bounding-box diagonal, with a threshold of `1e-12`. Bounds include all vertices, including isolated ones. Zero-size meshes can be inspected but not normalized.
- Evaluation uses float32 Warp queries and sampled estimates. Sampled maximum is **not exact Hausdorff distance**. Increase sample counts or repeat seeds when assessing small features.
- Surface distances do not measure texture, shading, topology quality, or visual fidelity. QEM cost itself is not a measured surface-error score.
- Text OBJ formatting can increase file size despite triangle reduction. Broad production-asset validation, competing-simplifier baselines, and a web comparison viewer remain future work.

Generated outputs, downloaded models, build artifacts, and the virtual environment are excluded from Git. Inspection/simplification output paths may replace existing files; benchmarks require a new or empty directory. Source mesh paths are protected against direct output aliasing.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests cover inspection, invalid input, normalization, topology checks, simplification budgets, boundary preservation, deterministic exports, analytic surface distances, sampling distributions, seed reproducibility, benchmark failure handling, and threshold selection. Evaluation and benchmark tests skip if their optional dependencies are missing.

Set `ASSETQ_BINARY` or `ASSETQ_SIMPLIFY_BINARY` to test executables outside `build/`. Warp's JIT cache defaults to `work/warp-cache` and can be overridden with `ASSETQ_WARP_CACHE`.

## References

- [Meshy AI](https://www.meshy.ai/) — upstream asset-generation tool; manual export workflow.
- [Surface Simplification Using Quadric Error Metrics, Garland and Heckbert (1997)](https://www.cs.cmu.edu/~garland/quadrics/) — simplification method.
- [NVIDIA Warp](https://github.com/NVIDIA/warp) — geometry-query backend.
- [Warp closest-point query documentation](https://nvidia.github.io/warp/v1.17/language_reference/_generated/warp.mesh_query_point_no_sign.html).
