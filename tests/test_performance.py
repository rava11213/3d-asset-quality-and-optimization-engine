import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from test_simplification import sphere,write_mesh

ROOT=Path(__file__).resolve().parents[1]
BINARY=Path(os.environ.get("ASSETQ_SIMPLIFY_BINARY",ROOT/"build/assetq-simplify")).resolve()
INSPECTOR=Path(os.environ.get("ASSETQ_BINARY",ROOT/"build/assetq-inspect")).resolve()


class InstrumentationTests(unittest.TestCase):
    def test_profile_and_refresh_leave_geometry_identical(self):
        from test_boundary import grid
        from test_features import subdivided_cube
        with tempfile.TemporaryDirectory() as d:
            d=Path(d); source=d/"input.obj"; output=d/"output.obj"; report=d/"report.json"
            for method,geometry,target in (("standard",sphere(2),64),("combined",subdivided_cube(),216),("standard",grid(curved=True),40)):
                write_mesh(source,*geometry); expected=None
                for variant,profile in (("tree","off"),("tree","on"),("sorted","on"),("sorted","off")):
                    command=[str(BINARY),str(source),"--target",str(target),"--export",str(output),"--report",str(report),"--method",method,
                             "--boundary-policy","chain","--queue-refresh",variant,"--profile",profile]
                    r=subprocess.run(command,capture_output=True,text=True)
                    self.assertEqual(r.returncode,0,r.stderr)
                    if expected is None: expected=output.read_bytes()
                    self.assertEqual(output.read_bytes(),expected)
                    value=json.loads(report.read_text())
                    if profile=="off": self.assertIsNone(value["profile"])
                    else:
                        p=value["profile"]
                        self.assertGreater(p["queue_pushes"],0)
                        self.assertGreaterEqual(p["queue_pops"],p["stale_pops"])
                        self.assertGreater(p["queue_refresh_seconds"],0)
                        total=sum(v for k,v in p.items() if k.endswith("_seconds"))
                        self.assertLessEqual(total,value["elapsed_seconds"]*1.01)
                        self.assertTrue(all(math.isfinite(v) and v>=0 for v in p.values()))
                    if value["peak_process_rss_bytes"] is not None: self.assertGreater(value["peak_process_rss_bytes"],0)


@unittest.skipUnless(importlib.util.find_spec("matplotlib"),"Install performance extra")
class PerformanceRunnerTests(unittest.TestCase):
    def test_missing_asset_records_failure_without_comparison(self):
        with tempfile.TemporaryDirectory() as d:
            output=Path(d)/"run"
            r=subprocess.run([sys.executable,"-m","assetq","performance",str(Path(d)/"missing.obj"),"--output-dir",str(output),
                              "--repeats","2","--binary",str(BINARY),"--inspector",str(INSPECTOR)],capture_output=True,text=True,
                             env=dict(os.environ,PYTHONPATH=str(ROOT/"python")))
            self.assertEqual(r.returncode,1,r.stderr)
            report=json.loads((output/"performance.json").read_text())
            self.assertEqual(report["status"],"partial_failure")
            self.assertFalse(report["comparisons"])
            self.assertTrue((output/"report.md").is_file())

    def test_complete_trials_and_excluded_warmup(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d); source=d/"sphere.obj"; write_mesh(source,*sphere(1)); out=d/"run"
            command=[sys.executable,"-m","assetq","performance",str(source),"--output-dir",str(out),"--repeats","2","--warmups","1",
                     "--retain","0.5","--binary",str(BINARY),"--inspector",str(INSPECTOR)]
            r=subprocess.run(command,capture_output=True,text=True,env=dict(os.environ,PYTHONPATH=str(ROOT/"python")))
            self.assertEqual(r.returncode,0,r.stderr)
            report=json.loads((out/"performance.json").read_text())
            self.assertEqual(len(report["rows"]),8)
            self.assertEqual(len(report["summaries"]),2)
            self.assertTrue(all(s["trials"]==2 for s in report["summaries"]))
            self.assertTrue(report["comparisons"][0]["identical_geometry"])
            self.assertTrue((out/"performance.png").is_file())
            r=subprocess.run(command,capture_output=True,text=True,env=dict(os.environ,PYTHONPATH=str(ROOT/"python")))
            self.assertNotEqual(r.returncode,0)

    def test_summary_ignores_profile_and_warmup(self):
        sys.path.insert(0,str(ROOT/"python"))
        from assetq.performance import aggregate
        rows=[]
        for phase,seconds in (("profile",100),("warmup",100),("trial",1),("trial",3)):
            rows.append(dict(asset_id="x",method="standard",target=10,variant="tree",phase=phase,original_triangles=20,
                             achieved_triangles=10,optimizer_seconds=seconds,process_wall_seconds=seconds,peak_process_rss_bytes=None,mesh_sha256="same"))
        summary=aggregate(rows)[0]
        self.assertEqual(summary["trials"],2)
        self.assertEqual(summary["optimizer_seconds"]["median"],2)
        self.assertEqual(summary["optimizer_seconds"]["iqr"],1)
        self.assertIsNone(summary["peak_process_rss_bytes"])
