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
- Boundary locking limits reduction on open meshes. Only the lowest-QEM candidate position for each edge is validated; other valid positions may remain unexplored. Standard mode has no explicit sharp-crease constraints. Neither mode checks self-intersections.
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

## Experimental feature-aware QEM

Standard QEM remains the default. After rebuilding the C++ simplifier:

```sh
PYTHONPATH=python .venv/bin/python -m assetq simplify outputs/sample-model/armadillo.obj --output-dir outputs/armadillo-feature --method feature --feature-angle 45 --feature-weight 10
```

The C++ executable accepts the same three options. Angle must be in `(0,180)`
degrees and weight in `[0,1000000]`, both finite. Original interior edges whose
adjacent face normals differ by at least the threshold are classified as creases.
Two planes containing each crease and perpendicular to its adjacent faces add
weighted plane quadrics at its endpoints. These penalize movement off the crease.

Feature endpoints/junctions (degree other than two) and chain turns over 30 degrees
are locked as corners. Other feature vertices can collapse only with crease
neighbors, using a position constrained to the current edge segment. Smooth
vertices cannot collapse into a crease. Original boundaries remain locked.
The original crease graph is remapped after collapses, not reclassified from
changing normals. Curved chains can change shape; exact preservation of the entire
original feature geometry is not guaranteed. Edges incident to locked corners
are excluded, which can limit reduction.

Weight zero retains graph/corner/segment constraints but removes the additional
plane penalty, enabling an ablation. With no detected creases, this mode produces
the same geometry as standard QEM. Reports record method, angle, weight, original
crease count, and protected-corner count. Feature-specific tests cover detection,
corner positions, valid topology, no-feature behavior, thresholds, zero weight,
determinism, and invalid options. These establish behavior, not superior quality.

The `benchmark` command defaults to standard QEM; use the comparison options below
to evaluate alternative policies.

## Controlled method comparisons

```sh
PYTHONPATH=python .venv/bin/python -m assetq benchmark outputs/sample-model/armadillo.obj outputs/comparison-inputs/rippled-box.obj outputs/qem-demo/original.obj --output-dir outputs/new-comparison --methods standard constraints feature --retain 0.75 0.5 0.25 --samples 100000 --feature-samples 2000 --seeds 42 43
```

Methods are `standard` (original QEM), `constraints` (feature graph, corner locks,
and segment constraints with **zero** extra feature-plane penalty), `feature`
(same constraints with `--feature-weight`, default 10), and `fast` (optional
external reference). `--feature-angle` defaults to 45 degrees for both optimizer
classification and feature evaluation; using one shared threshold keeps the
definition consistent across methods. Each method/budget starts from the original.
Per-method subdirectories prevent report/export collisions. JSON schema version 2
and CSV include method labels so methods are never pooled together.

Generate the deterministic curved mechanical test shape before using that input:

```sh
PYTHONPATH=python .venv/bin/python -m assetq.examples outputs/comparison-inputs/rippled-box.obj --subdivisions 12
```

New evaluation metrics:

- **Normal deviation:** oriented geometric triangle normals at sampled source
  positions versus the closest target triangle's normal. Reports directional
  mean/RMS/P95/P99/maximum in degrees and the larger directional P95. Angles range
  from 0 to 180; reversed winding is penalized. At creases, ties between equally
  close target faces can affect the normal score. This is nearest-surface normal
  disagreement, not tracked material-point correspondence or shading-normal error.
- **Crease distance:** independently detect interior dihedral creases on original
  and candidate, sample each network uniformly by edge length, and compute nearest
  point-to-segment distances in both directions. Results are normalized by original
  diagonal. The search uses bounded-memory NumPy batches, independent of Warp's
  triangle queries. Original boundaries are excluded. Default 2,000 samples per
  direction; `--feature-samples 0` disables it. Sampling estimates do not guarantee
  coverage of tiny features or semantic correspondence between unrelated creases.
- **Missing features:** no original/candidate creases gives `not_applicable`;
  original-only creases gives `missing_candidate_features`; candidate-only creases
  gives `introduced_candidate_features`. Distances are null/N/A in these cases,
  never a misleading zero. Original and candidate crease counts and lengths are
  retained in the full evaluation JSON. A distance score alone cannot establish
  feature topology preservation.

Standalone `evaluate` accepts `--feature-samples` and `--feature-angle` too; normals
are always measured. The original random surface samples are unchanged by enabling
these metrics. New `method-comparison.png`/`.svg` plots show surface, normal, and
crease errors per asset. All reported budget summaries use the maximum across
requested seeds for P95. Missing feature metrics are omitted from plot curves and
explicitly flagged in tables.

`benchmark.json` includes comparisons against standard QEM **only when achieved
counts match exactly and every requested seed completed**. Mismatched comparisons
retain their counts but numerical deltas are null; there is no interpolation,
automatic count reconciliation, or claim of improvement at mismatched budgets.
Negative deltas favor the alternative. Differences near the float32 numerical
floor should be treated as ties. No single overall winner is inferred: surface,
normal, and crease scores may disagree. Threshold selection still uses surface
P95 only and selects across all tested methods when more than one is requested.
Candidate inspection defects are also included in CSV; low distance alone does
not certify valid topology.

### External reference

Install the optional [fast-simplification](https://github.com/pyvista/fast-simplification)
adapter dependency:

```sh
.venv/bin/python -m pip install -e '.[comparison]'
```

Then include `fast` in `--methods`. The isolated adapter calls the library's
`simplify(..., target_count=..., agg=7.0)` after the same centering/scaling used
locally, restores coordinates, and records library version/parameters. It uses
the reference's own boundary/topology policy, which is not identical to ours.
Reference timing covers the Python library call; local timing also includes our
validation/initialization/compaction. These are not controlled speed comparisons.
The library can overshoot or fail to meet budgets; actual counts remain decisive.
Failed reference runs are recorded and other methods continue. No substitute
result is invented when the dependency is unavailable.

The reference dependency could not be downloaded in the current development
session due to restricted package-index access. Its adapter is implemented but
not yet validated against an installed external library; the published experiment
compares the three local methods only. External superiority is not established.

## Curvature-weighted quadric experiments

Two additional methods are available after rebuilding `assetq-simplify`:

- `curvature`: weighted original face-plane quadrics, with standard topology and
  boundary policies.
- `combined`: curvature weighting plus the feature mode's original crease graph,
  corner protection, segment constraints, and feature-plane quadrics.

For example:

```sh
PYTHONPATH=python .venv/bin/python -m assetq simplify outputs/sample-model/armadillo.obj --output-dir outputs/armadillo-curvature --method curvature --curvature-strength 4
PYTHONPATH=python .venv/bin/python -m assetq benchmark outputs/sample-model/armadillo.obj outputs/comparison-inputs/rippled-box.obj --output-dir outputs/new-curvature-comparison --methods standard feature curvature combined --retain 0.5 0.25 --seeds 42 43 --curvature-strength 4
```

For each original triangle, compute the mean `1 - dot(n_face, n_neighbor)` across
its interior-edge neighbors. This dimensionless normal-variation **proxy** is
tessellation dependent; it is not an exact mean/Gaussian curvature estimator. No
angle deficit is assigned to open boundaries. Variations at or below `1e-15` are
treated as roundoff. Let `r` be the linearly interpolated 95th percentile of the
positive face variations. The original plane quadric receives weight:

```text
weight = 1 + strength * min(variation / r, 1)
```

If every variation is zero, all weights stay one. Strength must be finite and in
`[0,1000]` (default 4), yielding weights in `[1,1+strength]`. Statistics are
calculated once on the original mesh; weighted quadrics are accumulated through
collapses, not recomputed on the simplified surface. Feature-plane penalties are
added separately afterward. Higher weighting gives an original region more
influence over edge costs; it does not guarantee lower measured error there or
globally. Percentile capping reduces dominance by high-variation outliers but can
also compress distinctions between genuinely sharp regions.

`--curvature-strength 0` is an exact ablation: `curvature` reproduces `standard`
and `combined` reproduces `feature`, including deterministic exported geometry.
Standard and feature modes ignore the weighting policy. Reports record the
requested strength, proxy name, positive-variation P95 reference, and minimum,
maximum, and mean original face weight. Weights do not change boundary locking,
normal-turn validation, manifold checks, or target-budget behavior.

For a smooth surface with localized detail, generate a deterministic bumped sphere:

```sh
PYTHONPATH=python .venv/bin/python -m assetq.examples outputs/comparison-inputs/bumped-sphere.obj --shape bumped-sphere
```

This subdivides an octahedron four times, projects to the unit sphere, then adds
two smooth radial bumps. It is a synthetic diagnostic input, not an AI-generated
production asset. Compare original geometry, weights, and multiple metrics before
claiming better detail preservation. A single strength is not an optimized parameter
search; separate held-out assets would be needed to establish generalization.

## Constrained open-boundary reduction

Original boundary vertices stay locked by default. After rebuilding, enable
controlled chain reduction with `--boundary-policy chain` on `simplify` or the
C++ executable. This works with standard, feature, curvature, and combined QEM.

```sh
PYTHONPATH=python .venv/bin/python -m assetq.examples outputs/boundary-inputs/open-hole.obj --shape open-hole
PYTHONPATH=python .venv/bin/python -m assetq simplify outputs/boundary-inputs/open-hole.obj --output-dir outputs/hole-chain --boundary-policy chain
PYTHONPATH=python .venv/bin/python -m assetq benchmark outputs/boundary-inputs/open-hole.obj --output-dir outputs/new-boundary-run --methods standard boundary --retain 0.5 0.25 --seeds 42 43
```

The benchmark alias `boundary` selects standard QEM with chain reduction; other
benchmark methods retain locked boundaries. Other open diagnostic generators are
`open-plane`, `open-strip`, and `open-curved`.

Original boundary turns over 30 degrees are locked as corners. Only neighboring
boundary vertices may collapse together, with their replacement constrained to
the current edge segment. Interior-to-boundary merges are excluded. Original
planes containing open edges and perpendicular to adjacent faces add fixed
weight-10 penalties, **after** curvature weighting. Costs accumulate through
collapses. Corner detection is static, not recomputed after every change. Edges
incident to locked corners are excluded, retaining some extra nearby vertices.

Boundary collapses remove one face; interior collapses remove two. Both obey the
no-overshoot budget rule. The implementation checks edge links, duplicate faces,
degeneracy, normal changes, and complete post-collapse face-fan connectivity and
degrees around touched vertices. It remaps boundary connectivity and refuses to
collapse three-edge loops. Invalid input junctions/fans are rejected by the input
validator. These checks protect local manifold topology, not global intersections.
Curved outlines may shorten or move; there is no continuous outline-error bound.

Reports record policy, original edge count, protected boundary corners, collapse
count, plane weight, and corner threshold. Evaluation with `--feature-samples N`
also samples incidence-one boundary edges by length and computes bidirectional
nearest-segment distances normalized by original diagonal. Closed meshes give N/A;
missing/introduced boundaries are explicitly flagged instead of scored zero.
JSON and CSV include boundary P95/status, policy, collapses, and achieved edge
counts. Comparison plots include a boundary-error panel. Actual triangle counts
can differ between policies; numerical comparison deltas remain null in that case.

Tests cover corner positions, holes/Euler characteristic, separate patches, narrow
strips, curved patches with combined weighting, odd budgets, closed-mesh equivalence,
clean geometry, analytic boundary distances, and benchmark integration. A low
boundary-distance score alone does not establish equal loop topology. Broad
real-world open-asset validation and global self-intersection checks remain future work.

## Optimizer profiling and performance trials

The `performance` command measures separately from geometry-sampling benchmarks:

```sh
PYTHONPATH=python .venv/bin/python -m assetq performance outputs/sample-model/armadillo.obj --output-dir outputs/new-performance-run --retain 0.25 --variants tree sorted --warmups 1 --repeats 5 --build-label 'C++17 -O2; record your actual compiler flags'
```

It uses the local C++ executables and Matplotlib (`pip install -e '.[performance]'`),
without requiring Warp execution. Input paths, binary hashes, compiler version,
platform/architecture, logical CPU count, and user-supplied build label are recorded.
Compiler flags cannot be recovered reliably from an executable; `--build-label`
records supplied provenance rather than verifying how a binary was built.

For each asset/method/budget and refresh variant, run one separately instrumented
diagnostic execution, at least one excluded warm-up, then at least two measured
executions (default five). Measured executions have profiling **off**. Variant
order reverses on alternating iterations. Every execution is a fresh process:
warm-ups warm OS/file caches, not a retained optimizer or allocator. No sampling
seed is involved. `--timeout` bounds each child process (default 300 seconds).

`--methods` supports standard, feature, curvature, combined, and boundary. These
use their default policy weights. `--retain` supports multiple independent budgets;
actual achieved counts are retained. A new or empty output directory is required.
Failures/timeouts are recorded while other trials continue; exit code 1 indicates
partial failure. The runner writes checkpointed JSON, flat trial CSV, a readable
report, runtime/RSS plots, command logs, original/output inspections, and per-case
meshes. Summary statistics include only successful unprofiled measured trials;
incomplete groups show their actual trial counts. A failed case cannot receive a
paired time ratio.

C++ `--profile on` records mesh setup/validation and base quadrics, curvature
weighting, boundary penalties, feature constraints, initial queue construction,
queue popping, collapse validation, mesh updates, queue refresh, and output
compaction. Initial corner classification is included in mesh setup. Stages are
nonoverlapping; constructor allocations, local destruction, control flow, and clock
overhead can remain unaccounted. Fine-grained clocks perturb these diagnostic
executions; do not compare their total against unprofiled performance as a speedup.
Queue pushes, pops, stale pops, and peak entries identify candidate churn. These
are entry counts, not an estimate of allocated bytes. `--profile off` is the default.

All simplifier reports include process peak resident memory where supported:
macOS `getrusage` bytes; Linux KiB converted to bytes; null elsewhere. This is the
child process's lifetime high-water mark, including input/output mesh storage,
runtime, and allocator pages. It is not incremental optimizer-only memory or a
per-stage memory profile. Optimizer time excludes input/output and engine teardown;
the runner also records total child wall time including startup and I/O.

Two refresh implementations allow an isolated comparison: `--queue-refresh tree`
uses the previous temporary ordered set; `sorted` collects edges in a contiguous
vector, sorts them, and removes duplicates. Both enqueue the same lexicographic
edge sequence. The optimization targets refresh allocation/bookkeeping, not the
stale priority-queue policy. The runner checks exact OBJ hashes across every
profile, warm-up, repeat, and variant before accepting results. Profile exports
also pass geometry diagnostics. Identical output bytes establish unchanged
geometry for tested cases without adding sampling noise to the comparison.

Sorted refresh is now the default in both the C++ executable and Python `simplify`.
Use `--queue-refresh tree` to reproduce the previous bookkeeping path. This does
not change QEM costs, collapse checks, boundary policy, or triangle budgets.

Reports show medians, min/max, and interquartile ranges for optimizer time, process
wall time, and RSS. A tree/sorted median-time ratio greater than one favors sorted
refresh, but is not a confidence interval or a claim across hardware/assets.
Hardware load and thermal state are uncontrolled. To study size sensitivity with
a common procedural family, generate bumped spheres with `assetq.examples --shape
bumped-sphere --levels 3` through `--levels 6`. A comparison against Armadillo is a
different asset, not a point proving asymptotic scaling. Report plots use points
without asserting a fitted complexity law.
