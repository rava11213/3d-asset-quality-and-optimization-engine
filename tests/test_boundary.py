import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_simplification import write_mesh,read_vertices,sphere

ROOT=Path(__file__).resolve().parents[1]
BINARY=Path(os.environ.get("ASSETQ_SIMPLIFY_BINARY",ROOT/"build/assetq-simplify")).resolve()
INSPECTOR=Path(os.environ.get("ASSETQ_BINARY",ROOT/"build/assetq-inspect")).resolve()


def grid(n=9,hole=False,width=None,curved=False):
    width=n if width is None else width
    vertices=[(x,y,.3*math.sin(x*.6)*math.sin(y*.6) if curved else 0) for y in range(n) for x in range(width)]
    faces=[]
    for y in range(n-1):
        for x in range(width-1):
            if hole and 3<=x<5 and 3<=y<5:
                continue
            a=y*width+x
            faces.extend([(a,a+1,a+width+1),(a,a+width+1,a+width)])
    return vertices,faces


def topology(path):
    faces=[tuple(int(x)-1 for x in line.split()[1:]) for line in path.read_text().splitlines() if line.startswith("f ")]
    edges={}
    used=set()
    for t in faces:
        used.update(t)
        for a,b in zip(t,t[1:]+t[:1]):
            e=tuple(sorted((a,b))); edges[e]=edges.get(e,0)+1
    boundary={}
    for (a,b),count in edges.items():
        if count==1:
            boundary.setdefault(a,set()).add(b); boundary.setdefault(b,set()).add(a)
    assert all(len(n)==2 for n in boundary.values()),"Boundary must consist of disjoint closed loops"
    unseen=set(boundary); loops=0
    while unseen:
        loops+=1; stack=[unseen.pop()]
        while stack:
            v=stack.pop()
            for x in boundary[v]:
                if x in unseen:
                    unseen.remove(x); stack.append(x)
    return len(used)-len(edges)+len(faces),loops,len(boundary)


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name); self.source=self.folder/"input.obj"
        self.output=self.folder/"out.obj"; self.report=self.folder/"report.json"
        write_mesh(self.source,*grid())

    def run_mesh(self,policy="chain",target=20,method="standard",ok=True):
        r=subprocess.run([str(BINARY),str(self.source),"--target",str(target),"--export",str(self.output),
                          "--report",str(self.report),"--boundary-policy",policy,"--method",method],capture_output=True,text=True)
        self.assertEqual(r.returncode==0,ok,r.stderr)
        return json.loads(self.report.read_text()) if ok else r

    def assert_clean(self):
        p=self.folder/"inspect.json"
        subprocess.run([str(INSPECTOR),str(self.output),"--report",str(p)],check=True,capture_output=True)
        g=json.loads(p.read_text())["geometry"]
        for k in ("degenerate_triangles","duplicate_triangles","non_manifold_edges","inconsistent_winding_edges"):
            self.assertEqual(g[k],0,k)

    def test_grid_reduces_boundary_and_protects_corners(self):
        before=self.source.read_bytes()
        locked=self.run_mesh("lock",target=12)
        report=self.run_mesh(target=12)
        self.assertEqual(report["protected_boundary_corners"],4)
        self.assertGreater(report["boundary_collapses"],0)
        self.assertLess(report["achieved_triangles"],locked["achieved_triangles"])
        self.assertGreaterEqual(report["achieved_triangles"],12)
        self.assertEqual(topology(self.output)[:2],(1,1))
        self.assertLess(topology(self.output)[2],32)
        points=read_vertices(self.output)
        for corner in ((0,0,0),(8,0,0),(0,8,0),(8,8,0)):
            self.assertTrue(any(math.dist(corner,p)<1e-10 for p in points))
        self.assert_clean(); self.assertEqual(before,self.source.read_bytes())

    def test_hole_is_preserved(self):
        write_mesh(self.source,*grid(hole=True))
        self.run_mesh(target=24)
        self.assertEqual(topology(self.output)[:2],(0,2))
        self.assert_clean()

    def test_disconnected_patches_remain_separate(self):
        v,f=grid(n=5)
        vertices=v+[(x+10,y,z) for x,y,z in v]
        faces=f+[tuple(x+len(v) for x in t) for t in f]
        write_mesh(self.source,vertices,faces)
        self.run_mesh(target=20)
        self.assertEqual(topology(self.output)[:2],(2,2))
        self.assert_clean()

    def test_thin_strip_and_odd_budget(self):
        write_mesh(self.source,*grid(n=12,width=2))
        report=self.run_mesh(target=9)
        self.assertGreater(report["boundary_collapses"],0)
        self.assertGreaterEqual(report["achieved_triangles"],9)
        self.assertEqual(topology(self.output)[:2],(1,1))
        self.assert_clean()

    def test_closed_mesh_matches_locked_policy(self):
        write_mesh(self.source,*sphere(2))
        self.run_mesh("lock",target=64); before=self.output.read_bytes()
        report=self.run_mesh(target=64)
        self.assertEqual(report["boundary_collapses"],0)
        self.assertEqual(before,self.output.read_bytes())

    def test_combined_mode_on_curved_patch(self):
        write_mesh(self.source,*grid(curved=True))
        report=self.run_mesh(target=40,method="combined")
        self.assertGreater(report["boundary_collapses"],0)
        self.assertGreater(report["maximum_face_weight"],1)
        self.assertEqual(topology(self.output)[:2],(1,1))
        self.assert_clean()

    def test_unknown_policy_fails(self):
        self.run_mesh("invalid",ok=False)
