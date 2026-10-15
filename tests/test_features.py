import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_simplification import sphere, write_mesh, read_vertices

ROOT = Path(__file__).resolve().parents[1]
BINARY = Path(os.environ.get("ASSETQ_SIMPLIFY_BINARY", ROOT/"build/assetq-simplify")).resolve()
INSPECTOR = Path(os.environ.get("ASSETQ_BINARY", ROOT/"build/assetq-inspect")).resolve()


def subdivided_cube(n=6):
    """Shared positions on six consistently wound subdivided cube faces."""
    vertices, faces, cache = [], [], {}
    def vertex(p):
        if p not in cache:
            cache[p] = len(vertices)
            vertices.append(p)
        return cache[p]
    # u cross v is the outward normal for each parameterized face.
    frames = [((n,0,0),(0,1,0),(0,0,1)), ((0,0,0),(0,0,1),(0,1,0)),
              ((0,n,0),(0,0,1),(1,0,0)), ((0,0,0),(1,0,0),(0,0,1)),
              ((0,0,n),(1,0,0),(0,1,0)), ((0,0,0),(0,1,0),(1,0,0))]
    for origin,u,v in frames:
        def at(i,j):
            return vertex(tuple(origin[k]+i*u[k]+j*v[k] for k in range(3)))
        for j in range(n):
            for i in range(n):
                a,b,c,d = at(i,j),at(i+1,j),at(i+1,j+1),at(i,j+1)
                faces += [(a,b,c),(a,c,d)]
    return vertices,faces


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder/"cube.obj"
        write_mesh(self.source,*subdivided_cube())
        self.output = self.folder/"out.obj"
        self.report = self.folder/"report.json"

    def run_mesh(self,*options,target=216,ok=True):
        result = subprocess.run([str(BINARY),str(self.source),"--target",str(target),"--export",str(self.output),"--report",str(self.report),*options],capture_output=True,text=True)
        self.assertEqual(result.returncode==0,ok,result.stderr)
        return json.loads(self.report.read_text()) if ok else result

    def test_cube_detection_corner_preservation_and_reduction(self):
        before = self.source.read_bytes()
        report = self.run_mesh("--method","feature")
        self.assertEqual(report["original_crease_edges"],12*6)
        self.assertEqual(report["protected_corners"],8)
        self.assertEqual(report["method"],"feature")
        self.assertTrue(report["target_reached"])
        vertices = read_vertices(self.output)
        for x in (0,6):
            for y in (0,6):
                for z in (0,6):
                    self.assertTrue(any(sum((p[k]-q)**2 for k,q in enumerate((x,y,z)))<1e-20 for p in vertices))
        # Every exported vertex remains on the cube surface.
        for p in vertices:
            self.assertTrue(any(abs(x)<1e-10 or abs(x-6)<1e-10 for x in p))
        inspection = self.folder/"inspection.json"
        subprocess.run([str(INSPECTOR),str(self.output),"--report",str(inspection)],check=True,capture_output=True)
        geometry = json.loads(inspection.read_text())["geometry"]
        for key in ("boundary_edges","non_manifold_edges","inconsistent_winding_edges","degenerate_triangles","duplicate_triangles"):
            self.assertEqual(geometry[key],0,key)
        self.assertEqual(self.source.read_bytes(),before)

    def test_smooth_sphere_has_no_45_degree_creases(self):
        write_mesh(self.source,*sphere(2))
        report = self.run_mesh("--method","feature",target=64)
        self.assertEqual(report["original_crease_edges"],0)
        self.assertEqual(report["protected_corners"],0)
        feature = self.output.read_bytes()
        self.run_mesh("--method","standard",target=64)
        self.assertEqual(self.output.read_bytes(),feature)

    def test_threshold_and_zero_weight_ablation(self):
        report = self.run_mesh("--method","feature","--feature-angle","100")
        self.assertEqual(report["original_crease_edges"],0)
        report = self.run_mesh("--method","feature","--feature-weight","0")
        self.assertEqual(report["original_crease_edges"],72)
        self.assertEqual(report["protected_corners"],8)
        self.assertTrue(report["target_reached"])

    def test_default_stays_standard_and_reproducible(self):
        report = self.run_mesh()
        self.assertEqual(report["method"],"standard")
        before = self.output.read_bytes()
        self.run_mesh("--method","standard")
        self.assertEqual(self.output.read_bytes(),before)
        self.run_mesh("--method","feature")
        before = self.output.read_bytes()
        self.run_mesh("--method","feature")
        self.assertEqual(self.output.read_bytes(),before)

    def test_invalid_options(self):
        for options in (("--method","unknown"),("--feature-angle","0"),("--feature-angle","180"),
                        ("--feature-angle","nan"),("--feature-weight","-1"),("--feature-weight","inf"),
                        ("--feature-weight","10junk")):
            self.run_mesh(*options,ok=False)
