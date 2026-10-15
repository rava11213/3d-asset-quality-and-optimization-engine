import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"python"))
AVAILABLE = all(importlib.util.find_spec(m) for m in ("numpy","warp","matplotlib"))


@unittest.skipUnless(AVAILABLE,"Install evaluation and benchmark extras")
class ComparisonTests(unittest.TestCase):
    def test_segment_distances_are_to_lines_not_endpoints(self):
        import numpy as np
        from assetq.feature_metrics import segment_distances
        p = np.array([[.5,1,0],[.5,0,0],[2,0,0]],dtype=float)
        s = np.array([[[0,0,0],[1,0,0]]],dtype=float)
        np.testing.assert_allclose(segment_distances(p,s),[1,0,1])

    def test_feature_network_statuses_and_distance(self):
        import numpy as np
        from assetq.feature_metrics import feature_errors
        from test_features import subdivided_cube
        from test_simplification import sphere
        v,f = subdivided_cube(2)
        v,f = np.array(v,dtype=float),np.array(f)
        r = feature_errors(v,f,v,f,100,42,45)
        self.assertEqual(r["original_crease_edges"],24)
        self.assertEqual(r["status"],"measured")
        self.assertLess(r["worst_direction_p95"],1e-12)
        sv,sf = sphere(2)
        sv,sf = np.array(sv),np.array(sf)
        r = feature_errors(v,f,sv,sf,100,42,45)
        self.assertEqual(r["status"],"missing_candidate_features")
        self.assertIsNone(r["worst_direction_p95"])
        self.assertEqual(feature_errors(sv,sf,sv,sf,100,42,45)["status"],"not_applicable")

    def test_oriented_normals_detect_flipped_surface(self):
        import tempfile
        from assetq.evaluate import evaluate
        with tempfile.TemporaryDirectory() as d:
            a,b = Path(d)/"a.obj",Path(d)/"b.obj"
            text="v 0 0 0\nv 1 0 0\nv 0 1 0\n"
            a.write_text(text+"f 1 2 3\n")
            b.write_text(text+"f 1 3 2\n")
            r = evaluate(a,b,samples=100)
            self.assertLess(r["normalized"]["symmetric_pooled"]["p95"],1e-6)
            self.assertAlmostEqual(r["worst_direction_normal_p95_degrees"],180)

    def test_different_methods_are_not_pooled_and_counts_must_match(self):
        from assetq.benchmark import summarize,matched_comparisons
        rows = []
        for method,count,error in (("standard",12,.02),("constraints",12,.01),("feature",14,.005)):
            rows.append(dict(asset_id="a",method=method,target_triangles=10,achieved_triangles=count,
                             target_reached=False,triangle_reduction=.5,mesh_path="m.obj",seed=42,
                             worst_direction_p95=error,mean=error))
        points = summarize(rows,[42])
        self.assertEqual(len(points),3)
        comparisons = matched_comparisons(points)
        self.assertTrue(comparisons[0]["matched_complete"])
        self.assertAlmostEqual(comparisons[0]["surface_p95_delta"],-.01)
        self.assertFalse(comparisons[1]["matched_complete"])
        self.assertIsNone(comparisons[1]["surface_p95_delta"])
