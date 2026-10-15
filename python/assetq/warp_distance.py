"""Unsigned point-to-triangle queries; imported only by the evaluation command."""
import os
from pathlib import Path
import numpy as np
import warp as wp

# Keep JIT artifacts inside the project, rather than requiring a user-cache write.
wp.config.kernel_cache_dir = os.environ.get("ASSETQ_WARP_CACHE", str(Path(__file__).resolve().parents[2] / "work/warp-cache"))


@wp.kernel
def closest_distances(mesh: wp.uint64, points: wp.array(dtype=wp.vec3),
                      max_distance: float, distances: wp.array(dtype=float), faces: wp.array(dtype=int)):
    i = wp.tid()
    query = wp.mesh_query_point_no_sign(mesh, points[i], max_distance)
    if query.result:
        nearest = wp.mesh_eval_position(mesh, query.face, query.u, query.v)
        distances[i] = wp.length(points[i] - nearest)
        faces[i] = query.face
    else:
        distances[i] = -1.0
        faces[i] = -1


def distances(points, vertices, faces, device="cpu", return_faces=False):
    wp.init()
    with wp.ScopedDevice(device):
        mesh = wp.Mesh(points=wp.array(vertices.astype(np.float32), dtype=wp.vec3),
                       indices=wp.array(faces.astype(np.int32).ravel(), dtype=wp.int32))
        samples = wp.array(points.astype(np.float32), dtype=wp.vec3)
        out = wp.empty(len(points), dtype=float)
        out_faces = wp.empty(len(points), dtype=int)
        # A conservative bound encompassing both sample and target positions.
        lo = np.minimum(points.min(axis=0), vertices.min(axis=0))
        hi = np.maximum(points.max(axis=0), vertices.max(axis=0))
        bound = float(np.linalg.norm(hi - lo) * 2 + 1)
        wp.launch(closest_distances, dim=len(points), inputs=[mesh.id, samples, bound], outputs=[out,out_faces])
        result = out.numpy().astype(np.float64)
        closest_faces = out_faces.numpy()
    if not np.isfinite(result).all() or np.any(result < 0):
        raise ValueError("Warp closest-point query failed; no valid distance was returned")
    return (result, closest_faces) if return_faces else result
