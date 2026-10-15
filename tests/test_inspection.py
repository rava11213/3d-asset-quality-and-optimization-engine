import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BINARY = Path(os.environ.get("ASSETQ_BINARY", ROOT / "build/assetq-inspect")).resolve()


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.report = self.folder / "report.json"

    def run_mesh(self, source, *options, ok=True):
        result = subprocess.run([str(BINARY), str(source), "--report", str(self.report), *map(str, options)], capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        return json.loads(self.report.read_text()) if ok else result

    def source(self, text):
        path = self.folder / "input.obj"
        path.write_text(text)
        return path

    def test_closed_mesh(self):
        source = ROOT / "tests/fixtures/tetrahedron.obj"
        report = self.run_mesh(source)
        self.assertEqual(report["vertex_count"], 4)
        self.assertEqual(report["triangle_count"], 4)
        self.assertEqual(report["file_size_bytes"], source.stat().st_size)
        self.assertAlmostEqual(report["bounding_box_diagonal"], math.sqrt(3))
        for key in ("degenerate_triangles", "duplicate_triangles", "boundary_edges", "non_manifold_edges", "inconsistent_winding_edges", "isolated_vertices"):
            self.assertEqual(report["geometry"][key], 0, key)

    def test_known_defects(self):
        report = self.run_mesh(ROOT / "tests/fixtures/problems.obj")
        for key, count in {"degenerate_triangles": 1, "duplicate_triangles": 1, "boundary_edges": 4, "non_manifold_edges": 1, "inconsistent_winding_edges": 2, "isolated_vertices": 1}.items():
            self.assertEqual(report["geometry"][key], count, key)

    def test_negative_indices_and_slash_tokens(self):
        report = self.run_mesh(self.source("v 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nf -3/1 -2/1 -1/1 # triangle\n"))
        self.assertEqual(report["geometry"]["boundary_edges"], 3)
        self.assertEqual(report["ignored_obj_records"], 1)

    def test_normalized_round_trip_and_original_preservation(self):
        source = ROOT / "tests/fixtures/tetrahedron.obj"
        before = source.read_bytes()
        exported = self.folder / "normalized.obj"
        report = self.run_mesh(source, "--export", exported, "--normalize")
        self.assertAlmostEqual(report["bounding_box_diagonal"], math.sqrt(3))
        normalized = self.run_mesh(exported)
        self.assertAlmostEqual(normalized["bounding_box_diagonal"], 1)
        for a, b in zip(normalized["bounds_min"], normalized["bounds_max"]):
            self.assertAlmostEqual(a + b, 0)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(normalized["triangle_count"], 4)

    def test_invalid_inputs(self):
        prefix = "v 0 0 0\nv 1 0 0\nv 0 1 0\n"
        for face in ("f 0 2 3", "f 1 2 4", "f -4 -2 -1", "f 1 2", "f 1 2 3 1", "f 1x 2 3"):
            with self.subTest(face=face):
                result = self.run_mesh(self.source(prefix + face), ok=False)
                self.assertIn("OBJ line 4", result.stderr)
        for text in ("", "v nan 0 0\nf 1 1 1", "v 0 0 0 1\nf 1 1 1"):
            self.run_mesh(self.source(text), ok=False)

    def test_zero_size_inspect_but_no_normalization(self):
        source = self.source("v 0 0 0\nf 1 1 1\n")
        report = self.run_mesh(source)
        self.assertFalse(report["normalization"]["available"])
        self.assertEqual(report["geometry"]["degenerate_triangles"], 1)
        self.run_mesh(source, "--normalize", "--export", self.folder / "out.obj", ok=False)

    def test_does_not_overwrite_source(self):
        source = self.source("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3")
        before = source.read_bytes()
        self.run_mesh(source, "--export", source, ok=False)
        self.assertEqual(source.read_bytes(), before)

    def test_python_entry_point(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "python"))
        result = subprocess.run([sys.executable, "-m", "assetq", "inspect", str(ROOT / "tests/fixtures/tetrahedron.obj"), "--report", str(self.report), "--binary", str(BINARY)], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Vertices: 4 | Triangles: 4", result.stdout)

    def test_hard_link_cannot_overwrite_source(self):
        source = self.source("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3")
        alias = self.folder / "alias.obj"
        os.link(source, alias)
        before = source.read_bytes()
        self.run_mesh(source, "--export", alias, ok=False)
        self.assertEqual(source.read_bytes(), before)

    def test_extreme_coordinate_ranges_fail_cleanly(self):
        for size in ("1e-310", "1e308"):
            self.run_mesh(self.source(f"v -{size} 0 0\nv {size} 0 0\nv 0 0 0\nf 1 2 3"), ok=False)


if __name__ == "__main__":
    unittest.main()
