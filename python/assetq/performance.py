"""Repeated fresh-process measurements; profiled trials never enter runtime summaries."""
import csv
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import time
from .benchmark import save_json, fingerprint, plt


def distribution(values):
    ordered=sorted(values)
    def percentile(q):
        position=(len(ordered)-1)*q
        low=int(position); high=min(low+1,len(ordered)-1)
        return ordered[low]+(position-low)*(ordered[high]-ordered[low])
    return {"median":statistics.median(values),"min":min(values),"max":max(values),
            "p25":percentile(.25),"p75":percentile(.75),"iqr":percentile(.75)-percentile(.25)}


def aggregate(rows):
    groups={}
    for row in rows:
        if row["phase"]=="trial":
            key=(row["asset_id"],row["method"],row["target"],row["variant"])
            groups.setdefault(key,[]).append(row)
    output=[]
    for (asset,method,target,variant),group in groups.items():
        rss=[r["peak_process_rss_bytes"] for r in group if r["peak_process_rss_bytes"] is not None]
        output.append({"asset_id":asset,"method":method,"target":target,"variant":variant,
                       "original_triangles":group[0]["original_triangles"],"achieved_triangles":group[0]["achieved_triangles"],
                       "trials":len(group),"optimizer_seconds":distribution([r["optimizer_seconds"] for r in group]),
                       "process_wall_seconds":distribution([r["process_wall_seconds"] for r in group]),
                       "peak_process_rss_bytes":distribution(rss) if rss else None,
                       "mesh_sha256":group[0]["mesh_sha256"]})
    return output


def render(output,report):
    summaries=report["summaries"]
    fig,axes=plt.subplots(1,2,figsize=(11,4),layout="constrained")
    for method,variant in sorted({(r["method"],r["variant"]) for r in summaries}):
        group=sorted([r for r in summaries if r["method"]==method and r["variant"]==variant],key=lambda r:r["original_triangles"])
        x=[r["original_triangles"] for r in group]
        y=[r["optimizer_seconds"]["median"] for r in group]
        axes[0].errorbar(x,y,yerr=[[v-r["optimizer_seconds"]["p25"] for v,r in zip(y,group)],
                                 [r["optimizer_seconds"]["p75"]-v for v,r in zip(y,group)]],marker="o",linestyle="none",label=f"{method}/{variant}")
        measured=[r for r in group if r["peak_process_rss_bytes"] is not None]
        if measured:
            axes[1].plot([r["original_triangles"] for r in measured],[r["peak_process_rss_bytes"]["median"]/1048576 for r in measured],marker="o",linestyle="none",label=f"{method}/{variant}")
    for ax,label in zip(axes,("Optimizer seconds (median; IQR)","Peak process RSS (MiB; median)")):
        ax.set(xlabel="Original triangle count",ylabel=label)
        ax.set_ylim(bottom=0); ax.grid(alpha=.25)
        if ax.get_legend_handles_labels()[0]: ax.legend(fontsize=8)
    fig.savefig(output/"performance.png",dpi=170); fig.savefig(output/"performance.svg"); plt.close(fig)
    lines=["# Optimizer performance study","",f"Status: {report['status']}. Hardware: {report['environment']}","",
           "Profiled executions and warm-ups are excluded from timing summaries. IQR describes observed trial spread, not a confidence interval.","",
           "![Runtime and memory](performance.png)","",
           "| Asset | Method | Refresh | Achieved | Trials | Median optimizer seconds | IQR | Median peak RSS MiB |","|---|---|---|---:|---:|---:|---:|---:|"]
    for r in summaries:
        rss=f"{r['peak_process_rss_bytes']['median']/1048576:.2f}" if r["peak_process_rss_bytes"] else "N/A"
        lines.append(f"| {r['asset_id']} | {r['method']} | {r['variant']} | {r['achieved_triangles']} | {r['trials']} | {r['optimizer_seconds']['median']:.6f} | {r['optimizer_seconds']['iqr']:.6f} | {rss} |")
    lines += ["","## Profiled stage costs","","Single separately instrumented execution per case/variant. These diagnostic times have per-stage clock overhead; they do not enter unprofiled speed comparisons.",""]
    for r in report["rows"]:
        if r["phase"]=="profile":
            p=r["profile"]; stages=sorted([(k,v) for k,v in p.items() if k.endswith("_seconds")],key=lambda kv:-kv[1])
            lines.append(f"- {r['asset_id']}/{r['method']}/{r['target']}/{r['variant']}: "+", ".join(f"{k}={v:.6f}" for k,v in stages[:4])+f"; pushes {p['queue_pushes']}, stale pops {p['stale_pops']}, peak queue entries {p['max_queue_entries']}.")
    lines += ["","## Paired comparisons","","Ratios compare medians only for fully completed tree/sorted cases with identical mesh hashes.",""]
    for c in report["comparisons"]:
        lines.append(f"- {c['asset_id']}/{c['method']}/{c['target']}: tree/sorted optimizer-time ratio {c['median_time_ratio']:.3f}; hash identical: {c['identical_geometry']}.")
    if report["failures"]: lines += ["","Failures: "+str(report["failures"])]
    lines += ["","Each run is a new process. Warm-ups warm OS/file caches, not a persistent optimizer. Variant order alternates by trial to reduce ordering bias. Process wall time includes startup and I/O; optimizer time excludes I/O and engine destruction. RSS is a per-process lifetime high-water mark including input, output, runtime, and optimizer allocations; it is not incremental algorithm-only memory. macOS reports bytes; Linux KiB is converted to bytes; unsupported platforms report null.",
              "","Stage sums exclude constructor allocations/destructors and control/timer overhead. Fine-grained clocks perturb profiles. Queue entries are counts, not byte estimates. Hardware load, thermal state, and allocator noise remain uncontrolled. Points across unrelated assets do not establish asymptotic scaling. No speedup is inferred from different meshes/methods or incomplete trials."]
    (output/"report.md").write_text("\n".join(lines)+"\n")


def run_performance(args,binary,inspector):
    if args.repeats<2 or args.warmups<1 or not math.isfinite(args.timeout) or args.timeout<=0:
        raise ValueError("Use at least two measured repeats, one warm-up, and a positive timeout")
    if any(not math.isfinite(x) or not 0<x<=1 for x in args.retain):
        raise ValueError("Retention fractions must be in (0,1]")
    if args.output_dir.exists() and (not args.output_dir.is_dir() or any(args.output_dir.iterdir())):
        raise ValueError("Output directory must be new or empty")
    output=args.output_dir.resolve(); output.mkdir(parents=True,exist_ok=True)
    variants=list(dict.fromkeys(args.variants)); methods=list(dict.fromkeys(args.methods))
    report={"schema_version":1,"status":"running","config":vars(args).copy(),
            "environment":{"platform":platform.platform(),"machine":platform.machine(),"processor":platform.processor(),"logical_cpus":os.cpu_count(),"python":platform.python_version()},
            "binary":fingerprint(binary),"inspector":fingerprint(inspector),"assets":[],"rows":[],"failures":[],"summaries":[],"comparisons":[]}
    report["config"]={k:[str(x) for x in v] if isinstance(v,list) and v and isinstance(v[0],Path) else str(v) if isinstance(v,Path) else v for k,v in report["config"].items()}
    import json
    for index,source in enumerate(args.inputs):
        source=source.resolve(); asset=f"{index+1:03d}-{source.stem}"
        folder=output/asset; folder.mkdir()
        try:
            result=subprocess.run([inspector,str(source),"--report",str(folder/"original.json")],capture_output=True,text=True,timeout=args.timeout)
            if result.returncode: raise ValueError(result.stderr)
            original=json.loads((folder/"original.json").read_text())
            report["assets"].append({"asset_id":asset,**fingerprint(source),"inspection":original})
        except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
            report["failures"].append({"asset_id":asset,"error":str(exc)}); continue
        targets=sorted({max(1,math.floor(original["triangle_count"]*r)) for r in args.retain},reverse=True)
        for method in methods:
            for target in targets:
                expected=None
                for phase,rounds in (("profile",1),("warmup",args.warmups),("trial",args.repeats)):
                    for iteration in range(rounds):
                        for variant in variants if iteration%2==0 else list(reversed(variants)):
                            case=folder/f"{method}-{target}-{variant}"; case.mkdir(exist_ok=True)
                            mesh=case/"mesh.obj"; destination=case/f"{phase}-{iteration}.json"
                            command=[binary,str(source),"--target",str(target),"--export",str(mesh),"--report",str(destination),
                                     "--method","standard" if method=="boundary" else method,"--boundary-policy","chain" if method=="boundary" else "lock",
                                     "--queue-refresh",variant,"--profile","on" if phase=="profile" else "off"]
                            try:
                                start=time.perf_counter(); result=subprocess.run(command,capture_output=True,text=True,timeout=args.timeout); wall=time.perf_counter()-start
                                save_json(case/f"{phase}-{iteration}-log.json",{"command":command,"returncode":result.returncode,"stdout":result.stdout,"stderr":result.stderr})
                                if result.returncode: raise ValueError(result.stderr)
                                measured=json.loads(destination.read_text()); digest=fingerprint(mesh)["sha256"]
                                if expected is None: expected=digest
                                if digest!=expected: raise ValueError("Output geometry differs across variants or trials")
                                if phase=="profile":
                                    checked=subprocess.run([inspector,str(mesh),"--report",str(case/"inspection.json")],capture_output=True,text=True,timeout=args.timeout)
                                    if checked.returncode: raise ValueError(checked.stderr)
                                    g=json.loads((case/"inspection.json").read_text())["geometry"]
                                    if any(g[k] for k in ("degenerate_triangles","duplicate_triangles","non_manifold_edges","inconsistent_winding_edges")):
                                        raise ValueError("Export failed geometry diagnostics")
                                report["rows"].append({"asset_id":asset,"method":method,"target":target,"variant":variant,"phase":phase,"iteration":iteration,
                                    "original_triangles":original["triangle_count"],"achieved_triangles":measured["achieved_triangles"],"optimizer_seconds":measured["elapsed_seconds"],
                                    "process_wall_seconds":wall,"peak_process_rss_bytes":measured["peak_process_rss_bytes"],"mesh_sha256":digest,"profile":measured["profile"],"compiler":measured["compiler"]})
                            except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
                                report["failures"].append({"asset_id":asset,"method":method,"target":target,"variant":variant,"phase":phase,"iteration":iteration,"error":str(exc)})
                save_json(output/"performance.json",report)
                print(f"{asset}/{method}: target {target:,}; trials completed",flush=True)
    report["summaries"]=aggregate(report["rows"])
    for b in report["summaries"]:
        if b["variant"]!="tree": continue
        for a in report["summaries"]:
            failed=any(all(f.get(k)==a[k] for k in ("asset_id","method","target")) for f in report["failures"])
            if not failed and a["variant"]=="sorted" and all(a[k]==b[k] for k in ("asset_id","method","target")) and a["trials"]==b["trials"]==args.repeats and a["mesh_sha256"]==b["mesh_sha256"]:
                report["comparisons"].append({"asset_id":a["asset_id"],"method":a["method"],"target":a["target"],"identical_geometry":True,"median_time_ratio":b["optimizer_seconds"]["median"]/a["optimizer_seconds"]["median"]})
    report["status"]="partial_failure" if report["failures"] else "complete"
    save_json(output/"performance.json",report)
    with (output/"trials.csv").open("w",newline="") as stream:
        fields=[k for k in report["rows"][0] if k!="profile"] if report["rows"] else ["asset_id","phase"]
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction="ignore"); writer.writeheader(); writer.writerows(report["rows"])
    render(output,report)
    print(f"Performance {report['status']}: {output/'report.md'}",flush=True)
    return 1 if report["failures"] else 0
