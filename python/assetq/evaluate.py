"""Reproducible area-weighted surface evaluation in a shared coordinate frame."""
import hashlib
import json
from pathlib import Path
import time
import numpy as np


def load_obj(path):
    vertices, faces = [], []
    with Path(path).open() as stream:
        for number, line in enumerate(stream, 1):
            tokens = line.partition("#")[0].split()
            if not tokens:
                continue
            try:
                if tokens[0] == "v":
                    if len(tokens) != 4:
                        raise ValueError("Expected XYZ vertex")
                    vertices.append(tuple(map(float, tokens[1:])))
                elif tokens[0] == "f":
                    if len(tokens) != 4:
                        raise ValueError("Expected triangle face")
                    indices = [int(t.split("/")[0]) for t in tokens[1:]]
                    if any(i == 0 for i in indices):
                        raise ValueError("Zero vertex index")
                    face = [i-1 if i > 0 else len(vertices)+i for i in indices]
                    if any(i < 0 or i >= len(vertices) for i in face):
                        raise ValueError("Vertex index out of range")
                    faces.append(face)
            except ValueError as exc:
                raise ValueError(f"{path}: OBJ line {number}: {exc}") from exc
    v, f = np.asarray(vertices, dtype=np.float64), np.asarray(faces, dtype=np.int64)
    if not vertices or not faces or not np.isfinite(v).all():
        raise ValueError(f"{path}: expected finite vertices and triangle faces")
    if len(vertices) > np.iinfo(np.int32).max:
        raise ValueError("Mesh exceeds Warp int32 indexing")
    d = np.linalg.norm(v.max(axis=0)-v.min(axis=0))
    if not np.isfinite(d) or d <= 0:
        raise ValueError(f"{path}: invalid bounding-box diagonal")
    tri = (v - v.min(axis=0)) / d
    tri = tri[f]
    areas2 = np.linalg.norm(np.cross(tri[:,1]-tri[:,0], tri[:,2]-tri[:,0]), axis=1)
    if np.any(areas2 <= 1e-12):
        raise ValueError(f"{path}: degenerate triangles must be repaired before evaluation")
    return v, f


def sample_surface(vertices, faces, count, rng, return_faces=False):
    triangles = vertices[faces]
    areas = np.linalg.norm(np.cross(triangles[:,1]-triangles[:,0], triangles[:,2]-triangles[:,0]), axis=1)
    indices = rng.choice(len(faces), size=count, p=areas/areas.sum())
    chosen = triangles[indices]
    # sqrt maps two uniform random variables to uniform triangle area.
    r = np.sqrt(rng.random(count))
    s = rng.random(count)
    points = ((1-r)[:,None]*chosen[:,0] + (r*(1-s))[:,None]*chosen[:,1] + (r*s)[:,None]*chosen[:,2])
    return (points,indices) if return_faces else points


def statistics(values):
    return {"mean": float(np.mean(values)), "rms": float(np.sqrt(np.mean(values**2))),
            "p95": float(np.percentile(values,95)), "p99": float(np.percentile(values,99)),
            "sampled_max": float(np.max(values))}


def evaluate(original, simplified, samples=100000, seed=42, device="cpu", feature_samples=0, feature_angle=45):
    if samples < 1 or seed < 0:
        raise ValueError("Samples must be positive and seed nonnegative")
    if feature_samples<0:
        raise ValueError("Feature sample count must be nonnegative")
    from .warp_distance import distances, wp
    from .feature_metrics import face_normals, feature_errors
    start = time.perf_counter()
    original, simplified = Path(original), Path(simplified)
    ov, of = load_obj(original)
    sv, sf = load_obj(simplified)
    lo, hi = ov.min(axis=0), ov.max(axis=0)
    diagonal = float(np.linalg.norm(hi-lo))
    center = lo/2 + hi/2
    ov, sv = (ov-center)/diagonal, (sv-center)/diagonal
    # Never independently normalize candidates: displacement and scale changes
    # must count as error. Use identical original samples for every budget.
    a, b = np.random.SeedSequence(seed).spawn(2)
    op,oi = sample_surface(ov, of, samples, np.random.default_rng(a),True)
    sp,si = sample_surface(sv, sf, samples, np.random.default_rng(b),True)
    forward,near_s = distances(op, sv, sf, device,return_faces=True)
    reverse,near_o = distances(sp, ov, of, device,return_faces=True)
    on,sn = face_normals(ov,of),face_normals(sv,sf)
    def normal_stats(a,b):
        return statistics(np.rad2deg(np.arccos(np.clip(np.sum(a*b,axis=1),-1,1))))
    normals = {"original_to_simplified":normal_stats(on[oi],sn[near_s]),
               "simplified_to_original":normal_stats(sn[si],on[near_o])}
    distributions = {"original_to_simplified": forward, "simplified_to_original": reverse,
                     "symmetric_pooled": np.concatenate([forward,reverse])}
    normalized = {key: statistics(v) for key,v in distributions.items()}
    raw = {key: {metric: value*diagonal for metric,value in stats.items()} for key,stats in normalized.items()}
    def source(path, faces):
        return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "triangles": len(faces), "file_size_bytes": path.stat().st_size}
    return {"schema_version": 2, "backend": "nvidia_warp", "warp_version": wp.__version__,
            "numpy_version": np.__version__, "device": str(wp.get_device(device)),
            "samples_per_direction": samples, "seed": seed, "sampling": "area_weighted_uniform",
            "percentile_method": "linear", "distance": "unsigned_point_to_triangle_surface",
            "original": source(original,of), "simplified": source(simplified,sf),
            "original_bounding_box_diagonal": diagonal, "normalization_center": center.tolist(),
            "normalized": normalized, "original_units": raw,
            "normal_deviation_degrees":normals,
            "worst_direction_normal_p95_degrees":max(s["p95"] for s in normals.values()),
            "feature_metrics":feature_errors(ov,of,sv,sf,feature_samples,seed,feature_angle) if feature_samples else None,
            "boundary_metrics":feature_errors(ov,of,sv,sf,feature_samples,seed,feature_angle,True) if feature_samples else None,
            "worst_direction_p95_normalized": max(normalized[k]["p95"] for k in ("original_to_simplified","simplified_to_original")),
            "elapsed_seconds": time.perf_counter()-start,
            "limitations": ["Sampled estimates; sampled_max is not exact Hausdorff distance",
                            "Geometry only; excludes appearance and topology quality", "Warp queries use float32"]}


def run_cli(args):
    sources = [args.original, *args.simplified]
    if any(args.report.resolve() == p.resolve() or (args.report.exists() and args.report.samefile(p)) for p in sources):
        raise ValueError("Report path must differ from all input meshes")
    reports = [evaluate(args.original,p,args.samples,args.seed,args.device,args.feature_samples,args.feature_angle) for p in args.simplified]
    report = {"schema_version": 1, "evaluations": reports}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    for r in reports:
        stats = r["normalized"]["symmetric_pooled"]
        print(f"{r['simplified']['triangles']:,} triangles | mean {stats['mean']:.6g} | P95 {stats['p95']:.6g} | P99 {stats['p99']:.6g} (fractions of original diagonal)")
    print(f"Evaluation: {args.report}")
    return 0
