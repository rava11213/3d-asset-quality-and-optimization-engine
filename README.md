# 3D Asset Quality and Optimization Engine

Measure how much smaller an AI-generated mesh can become while preserving its shape.
Stages 1–4 provide C++ mesh inspection, geometry-only OBJ export, optional
normalization, QEM simplification, Python + NVIDIA Warp surface evaluation, and
automated multi-asset benchmarks. The web viewer is the remaining stage. Inspection and
simplification require no Python runtime dependencies; evaluation uses optional extras.

## Build

Requires a C++17 compiler. With CMake 3.16 or later:

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

## Inspect an asset

Export a **triangulated OBJ** from your model tool. Keep the source and its associated
textures together. No Meshy account or API key is required.

```sh
PYTHONPATH=python python3 -m assetq inspect tests/fixtures/tetrahedron.obj --report outputs/tetrahedron-report.json --export outputs/tetrahedron-normalized.obj --normalize
```

Or call C++ directly:

```sh
build/assetq-inspect tests/fixtures/tetrahedron.obj --report outputs/tetrahedron-report.json --export outputs/tetrahedron-copy.obj
```

Create output directories before running. An existing report or export at a distinct
path is replaced; the source cannot be used as an output path. Omit `--normalize`
to retain the original coordinates. Reports always describe the original input.

Python 3.10+ is required for the wrapper. Optionally install with
`python3 -m pip install -e .` to expose `assetq inspect`. The wrapper finds the C++
binary in the source tree's `build/` directory or on PATH; `--binary PATH` selects
another location (including `build/Release/assetq-inspect.exe` for multi-config builds).

## Report semantics

- Vertex and triangle counts include defective faces and unused vertices; nothing is repaired.
- File size is the source OBJ's bytes, excluding textures and material files.
- Bounds include all vertices, including isolated vertices.
- Normalization is `normalized = (original - center) / diagonal`, where center is
  the bounding-box midpoint and diagonal is its Euclidean diagonal length.
  The report records center and reciprocal diagonal as scale. A zero-size mesh can
  be inspected but cannot be normalized.
- Degenerate triangles have double area divided by the squared bounding-box
  diagonal at most `1e-12`; this includes repeated indices and collinear vertices.
- Duplicate triangles use the same three vertex indices, regardless of winding.
- Boundary edges belong to one face; non-manifold edges belong to more than two.
  Repeated edges within one defective face count once. Non-manifold **vertices**,
  self-intersections, holes, and coincident positions with distinct indices are not diagnosed.
- Inconsistent winding counts edges with exactly two incident faces traversing
  the shared edge in the same direction. Defective and duplicate faces contribute
  to topology counts, so these are diagnostics rather than repair instructions.

## OBJ scope

Reads XYZ `v` records and triangle `f` records. Supports positive one-based indices,
negative indices relative to vertices already read, comments, and face tokens such
as `1/2/3` or `1//3`. Only the vertex portion of face tokens is used and validated;
UV and normal references are discarded. Other records are ignored and counted.
Rejects polygons, forward vertex references, zero/out-of-range indices, non-finite
coordinates, and weighted or colored vertices. Output contains only positions and
triangle indices: materials, UVs, normals, groups, and textures are not retained.
The original file remains the source of truth for later attribute-preserving work.

## Verify

After building:

```sh
python3 -m unittest discover -s tests -v
```

Set `ASSETQ_BINARY` to test a binary outside `build/assetq-inspect`. Tests cover known
geometry counts, malformed input, negative/slash indices, source preservation,
normalization round trips, zero-size meshes, and the Python entry point.
Simplification tests additionally cover actual reductions, closed topology, boundary
preservation, scale/translation, deterministic geometry, rejected inputs, unattainable
budgets, and the Python budget sweep. Set `ASSETQ_SIMPLIFY_BINARY` to select another
simplifier executable.

## Simplify an asset (Stage 2)

Generate independent versions retaining 75%, 50%, and 25% of input triangles:

```sh
PYTHONPATH=python python3 -m assetq simplify your-model.obj --output-dir outputs/your-model
```

Customize the fractions with `--retain 0.8 0.4`. The wrapper rounds targets down,
clamps them to at least one triangle, and deduplicates equal targets. Each version
starts from the original mesh. It saves `mesh-N.obj`, `mesh-N.json`, an original
inspection report, and `summary.json`. Paths already present in that directory may
be replaced; use a separate directory for each experiment. `--binary` selects the
simplifier and `--inspector` selects the inspector.

For one exact integer target, use C++ directly:

```sh
build/assetq-simplify your-model.obj --target 1000 --export outputs/simplified.obj --report outputs/simplification.json
```

The C++ optimizer:

1. Centers and scales a working copy to unit bounding-box diagonal for numerical stability.
2. Builds face incidence, validates manifold connectivity and winding, and accumulates
   unit face-plane outer products into each vertex's 4x4 quadric.
3. Sums endpoint quadrics for each edge. Solves the 3x3 optimal-position system
   with pivoting; compares that candidate with endpoints and midpoint. Singular
   systems use those fallback positions.
4. Orders edges in a min-cost priority queue. Revision stamps discard stale entries;
   local connectivity and neighboring candidate costs are refreshed after each collapse.
5. Rejects collapses violating the edge link condition, producing duplicate or
   near-zero-area faces, or changing a surviving face normal by at least `acos(0.2)`
   (about 78 degrees). The normal threshold applies per collapse, not cumulatively.
6. Compacts surviving vertices/faces and restores original coordinates for export.

Initial inputs must have non-degenerate, non-duplicate triangles, consistently wound
manifold edges, and connected face fans at each vertex. Disconnected components are
allowed if each component satisfies these checks. Inspection can diagnose inputs
the simplifier refuses; automatic repair is not implemented. Isolated vertices
are removed during compaction.

All boundary vertices are locked, including their incident candidate edges. This
preserves open boundaries conservatively and can limit reductions. Closed interior
edge collapses remove two triangles; odd targets may therefore stop one triangle
above the budget. The optimizer never goes below the requested triangle count.
Read `target_reached`, `achieved_triangles`, and `stop_reason` in each report.

The report also includes collapse/rejection counts, triangle reduction, OBJ file
sizes, and optimizer wall time (validation, initialization, and compaction included;
file loading/export excluded). Text OBJ size is affected by numeric formatting,
so triangle reduction does not imply an identical byte-size reduction.

QEM cost guides local edge selection; it is **not a measured surface-error score**.
There is no self-intersection detection, texture/UV preservation, or explicit sharp
crease constraint yet. Only the lowest-QEM position is tested for an edge; a rejected
position may leave other geometrically valid choices unexplored. Boundary locks and
validation favor conservative results over reaching every requested budget. Large
production assets have not yet been benchmarked.

## Next stages

1. Local viewer: synchronized comparisons and selection by an error threshold.

## Evaluate shape preservation (Stage 3)

Install optional dependencies in an isolated environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[evaluation]'
```

Evaluate the downloaded Armadillo and all three exported versions:

```sh
PYTHONPATH=python .venv/bin/python -m assetq evaluate outputs/sample-model/armadillo.obj outputs/armadillo-results/mesh-64861.obj outputs/armadillo-results/mesh-43241.obj outputs/armadillo-results/mesh-21620.obj --report outputs/armadillo-results/evaluation.json
```

Names encode the requested budget; read the report for the achieved triangle count.
This command uses 100,000 samples **per direction per candidate**, seed 42, and
Warp's CPU backend. Select a different count/seed with `--samples`/`--seed`.
On a supported NVIDIA machine, `--device cuda:0` selects CUDA. macOS uses CPU;
no Metal backend is assumed. Tested locally with Warp 1.18.0 and NumPy 2.5.4.

Original-to-simplified samples estimate how well the candidate covers the original;
reverse samples detect candidate surface displaced from the original. Points are
sampled uniformly by surface area (area-weighted face choice, square-root barycentric
sampling). Each point queries the target triangle surface with Warp's unsigned
closest-point query, rather than searching target vertices or another point cloud.
No alignment or independent candidate normalization is performed. Both meshes share
the original bounding-box center and diagonal, improving float32 query precision.

The JSON records mean, RMS, P95, P99, and sampled maximum for each direction and
an equally weighted pooled distribution. `normalized` values are fractions of the
original diagonal; multiply by 100 to express percentages. `original_units` scales
distances back to the OBJ's unspecified coordinate units. For conservative later
threshold selection, `worst_direction_p95_normalized` is also recorded. It can differ
from pooled P95. Percentiles use NumPy's linear method.

Hashes, source paths/counts, seed, library versions, device, and elapsed time are
recorded for reproducibility. Time includes loading, sampling, BVH construction,
queries, and first-use compilation, but excludes report serialization. Original
sample points are identical across candidates for a fixed seed and input; target
samples depend on the target surface. JIT cache defaults to `work/warp-cache`,
overridable with `ASSETQ_WARP_CACHE`.

Inputs must be finite triangle OBJs without near-degenerate triangles. Open surfaces
are supported: unsigned distances do not require watertightness. Invalid geometry
and failed closest-point queries raise errors instead of silently dropping samples.
The evaluation OBJ parser has the same geometry-only subset as the inspector;
UVs/materials/normals are ignored. All outputs preserve source meshes.

Sampled metrics are estimates, especially for small features; increase sample counts
or repeat seeds for sensitive comparisons. Sampled maximum is **not exact Hausdorff
distance**. Float32 introduces a numerical floor, even for identical meshes. These
metrics describe surface position, not texture, shading, topology, or self-intersections.

Run the full suite with evaluation dependencies enabled:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Evaluation tests use analytic parallel triangles with known distance, identical
surfaces, asymmetric coverage, known sampling-area ratios, scale invariance,
reproducible seeds, invalid inputs, and CLI source protection. Without optional
dependencies, evaluation tests skip while Stage 1/2 tests still run.

Warp references: [installation](https://github.com/NVIDIA/warp/blob/main/docs/user_guide/installation.rst),
[closest-point query](https://nvidia.github.io/warp/v1.17/language_reference/_generated/warp.mesh_query_point_no_sign.html).

## Automated benchmarks (Stage 4)

Install the benchmark extra (adds Matplotlib for static plots):

```sh
.venv/bin/python -m pip install -e '.[benchmark]'
```

Run multiple inputs, independent triangle budgets, and repeat evaluation seeds:

```sh
PYTHONPATH=python .venv/bin/python -m assetq benchmark outputs/sample-model/armadillo.obj outputs/qem-demo/original.obj tests/fixtures/tetrahedron.obj --output-dir outputs/my-benchmark --retain 0.75 0.5 0.25 --samples 100000 --seeds 42 43 --max-p95 0.0005
```

Choose a **new or empty output directory** for every run. Defaults are 75%, 50%,
25% retention, 100,000 samples per direction, seed 42, and CPU evaluation.
`--binary`, `--inspector`, and `--device` select executables and the Warp backend.
Repeated retention targets and seeds are deduplicated. Asset directory prefixes
are numbered to distinguish identical filenames from different directories.

Each asset is inspected, simplified from its original at each budget, inspected
again after simplification, and evaluated at every requested seed. Failed inputs,
budgets, and evaluation seeds are recorded while remaining experiments continue.
Exit status is 0 for a completed run and 1 if any experiment fails. A valid optimizer
result stopping above its budget is recorded as a successful measurement, with
`target_reached=false`; actual triangle reduction determines its plotted position.

Run outputs:

- `benchmark.json`: configuration, source/executable SHA-256 hashes, environment,
  per-seed rows, budget summaries, selections, and explicit failures. Checkpointed
  after each asset inspection failure or completed budget; final status is
  `complete` or `partial_failure`. A terminated run may retain `running` status.
- `results.csv`: one row per successful asset/budget/seed, with pooled mean/RMS/
  P95/P99/sampled maximum, each directional P95, larger directional P95, original
  and achieved triangle counts, reduction fractions, file sizes, and timings.
- `tradeoffs.png` / `tradeoffs.svg`: static plots of actual triangle reduction
  versus the maximum across seeds of the larger directional P95.
- `report.md`: readable comparison table, chart, failure summary, and optional
  threshold selections.
- Per-asset directories: original inspection, exported meshes, optimizer reports,
  candidate inspections, per-seed full evaluation reports, and command logs.

`--max-p95` is optional and takes a **fraction of original diagonal**:
`0.0005` means **0.05%**, not 0.0005%. Selection requires the larger directional
P95 to meet this threshold for **every requested seed**, then chooses the lowest
achieved triangle count. Partially evaluated budgets cannot qualify. If no tested
version passes, the selection is null. The original is not inserted as an implicit
fallback. This selects among tested versions; it does not search untested budgets.

Charts exclude budgets with failed seeds. Table rows can show partial evaluations
and explicitly flag completeness. Connected plot points do not imply monotonic
error or establish measurements between budgets. All numerical errors in CSV/JSON
are normalized fractions; chart/report errors are percentages. Unlike Stage 3's
pooled P95 summary, threshold selection uses the more conservative **worst-direction,
worst-seed P95**. Mean pooled errors in budget summaries average the requested seeds.

Optimizer time is measured once per budget and repeated in its per-seed CSV rows;
do not sum that column across seeds. Evaluation times include JIT compilation on
first use and are not warmed-up kernel benchmarks. Overall elapsed time covers
experiments but excludes final chart/report rendering. Asset processing is sequential
for predictable resource use. No resume or automatic repair is implemented.

`.venv/bin/python -m unittest discover -s tests -v` also checks complete benchmark
artifacts, partial/all-asset failure reporting, nonempty-directory protection,
and selection behavior when a seed is missing or a requested budget is unattainable.
