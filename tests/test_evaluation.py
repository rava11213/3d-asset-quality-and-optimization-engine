import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
AVAILABLE = all(importlib.util.find_spec(m) for m in ("numpy", "warp"))


@unittest.skipUnless(AVAILABLE, "Install evaluation extras to test NVIDIA Warp")
class EvaluationTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        from assetq.evaluate import evaluate, load_obj, sample_surface
        self.np, self.evaluate, self.load_obj, self.sample = np, evaluate, load_obj, sample_surface
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.original = self.mesh("original.obj")

    def mesh(self, name, z=0, scale=1):
        path = self.folder / name
        path.write_text(f"v 0 0 {z}\nv {scale} 0 {z}\nv 0 {scale} {z}\nf 1 2 3\n")
        return path

    def test_identical_mesh_near_zero(self):
        r = self.evaluate(self.original, self.original, samples=1000)
        self.assertLess(r["normalized"]["symmetric_pooled"]["sampled_max"], 2e-7)

    def test_parallel_surface_known_distance(self):
        target = self.mesh("shifted.obj", z=0.25)
        r = self.evaluate(self.original, target, samples=1000)
        for direction in ("original_to_simplified", "simplified_to_original", "symmetric_pooled"):
            for value in r["original_units"][direction].values():
                self.assertAlmostEqual(value, 0.25, delta=2e-7)
            self.assertAlmostEqual(r["normalized"][direction]["mean"], 0.25/math.sqrt(2), delta=2e-7)

    def test_both_directions_are_needed(self):
        target = self.mesh("larger.obj", scale=2)
        r = self.evaluate(self.original,target,samples=2000)
        self.assertLess(r["normalized"]["original_to_simplified"]["p95"], 2e-7)
        self.assertGreater(r["normalized"]["simplified_to_original"]["p95"], 0.2)

    def test_reproducible_samples_and_area_weights(self):
        np = self.np
        v = np.array([[0,0,0],[1,0,0],[0,1,0],[10,0,0],[12,0,0],[10,2,0]],dtype=float)
        f = np.array([[0,1,2],[3,4,5]])
        a = self.sample(v,f,20000,np.random.default_rng(42))
        b = self.sample(v,f,20000,np.random.default_rng(42))
        np.testing.assert_array_equal(a,b)
        self.assertAlmostEqual(float(np.mean(a[:,0]>5)),0.8,delta=0.015)
        self.assertAlmostEqual(float(a[a[:,0]<5,0].mean()),1/3,delta=0.02)

    def test_scale_normalization_and_seed(self):
        a = self.evaluate(self.original,self.mesh("a.obj",z=0.25),samples=100)
        large = self.mesh("large.obj",scale=100)
        b = self.evaluate(large,self.mesh("b.obj",scale=100,z=25),samples=100)
        self.assertAlmostEqual(a["normalized"]["symmetric_pooled"]["mean"], b["normalized"]["symmetric_pooled"]["mean"],delta=2e-7)
        c = self.evaluate(self.original,self.folder/"a.obj",samples=100)
        self.assertEqual(a["normalized"],c["normalized"])

    def test_invalid_geometry_and_options(self):
        for text in ("", "v 0 0 0\nf 1 1 1", "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 0 2 3"):
            path = self.folder / "bad.obj"
            path.write_text(text)
            with self.assertRaises(ValueError):
                self.load_obj(path)
        for samples, seed in ((0,42),(10,-1)):
            with self.assertRaises(ValueError):
                self.evaluate(self.original,self.original,samples=samples,seed=seed)

    def test_cli_and_input_protection(self):
        command = [sys.executable,"-m","assetq","evaluate",str(self.original),str(self.original),"--samples","100","--report"]
        import os
        env = dict(os.environ,PYTHONPATH=str(ROOT/"python"))
        report = self.folder / "report.json"
        result = subprocess.run([*command,str(report)],capture_output=True,text=True,env=env)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(len(json.loads(report.read_text())["evaluations"]),1)
        before = self.original.read_bytes()
        result = subprocess.run([*command,str(self.original)],capture_output=True,text=True,env=env)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.original.read_bytes(),before)
