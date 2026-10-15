import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_features import subdivided_cube
from test_simplification import write_mesh

ROOT=Path(__file__).resolve().parents[1]
BINARY=Path(os.environ.get("ASSETQ_SIMPLIFY_BINARY",ROOT/"build/assetq-simplify")).resolve()
INSPECTOR=Path(os.environ.get("ASSETQ_BINARY",ROOT/"build/assetq-inspect")).resolve()


class CurvatureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.source=self.folder/"input.obj"
        self.output=self.folder/"output.obj"
        self.report=self.folder/"report.json"
        write_mesh(self.source,*subdivided_cube())

    def run_mesh(self,method="standard",strength="4",target=216,ok=True):
        r=subprocess.run([str(BINARY),str(self.source),"--target",str(target),"--export",str(self.output),
                          "--report",str(self.report),"--method",method,"--curvature-strength",strength],capture_output=True,text=True)
        self.assertEqual(r.returncode==0,ok,r.stderr)
        return json.loads(self.report.read_text()) if ok else r

    def test_zero_strength_is_exact_ablation(self):
        for baseline,weighted in (("standard","curvature"),("feature","combined")):
            self.run_mesh(baseline)
            before=self.output.read_bytes()
            report=self.run_mesh(weighted,"0")
            self.assertEqual(self.output.read_bytes(),before)
            self.assertEqual(report["maximum_face_weight"],1)
            self.assertEqual(report["curvature_positive_p95_reference"],0)

    def test_cube_weights_are_bounded_and_geometry_valid(self):
        report=self.run_mesh("curvature")
        self.assertEqual(report["minimum_face_weight"],1)
        self.assertAlmostEqual(report["maximum_face_weight"],5)
        self.assertGreater(report["mean_face_weight"],1)
        self.assertLess(report["mean_face_weight"],5)
        self.assertGreater(report["curvature_positive_p95_reference"],0)
        self.assertTrue(report["target_reached"])
        inspection=self.folder/"inspection.json"
        subprocess.run([str(INSPECTOR),str(self.output),"--report",str(inspection)],check=True,capture_output=True)
        g=json.loads(inspection.read_text())["geometry"]
        for key in ("degenerate_triangles","duplicate_triangles","non_manifold_edges","boundary_edges","inconsistent_winding_edges"):
            self.assertEqual(g[key],0)

    def test_flat_mesh_has_no_curvature_and_matches_standard(self):
        n=7
        vertices=[(x,y,0) for y in range(n) for x in range(n)]
        faces=[]
        for y in range(n-1):
            for x in range(n-1):
                a=y*n+x
                faces.extend([(a,a+1,a+n+1),(a,a+n+1,a+n)])
        write_mesh(self.source,vertices,faces)
        self.run_mesh(target=40)
        before=self.output.read_bytes()
        report=self.run_mesh("curvature",target=40)
        self.assertEqual(report["curvature_positive_p95_reference"],0)
        self.assertEqual(report["mean_face_weight"],1)
        self.assertEqual(before,self.output.read_bytes())

    def test_combined_retains_feature_policy(self):
        report=self.run_mesh("combined")
        self.assertEqual(report["method"],"combined")
        self.assertEqual(report["protected_corners"],8)
        self.assertEqual(report["original_crease_edges"],72)
        self.assertGreater(report["mean_face_weight"],1)
        self.assertTrue(report["target_reached"])

    def test_invalid_strength(self):
        for value in ("-1","nan","inf","1001","4x"):
            self.run_mesh("curvature",value,ok=False)
