"""Dihedral crease extraction and length-weighted point-to-segment measurements."""
import numpy as np


def face_normals(vertices, faces):
    t = vertices[faces]
    normals = np.cross(t[:,1]-t[:,0],t[:,2]-t[:,0])
    return normals/np.linalg.norm(normals,axis=1)[:,None]


def crease_segments(vertices, faces, angle=45):
    if not np.isfinite(angle) or not 0<angle<180:
        raise ValueError("Feature angle must be in (0,180)")
    normals = face_normals(vertices,faces)
    incidence = {}
    for i,face in enumerate(faces):
        for a,b in zip(face,np.roll(face,-1)):
            incidence.setdefault(tuple(sorted((int(a),int(b)))),[]).append(i)
    threshold = np.cos(np.deg2rad(angle))
    edges = [e for e,fs in incidence.items() if len(fs)==2 and np.dot(normals[fs[0]],normals[fs[1]])<=threshold]
    return vertices[np.asarray(edges,dtype=int)] if edges else np.empty((0,2,3))


def sample_segments(segments,count,rng):
    length = np.linalg.norm(segments[:,1]-segments[:,0],axis=1)
    chosen = rng.choice(len(segments),size=count,p=length/length.sum())
    t = rng.random(count)[:,None]
    return (1-t)*segments[chosen,0]+t*segments[chosen,1]


def segment_distances(points,segments):
    """Exact nearest segment for each sampled point; bounded temporary memory."""
    if len(segments)==0:
        raise ValueError("Cannot query an empty feature network")
    output = np.empty(len(points))
    for start in range(0,len(points),256):
        p = points[start:start+256]
        best = np.full(len(p),np.inf)
        for j in range(0,len(segments),512):
            a,b = segments[j:j+512,0],segments[j:j+512,1]
            d = b-a
            offset = p[:,None,:]-a[None,:,:]
            t = np.clip(np.sum(offset*d[None,:,:],axis=2)/np.sum(d*d,axis=1),0,1)
            delta = offset-t[:,:,None]*d[None,:,:]
            best = np.minimum(best,np.min(np.sum(delta*delta,axis=2),axis=1))
        output[start:start+len(p)] = np.sqrt(best)
    return output


def feature_errors(ov,of,sv,sf,count,seed,angle):
    from .evaluate import statistics
    original,candidate = crease_segments(ov,of,angle),crease_segments(sv,sf,angle)
    report = {"angle_degrees":angle,"samples_per_direction":count,
              "original_crease_edges":len(original),"candidate_crease_edges":len(candidate),
              "original_length_normalized":float(np.linalg.norm(original[:,1]-original[:,0],axis=1).sum()),
              "candidate_length_normalized":float(np.linalg.norm(candidate[:,1]-candidate[:,0],axis=1).sum()),
              "original_to_candidate":None,"candidate_to_original":None,"worst_direction_p95":None}
    if not len(original) or not len(candidate):
        report["status"] = ("not_applicable" if not len(original) and not len(candidate) else
                            "missing_candidate_features" if len(original) else "introduced_candidate_features")
        return report
    a,b = np.random.SeedSequence([seed,8675309]).spawn(2)
    report["original_to_candidate"] = statistics(segment_distances(sample_segments(original,count,np.random.default_rng(a)),candidate))
    report["candidate_to_original"] = statistics(segment_distances(sample_segments(candidate,count,np.random.default_rng(b)),original))
    report["worst_direction_p95"] = max(report[k]["p95"] for k in ("original_to_candidate","candidate_to_original"))
    report["status"] = "measured"
    return report
