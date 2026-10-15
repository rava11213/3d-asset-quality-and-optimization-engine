"""Isolated adapter for PyVista's fast-simplification reference implementation."""
import argparse
import json
from pathlib import Path
import time
import numpy as np
from .evaluate import load_obj


def run(source,target,output,report):
    import fast_simplification as fs
    source,output,report = Path(source),Path(output),Path(report)
    paths=[source,output,report]
    for i,a in enumerate(paths):
        for b in paths[i+1:]:
            if a.resolve()==b.resolve() or (a.exists() and b.exists() and a.samefile(b)):
                raise ValueError("Source, export, and report must differ")
    vertices,faces = load_obj(source)
    if not 1<=target<=len(faces):
        raise ValueError("Target must be between one and input triangle count")
    lo,hi = vertices.min(axis=0),vertices.max(axis=0)
    center,diagonal = lo/2+hi/2,float(np.linalg.norm(hi-lo))
    start = time.perf_counter()
    # Match the numerical conditioning of our implementation; keep the library's
    # own topology/boundary policy and explicitly report it rather than claiming equivalence.
    points,triangles = fs.simplify((vertices-center)/diagonal,faces,target_count=target,agg=7.0)
    elapsed = time.perf_counter()-start
    points = points*diagonal+center
    with Path(output).open("w") as stream:
        for p in points:
            stream.write("v " + " ".join(format(float(x),".17g") for x in p) + "\n")
        for t in triangles:
            stream.write("f " + " ".join(str(int(x)+1) for x in t) + "\n")
    value = {"method":"fast","library":"fast-simplification","library_version":fs.__version__,"aggressiveness":7.0,
             "boundary_policy":"library_default","target_triangles":target,"original_triangles":len(faces),
             "achieved_triangles":len(triangles),"target_reached":len(triangles)==target,
             "triangle_reduction":1-len(triangles)/len(faces),"elapsed_seconds":elapsed,
             "timing_scope":"Python library call only; not directly comparable to C++ validation-inclusive time"}
    Path(report).write_text(json.dumps(value,indent=2)+"\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("target",type=int)
    parser.add_argument("output")
    parser.add_argument("report")
    args = parser.parse_args()
    run(args.source,args.target,args.output,args.report)
