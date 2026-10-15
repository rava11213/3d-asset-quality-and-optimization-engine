"""Render two OBJ meshes with the same camera and bounds for the README."""
import argparse
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR",str(ROOT/"work/matplotlib"))
sys.path.insert(0,str(ROOT/"python"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np
from assetq.evaluate import load_obj


def render(original,simplified,output):
    ov,of=load_obj(original); sv,sf=load_obj(simplified)
    # OBJ Y is vertical for this reference asset; plot Z vertically in Matplotlib.
    ov,sv=ov[:,[0,2,1]],sv[:,[0,2,1]]
    lo=np.minimum(ov.min(axis=0),sv.min(axis=0)); hi=np.maximum(ov.max(axis=0),sv.max(axis=0))
    center=(lo+hi)/2; radius=max(hi-lo)*.52
    fig=plt.figure(figsize=(11,5.5),facecolor="#f4f7fa")
    light=np.array([-.5,.8,1.]); light/=np.linalg.norm(light)
    for i,(v,f,label) in enumerate(((ov,of,"Original"),(sv,sf,"Simplified"))):
        ax=fig.add_subplot(1,2,i+1,projection="3d",computed_zorder=False)
        triangles=v[f]
        normal=np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
        normal/=-np.linalg.norm(normal,axis=1)[:,None]  # Axis swap reverses handedness.
        intensity=.35+.65*np.clip(normal@light,0,1)
        base=np.array([.22,.52,.58])
        colors=np.clip(intensity[:,None]*base[None,:]+.1,0,1)
        ax.add_collection3d(Poly3DCollection(triangles,facecolors=colors,edgecolors="none",linewidths=0,antialiased=False,rasterized=True))
        for setter,c in zip((ax.set_xlim,ax.set_ylim,ax.set_zlim),center): setter(c-radius,c+radius)
        ax.set_box_aspect((1,1,1)); ax.view_init(elev=10,azim=115)
        ax.set_axis_off(); ax.set_facecolor("#f4f7fa")
        ax.set_title(f"{label}\n{len(f):,} triangles",fontsize=15,pad=0,color="#243746")
    fig.subplots_adjust(left=0,right=1,bottom=0,top=.9,wspace=-.08)
    output.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(output,dpi=170,facecolor=fig.get_facecolor()); plt.close(fig)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original",type=Path); parser.add_argument("simplified",type=Path)
    parser.add_argument("--output",type=Path,default=ROOT/"docs/assets/mesh-comparison.png")
    args=parser.parse_args(); render(args.original,args.simplified,args.output)
