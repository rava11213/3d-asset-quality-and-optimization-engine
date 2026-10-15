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


def bumped_sphere(levels=4):
    """Closed smooth surface with two localized radial bumps; no semantic creases."""
    vertices=[(1.,0.,0.),(-1.,0.,0.),(0.,1.,0.),(0.,-1.,0.),(0.,0.,1.),(0.,0.,-1.)]
    faces=[(0,2,4),(2,1,4),(1,3,4),(3,0,4),(2,0,5),(1,2,5),(3,1,5),(0,3,5)]
    for _ in range(levels):
        cache={}
        def midpoint(a,b):
            key=tuple(sorted((a,b)))
            if key not in cache:
                p=tuple((vertices[a][k]+vertices[b][k])/2 for k in range(3))
                norm=math.sqrt(sum(x*x for x in p))
                cache[key]=len(vertices)
                vertices.append(tuple(x/norm for x in p))
            return cache[key]
        refined=[]
        for a,b,c in faces:
            ab,bc,ca=midpoint(a,b),midpoint(b,c),midpoint(c,a)
            refined.extend([(a,ab,ca),(ab,b,bc),(ca,bc,c),(ab,bc,ca)])
        faces=refined
    deformed=[]
    for x,y,z in vertices:
        radius=1+.25*math.exp(-18*(1-x))+.15*math.exp(-28*(1-y))
        deformed.append((x*radius,y*radius,z*radius))
    return deformed,faces


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output",type=Path)
    parser.add_argument("--subdivisions",type=int,default=12)
    parser.add_argument("--shape",choices=["rippled-box","bumped-sphere"],default="rippled-box")
    args=parser.parse_args()
    v,f=rippled_box(args.subdivisions) if args.shape=="rippled-box" else bumped_sphere()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("w") as stream:
        stream.write(f"# Procedural {args.shape}; assetq.examples\n")
        for p in v:
            stream.write("v "+" ".join(format(x,".17g") for x in p)+"\n")
        for t in f:
            stream.write("f "+" ".join(str(i+1) for i in t)+"\n")
