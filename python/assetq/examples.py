"""Generate a deterministic closed box with curved patches and sharp seams."""
import argparse
import math
from pathlib import Path


def rippled_box(n=12):
    if n<2:
        raise ValueError("Subdivisions must be at least two")
    vertices,faces,cache = [],[],{}
    def vertex(p):
        if p not in cache:
            cache[p]=len(vertices)
            x,y,z=p
            vertices.append((x+.12*n*math.sin(math.pi*z/n)*math.sin(2*math.pi*y/n),y,
                             z*(1+.18*math.sin(2*math.pi*x/n))+.4*abs(x-n/2)*z/n))
        return cache[p]
    frames = [((n,0,0),(0,1,0),(0,0,1)),((0,0,0),(0,0,1),(0,1,0)),
              ((0,n,0),(0,0,1),(1,0,0)),((0,0,0),(1,0,0),(0,0,1)),
              ((0,0,n),(1,0,0),(0,1,0)),((0,0,0),(0,1,0),(1,0,0))]
    for origin,u,v in frames:
        def at(i,j):
            return vertex(tuple(origin[k]+i*u[k]+j*v[k] for k in range(3)))
        for j in range(n):
            for i in range(n):
                a,b,c,d=at(i,j),at(i+1,j),at(i+1,j+1),at(i,j+1)
                faces.extend([(a,b,c),(a,c,d)])
    return vertices,faces


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output",type=Path)
    parser.add_argument("--subdivisions",type=int,default=12)
    args=parser.parse_args()
    v,f=rippled_box(args.subdivisions)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("w") as stream:
        stream.write("# Procedural rippled box; assetq.examples\n")
        for p in v:
            stream.write("v "+" ".join(format(x,".17g") for x in p)+"\n")
        for t in f:
            stream.write("f "+" ".join(str(i+1) for i in t)+"\n")
