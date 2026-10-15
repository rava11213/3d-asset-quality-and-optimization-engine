"""Integration checks for actual reduction, topology, budgets and failure modes."""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BINARY = Path(os.environ.get("ASSETQ_SIMPLIFY_BINARY", ROOT / "build/assetq-simplify")).resolve()
INSPECTOR = Path(os.environ.get("ASSETQ_BINARY", ROOT / "build/assetq-inspect")).resolve()


def sphere(levels=2):
    vertices = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
    faces = [(0, 2, 4), (2, 1, 4), (1, 3, 4), (3, 0, 4), (2, 0, 5), (1, 2, 5), (3, 1, 5), (0, 3, 5)]
    for _ in range(levels):
        cache = {}
        def midpoint(a, b):
            key = tuple(sorted((a, b)))
            if key not in cache:
                v = tuple((vertices[a][k] + vertices[b][k]) / 2 for k in range(3))
                norm = math.sqrt(sum(x*x for x in v))
                cache[key] = len(vertices)
                vertices.append(tuple(x / norm for x in v))
            return cache[key]
        refined = []
        for a, b, c in faces:
            ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            refined.extend([(a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)])
        faces = refined
    return vertices, faces


def write_mesh(path, vertices, faces):
    path.write_text("".join(f"v {x:.17g} {y:.17g} {z:.17g}\n" for x, y, z in vertices) +
                    "".join(f"f {a+1} {b+1} {c+1}\n" for a, b, c in faces))


def read_vertices(path):
    return [tuple(map(float, line.split()[1:])) for line in path.read_text().splitlines() if line.startswith("v ")]


class SimplificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / "sphere.obj"
        write_mesh(self.source, *sphere())
        self.output = self.folder / "out.obj"
        self.report = self.folder / "result.json"

    def run_target(self, target, source=None, ok=True):
        result = subprocess.run([str(BINARY), str(source or self.source), "--target", str(target), "--export", str(self.output), "--report", str(self.report)], capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        return json.loads(self.report.read_text()) if ok else result

    def inspect_output(self):
        report = self.folder / "inspection.json"
        subprocess.run([str(INSPECTOR), str(self.output), "--report", str(report)], check=True, capture_output=True)
        return json.loads(report.read_text())

    def test_sphere_reduces_to_all_budgets_with_valid_topology(self):
        before = self.source.read_bytes()
        for target in (96, 64, 32):
            with self.subTest(target=target):
                result = self.run_target(target)
                self.assertTrue(result["target_reached"])
                self.assertEqual(result["achieved_triangles"], target)
                self.assertEqual(result["collapses"], (128-target)//2)
                report = self.inspect_output()
                for key in ("degenerate_triangles", "duplicate_triangles", "boundary_edges", "non_manifold_edges", "inconsistent_winding_edges", "isolated_vertices"):
                    self.assertEqual(report["geometry"][key], 0, key)
                # Closed sphere topology: V - E + F = 2; E = 3F/2.
                self.assertEqual(report["vertex_count"] - target//2, 2)
                for vertex in read_vertices(self.output):
                    radius = math.sqrt(sum(x*x for x in vertex))
                    self.assertGreater(radius, 0.7)
                    self.assertLess(radius, 1.3)
        self.assertEqual(self.source.read_bytes(), before)

    def test_tetrahedron_stops_before_duplicate_faces(self):
        result = self.run_target(2, ROOT / "tests/fixtures/tetrahedron.obj")
        self.assertFalse(result["target_reached"])
        self.assertEqual(result["achieved_triangles"], 4)
        self.assertEqual(result["stop_reason"], "no_valid_collapse_within_budget")

    def test_odd_budget_never_overshoots(self):
        result = self.run_target(63)
        self.assertEqual(result["achieved_triangles"], 64)
        self.assertFalse(result["target_reached"])

    def test_open_grid_preserves_boundary(self):
        n = 7
        vertices = [(float(x), float(y), 0.0) for y in range(n) for x in range(n)]
        faces = []
        for y in range(n-1):
            for x in range(n-1):
                a = y*n+x
                faces.extend([(a, a+1, a+n+1), (a, a+n+1, a+n)])
        write_mesh(self.source, vertices, faces)
        self.run_target(40)
        report = self.inspect_output()
        self.assertLess(report["triangle_count"], len(faces))
        self.assertGreaterEqual(report["triangle_count"], 40)
        self.assertEqual(report["geometry"]["boundary_edges"], 4*(n-1))
        self.assertEqual(report["geometry"]["degenerate_triangles"], 0)
        output = read_vertices(self.output)
        for x, y, z in vertices:
            if x in (0, n-1) or y in (0, n-1):
                self.assertTrue(any(math.dist((x,y,z), p) < 1e-12 for p in output))

    def test_invalid_meshes_fail(self):
        self.run_target(2, ROOT / "tests/fixtures/problems.obj", ok=False)
        vertices, faces = sphere(0)
        faces[0] = tuple(reversed(faces[0]))
        write_mesh(self.source, vertices, faces)
        self.assertIn("winding", self.run_target(4, ok=False).stderr)

    def test_disconnected_vertex_fans_fail(self):
        tetra = [(0,2,1),(0,1,3),(0,3,2),(1,2,3)]
        vertices = [(0,0,0),(1,0,0),(0,1,0),(0,0,1),(-1,0,0),(0,-1,0),(0,0,-1)]
        second = [tuple(0 if x==0 else x+3 for x in face) for face in tetra]
        write_mesh(self.source, vertices, tetra + second)
        self.assertIn("disconnected face fans", self.run_target(4, ok=False).stderr)

    def test_translation_and_scale(self):
        vertices, faces = sphere()
        vertices = [tuple(x*1000 + 10000 for x in v) for v in vertices]
        write_mesh(self.source, vertices, faces)
        self.assertTrue(self.run_target(64)["target_reached"])
        for p in read_vertices(self.output):
            self.assertTrue(all(8500 < x < 11500 for x in p))

    def test_deterministic_and_noop(self):
        self.run_target(64)
        before = self.output.read_bytes()
        self.run_target(64)
        self.assertEqual(self.output.read_bytes(), before)
        result = self.run_target(128)
        self.assertEqual(result["collapses"], 0)
        self.assertTrue(result["target_reached"])

    def test_bad_budgets_and_source_alias(self):
        for target in (0, -1, 129, "2x"):
            self.run_target(target, ok=False)
        before = self.source.read_bytes()
        result = subprocess.run([str(BINARY), str(self.source), "--target", "64", "--export", str(self.source), "--report", str(self.report)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.source.read_bytes(), before)

    def test_python_default_budget_sweep(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "python"))
        directory = self.folder / "versions"
        result = subprocess.run([sys.executable, "-m", "assetq", "simplify", str(self.source), "--output-dir", str(directory), "--binary", str(BINARY), "--inspector", str(INSPECTOR)], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads((directory / "summary.json").read_text())
        self.assertEqual([r["achieved_triangles"] for r in summary["versions"]], [96,64,32])
        for r in summary["versions"]:
            self.assertTrue(Path(r["mesh_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
