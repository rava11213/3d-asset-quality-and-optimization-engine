import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"python"))
AVAILABLE = all(importlib.util.find_spec(m) for m in ("numpy","warp","matplotlib"))


@unittest.skipUnless(AVAILABLE,"Install benchmark extras")
class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        from test_simplification import sphere, write_mesh
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder/"sphere.obj"
        write_mesh(self.source,*sphere(1))
        self.output = self.folder/"benchmark"

    def run_command(self,*extra,inputs=None):
        command = [sys.executable,"-m","assetq","benchmark",*map(str,inputs or [self.source]),
                   "--output-dir",str(self.output),"--samples","100","--retain","0.5","--seeds","42","43",
                   "--binary",str(Path(os.environ.get("ASSETQ_SIMPLIFY_BINARY",ROOT/"build/assetq-simplify")).resolve()),
                   "--inspector",str(Path(os.environ.get("ASSETQ_BINARY",ROOT/"build/assetq-inspect")).resolve()),*extra]
        return subprocess.run(command,capture_output=True,text=True,env=dict(os.environ,PYTHONPATH=str(ROOT/"python")))

    def test_full_pipeline_and_exports(self):
        before = self.source.read_bytes()
        result = self.run_command("--max-p95","1")
        self.assertEqual(result.returncode,0,result.stderr)
        report = json.loads((self.output/"benchmark.json").read_text())
        self.assertEqual(report["status"],"complete")
        self.assertEqual(len(report["rows"]),2)
        self.assertEqual(report["points"][0]["achieved_triangles"],16)
        self.assertTrue(report["points"][0]["complete"])
        self.assertEqual(report["selections"]["001-sphere"]["achieved_triangles"],16)
        for path in ("results.csv","tradeoffs.png","tradeoffs.svg","report.md"):
            self.assertGreater((self.output/path).stat().st_size,0)
        self.assertEqual(len((self.output/"results.csv").read_text().splitlines()),3)
        self.assertEqual(self.source.read_bytes(),before)

    def test_bad_asset_recorded_and_other_asset_completes(self):
        missing = self.folder/"missing.obj"
        result = self.run_command(inputs=[missing,self.source])
        self.assertEqual(result.returncode,1,result.stderr)
        report = json.loads((self.output/"benchmark.json").read_text())
        self.assertEqual(report["status"],"partial_failure")
        self.assertEqual(report["failures"][0]["stage"],"inspection")
        self.assertEqual(len(report["rows"]),2)
        self.assertTrue((self.output/"tradeoffs.png").is_file())

    def test_all_failures_still_produce_report(self):
        result = self.run_command(inputs=[self.folder/"missing.obj"])
        self.assertEqual(result.returncode,1,result.stderr)
        report = json.loads((self.output/"benchmark.json").read_text())
        self.assertFalse(report["rows"])
        self.assertTrue((self.output/"report.md").is_file())

    def test_refuses_nonempty_output(self):
        self.output.mkdir()
        sentinel = self.output/"keep.txt"
        sentinel.write_text("keep")
        result = self.run_command()
        self.assertNotEqual(result.returncode,0)
        self.assertIn("new or empty",result.stderr)
        self.assertEqual(sentinel.read_text(),"keep")

    def test_multi_method_ablation_records_options_and_metrics(self):
        result = self.run_command("--methods","standard","constraints","feature","--feature-angle","100","--feature-samples","100")
        self.assertEqual(result.returncode,0,result.stderr)
        report = json.loads((self.output/"benchmark.json").read_text())
        self.assertEqual(len(report["rows"]),6)
        self.assertEqual({p["method"] for p in report["points"]},{"standard","constraints","feature"})
        for p in report["points"]:
            self.assertTrue(p["complete"])
        for row in report["rows"]:
            self.assertIn("normal_p95_degrees",row)
            self.assertEqual(row["feature_status"],"not_applicable")
            optimization = json.loads(Path(row["optimization_report"]).read_text())
            if row["method"]=="constraints":
                self.assertEqual(optimization["feature_weight"],0)
        self.assertTrue(all(c["matched_complete"] for c in report["comparisons"]))
        self.assertTrue((self.output/"method-comparison.png").is_file())

    @unittest.skipIf(importlib.util.find_spec("fast_simplification"),"Reference installed; missing-dependency check not applicable")
    def test_unavailable_external_method_is_a_failure_not_a_fake_result(self):
        result = self.run_command("--methods","standard","fast","--feature-samples","0")
        self.assertEqual(result.returncode,1,result.stderr)
        report = json.loads((self.output/"benchmark.json").read_text())
        self.assertEqual({r["method"] for r in report["rows"]},{"standard"})
        self.assertEqual(report["failures"][0]["method"],"fast")

    def test_selection_requires_all_seeds_and_uses_achieved_count(self):
        from assetq.benchmark import summarize,select_versions
        rows = []
        for target,achieved,seed,error in ((10,12,42,0.1),(10,12,43,0.2),(5,8,42,0.05)):
            rows.append(dict(asset_id="a",target_triangles=target,achieved_triangles=achieved,
                             target_reached=False,triangle_reduction=0.5,mesh_path="mesh.obj",seed=seed,
                             worst_direction_p95=error,mean=0.01))
        points = summarize(rows,[42,43])
        selected = select_versions(points,["a"],0.2)
        self.assertEqual(selected["a"]["achieved_triangles"],12)
        self.assertIsNone(select_versions(points,["a"],0.15)["a"])
