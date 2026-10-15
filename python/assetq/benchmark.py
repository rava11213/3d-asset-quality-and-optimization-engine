"""Sequential experiments with inspectable artifacts and explicit partial failures."""
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import time
import sys

# Matplotlib must not write into the user's global config/cache directory.
os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[2]/"work/matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from .evaluate import evaluate


def save_json(path, value):
    path.write_text(json.dumps(value,indent=2,allow_nan=False)+"\n")


def fingerprint(path):
    return {"path":str(Path(path).resolve()),"sha256":hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def invoke(command, log):
    result = subprocess.run(list(map(str,command)),capture_output=True,text=True)
    save_json(log,{"command":list(map(str,command)),"returncode":result.returncode,
                   "stdout":result.stdout,"stderr":result.stderr})
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"Command exited {result.returncode}")


def summarize(rows, seeds):
    """One measured point per asset/budget; never pretend a failed seed passed."""
    groups = {}
    for row in rows:
        groups.setdefault((row["asset_id"],row.get("method","standard"),row["target_triangles"]),[]).append(row)
    points = []
    for (asset,method,target), group in groups.items():
        complete = {r["seed"] for r in group} == set(seeds)
        first = group[0]
        points.append({"asset_id":asset,"method":method,"target_triangles":target,
                       "achieved_triangles":first["achieved_triangles"],
                       "target_reached":first["target_reached"],
                       "triangle_reduction":first["triangle_reduction"],
                       "mesh_path":first["mesh_path"],"complete":complete,"successful_seeds":len(group),
                       "worst_seed_direction_p95":max(r["worst_direction_p95"] for r in group),
                       "mean_pooled_error":sum(r["mean"] for r in group)/len(group),
                       "normal_p95_degrees":max(r.get("normal_p95_degrees",0) for r in group),
                       "feature_statuses":sorted({r.get("feature_status","disabled") for r in group}),
                       "feature_p95":max((r["feature_p95"] for r in group if r.get("feature_p95") is not None),default=None)})
    return points


def select_versions(points, assets, threshold):
    selected = {}
    for asset in assets:
        passing = [p for p in points if p["asset_id"]==asset and p["complete"] and p["worst_seed_direction_p95"]<=threshold]
        best = min(passing,key=lambda p:(p["achieved_triangles"],p["worst_seed_direction_p95"],p["target_triangles"])) if passing else None
        selected[asset] = best
    return selected


def matched_comparisons(points):
    """No numerical winner is asserted when budgets achieve different counts."""
    baseline = {(p["asset_id"],p["target_triangles"]):p for p in points if p["method"]=="standard"}
    output = []
    for p in points:
        b = baseline.get((p["asset_id"],p["target_triangles"]))
        if p["method"]=="standard" or b is None:
            continue
        eligible = p["complete"] and b["complete"] and p["achieved_triangles"]==b["achieved_triangles"]
        output.append({"asset_id":p["asset_id"],"method":p["method"],"target_triangles":p["target_triangles"],
                       "baseline_achieved":b["achieved_triangles"],"method_achieved":p["achieved_triangles"],
                       "matched_complete":eligible,
                       "surface_p95_delta":p["worst_seed_direction_p95"]-b["worst_seed_direction_p95"] if eligible else None,
                       "normal_p95_degrees_delta":p["normal_p95_degrees"]-b["normal_p95_degrees"] if eligible else None,
                       "feature_p95_delta":p["feature_p95"]-b["feature_p95"] if eligible and p["feature_p95"] is not None and b["feature_p95"] is not None else None})
    return output


def plot_methods(points,output):
    assets = sorted({p["asset_id"] for p in points})
    if not assets:
        return
    fig,axes = plt.subplots(len(assets),3,figsize=(14,3.5*len(assets)),squeeze=False,layout="constrained")
    metrics = [("worst_seed_direction_p95",100,"Surface P95 (% diagonal)"),
               ("normal_p95_degrees",1,"Normal P95 (degrees)"),("feature_p95",100,"Crease P95 (% diagonal)")]
    for i,asset in enumerate(assets):
        for j,(key,scale,label) in enumerate(metrics):
            ax = axes[i,j]
            for method in sorted({p["method"] for p in points if p["asset_id"]==asset}):
                group = sorted([p for p in points if p["asset_id"]==asset and p["method"]==method and p["complete"] and p[key] is not None],key=lambda p:p["triangle_reduction"])
                if group:
                    ax.plot([p["triangle_reduction"]*100 for p in group],[p[key]*scale for p in group],marker="o",label=method)
            ax.set(title=asset,xlabel="Triangle reduction (%)",ylabel=label)
            ax.set_ylim(bottom=0)
            ax.grid(alpha=.25)
            handles,_ = ax.get_legend_handles_labels()
            if handles:
                ax.legend(fontsize=8)
            else:
                ax.text(.5,.5,"Not measured / no comparable features",transform=ax.transAxes,ha="center",fontsize=8)
    fig.savefig(output/"method-comparison.png",dpi=160)
    fig.savefig(output/"method-comparison.svg")
    plt.close(fig)


def plot_tradeoffs(points, output, threshold):
    fig, ax = plt.subplots(figsize=(8,5),layout="constrained")
    for asset,method in sorted({(p["asset_id"],p.get("method","standard")) for p in points}):
        group = sorted([p for p in points if p["asset_id"]==asset and p.get("method","standard")==method and p["complete"]],key=lambda p:p["triangle_reduction"])
        if group:
            ax.plot([p["triangle_reduction"]*100 for p in group],
                    [p["worst_seed_direction_p95"]*100 for p in group],marker="o",label=f"{asset} / {method}")
    if threshold is not None:
        ax.axhline(threshold*100,color="#a33",linestyle="--",label="Requested threshold")
    if not any(p["complete"] for p in points):
        ax.text(0.5,0.5,"No complete evaluations",transform=ax.transAxes,ha="center")
    ax.set(xlabel="Triangle reduction (%)",ylabel="Worst-direction P95 (% of original diagonal)",
           title="Measured geometry tradeoffs (maximum across requested seeds)")
    ax.set_ylim(bottom=0)
    ax.grid(alpha=0.25)
    handles,_ = ax.get_legend_handles_labels()
    if handles:
        ax.legend(fontsize=8)
    fig.savefig(output/"tradeoffs.png",dpi=180)
    fig.savefig(output/"tradeoffs.svg")
    plt.close(fig)


def write_report(output, report):
    points, failures = report["points"],report["failures"]
    lines = ["# Asset quality benchmark", "",f"Status: **{report['status']}**. {len(report['rows'])} successful evaluations; {len(failures)} failures.","",
             "Errors are fractions of the original bounding-box diagonal in JSON/CSV, and percentages below.",
             "P95 is the larger directional P95, then the maximum across requested seeds. Only fully evaluated budgets appear in the chart.","",
             "![Triangle reduction versus surface error](tradeoffs.png)","",
             "| Asset | Method | Target | Achieved | Reduction | Worst P95 | Normal P95 | Crease P95 | Feature status | Seeds complete |", "|---|---|---:|---:|---:|---:|---:|---:|---|---|"]
    for p in points:
        feature = f"{p['feature_p95']*100:.6f}%" if p['feature_p95'] is not None else "N/A"
        lines.append(f"| {p['asset_id']} | {p['method']} | {p['target_triangles']:,} | {p['achieved_triangles']:,} | {p['triangle_reduction']*100:.2f}% | {p['worst_seed_direction_p95']*100:.6f}% | {p['normal_p95_degrees']:.4f}° | {feature} | {', '.join(p['feature_statuses'])} | {p['complete']} |")
    if points:
        lines += ["","![Method comparisons](method-comparison.png)","",
                  "Normal errors use oriented face normals at nearest surface points. Creases are independently detected at the configured angle; missing features are N/A, not zero error.","",
                  "## Comparisons with standard QEM", "", "Only complete runs at exactly equal achieved triangle counts receive numerical deltas. Negative deltas favor the compared method; positive deltas favor standard QEM. Differences near floating-point precision are not meaningful.","",
                  "| Asset | Method | Target | Counts matched | Surface P95 delta (% diagonal) |", "|---|---|---:|---|---:|"]
        for c in report["comparisons"]:
            delta = f"{c['surface_p95_delta']*100:+.6f}" if c["matched_complete"] else "N/A"
            lines.append(f"| {c['asset_id']} | {c['method']} | {c['target_triangles']} | {c['matched_complete']} | {delta} |")
    if report["config"]["max_p95"] is not None:
        lines += ["","## Smallest tested versions within threshold", "",
                  f"Threshold: {report['config']['max_p95']*100:.6f}% of original diagonal; all requested seeds must pass.",""]
        for asset,p in report["selections"].items():
            lines.append(f"- {asset}: {p['method']}, {p['achieved_triangles']:,} triangles (`{p['mesh_path']}`)." if p else f"- {asset}: no fully evaluated tested version qualifies.")
    if failures:
        lines += ["","## Failures",""]
        for f in failures:
            lines.append(f"- {f['asset_id']} / {f['stage']} / target {f.get('target', 'n/a')} / seed {f.get('seed','n/a')}: {f['error'].replace(chr(10),' ')}")
    lines += ["","Reports use actual achieved counts, including budgets that stop early. Lines connect measured points; they do not establish error between tested budgets.",
              "", "External library timing and boundary/topology policies differ from local QEM; timings here are not a controlled performance comparison. Inspection diagnostics are retained for every export.",
              "", "Sampled surface distances are estimates, not exact Hausdorff distance or visual accuracy. Text OBJ bytes may increase despite triangle reduction.",
              "", "Full configuration, source and executable hashes, per-seed metrics, timings, and failures are in `benchmark.json`; flat successful evaluation rows are in `results.csv`."]
    (output/"report.md").write_text("\n".join(lines)+"\n")


def run_benchmark(args, binary, inspector):
    if not math.isfinite(args.curvature_strength) or not 0<=args.curvature_strength<=1000:
        raise ValueError("Curvature strength must be finite and in [0,1000]")
    if not math.isfinite(args.feature_angle) or not 0<args.feature_angle<180 or args.feature_samples<0:
        raise ValueError("Feature angle must be in (0,180) and sample count nonnegative")
    if not math.isfinite(args.feature_weight) or not 0<=args.feature_weight<=1e6:
        raise ValueError("Feature weight must be finite and in [0,1000000]")
    if args.samples<1 or any(s<0 for s in args.seeds):
        raise ValueError("Samples must be positive and seeds nonnegative")
    if any(not math.isfinite(r) or not 0<r<=1 for r in args.retain):
        raise ValueError("Retention fractions must be finite and in (0, 1]")
    if args.max_p95 is not None and (not math.isfinite(args.max_p95) or args.max_p95<0):
        raise ValueError("--max-p95 must be a finite nonnegative fraction")
    if args.output_dir.exists() and (not args.output_dir.is_dir() or any(args.output_dir.iterdir())):
        raise ValueError("Benchmark output must be a new or empty directory; choose a new run directory")
    if len({p.resolve() for p in args.inputs})!=len(args.inputs):
        raise ValueError("Duplicate input paths are not allowed")
    output = args.output_dir.resolve()
    output.mkdir(parents=True,exist_ok=True)
    seeds = sorted(set(args.seeds))
    started = time.perf_counter()
    report = {"schema_version":2,"started_utc":datetime.now(timezone.utc).isoformat(),
              "status":"running","config":{"retain":args.retain,"samples_per_direction":args.samples,
              "seeds":seeds,"device":args.device,"max_p95":args.max_p95,
              "methods":args.methods,"feature_angle":args.feature_angle,"feature_weight":args.feature_weight,"curvature_strength":args.curvature_strength,
              "feature_samples":args.feature_samples,"selection_metric":"max_over_seeds_of_worst_direction_p95_normalized"},
              "environment":{"python":platform.python_version(),"platform":platform.platform(),
              "matplotlib":matplotlib.__version__},
              "executables":{"simplifier":fingerprint(binary),"inspector":fingerprint(inspector)},
              "assets":[],"rows":[],"failures":[],"points":[],"selections":{}}
    for index,source in enumerate(args.inputs):
        source = source.resolve()
        slug = re.sub(r"[^a-zA-Z0-9_-]","_",source.stem)[:60] or "asset"
        asset = f"{index+1:03d}-{slug}"
        directory = output/asset
        directory.mkdir()
        item = {"asset_id":asset,"input":str(source)}
        report["assets"].append(item)
        print(f"[{index+1}/{len(args.inputs)}] {source.name}",flush=True)
        try:
            item.update(fingerprint(source))
            invoke([inspector,source,"--report",directory/"original-inspection.json"],directory/"inspection-log.json")
            original = json.loads((directory/"original-inspection.json").read_text())
            item["inspection"] = original
        except (OSError,ValueError,RuntimeError) as exc:
            report["failures"].append({"asset_id":asset,"stage":"inspection","error":str(exc)})
            save_json(output/"benchmark.json",report)
            continue
        targets = sorted({max(1,math.floor(original["triangle_count"]*r)) for r in args.retain},reverse=True)
        asset_directory=directory
        for method in dict.fromkeys(args.methods):
            directory=asset_directory/method
            directory.mkdir()
            for target in targets:
                mesh = directory/f"mesh-{target}.obj"
                optimization_path = directory/f"mesh-{target}.json"
                try:
                    if method=="fast":
                        command=[sys.executable,"-m","assetq.external",source,target,mesh,optimization_path]
                    else:
                        command=[binary,source,"--target",target,"--export",mesh,"--report",optimization_path,
                                 "--method","feature" if method=="constraints" else method,
                                 "--feature-angle",args.feature_angle,"--feature-weight",0 if method=="constraints" else args.feature_weight,
                                 "--curvature-strength",args.curvature_strength]
                    invoke(command,directory/f"simplify-{target}-log.json")
                    optimization = json.loads(optimization_path.read_text())
                    invoke([inspector,mesh,"--report",directory/f"inspection-{target}.json"],directory/f"inspect-{target}-log.json")
                    geometry = json.loads((directory/f"inspection-{target}.json").read_text())["geometry"]
                except (OSError,ValueError,RuntimeError) as exc:
                    report["failures"].append({"asset_id":asset,"method":method,"stage":"simplification","target":target,"error":str(exc)})
                    continue
                for seed in seeds:
                    try:
                        evaluation = evaluate(source,mesh,args.samples,seed,args.device,args.feature_samples,args.feature_angle)
                        save_json(directory/f"evaluation-{target}-seed-{seed}.json",evaluation)
                        stats = evaluation["normalized"]["symmetric_pooled"]
                        report["rows"].append({"asset_id":asset,"method":method,"input":str(source),"mesh_path":str(mesh),
                            "original_triangles":original["triangle_count"],"target_triangles":target,
                            "achieved_triangles":optimization["achieved_triangles"],"target_reached":optimization["target_reached"],
                            "triangle_reduction":optimization["triangle_reduction"],
                            "original_file_bytes":evaluation["original"]["file_size_bytes"],
                            "output_file_bytes":evaluation["simplified"]["file_size_bytes"],
                            "byte_reduction":1-evaluation["simplified"]["file_size_bytes"]/evaluation["original"]["file_size_bytes"],
                            "seed":seed,"samples_per_direction":args.samples,**stats,
                            "worst_direction_p95":evaluation["worst_direction_p95_normalized"],
                            "normal_p95_degrees":evaluation["worst_direction_normal_p95_degrees"],
                            "feature_status":evaluation["feature_metrics"]["status"] if evaluation["feature_metrics"] else "disabled",
                            "feature_p95":evaluation["feature_metrics"]["worst_direction_p95"] if evaluation["feature_metrics"] else None,
                            "optimization_report":str(optimization_path),
                            "degenerate_triangles":geometry["degenerate_triangles"],
                            "duplicate_triangles":geometry["duplicate_triangles"],
                            "non_manifold_edges":geometry["non_manifold_edges"],
                            "inconsistent_winding_edges":geometry["inconsistent_winding_edges"],
                            "original_to_simplified_p95":evaluation["normalized"]["original_to_simplified"]["p95"],
                            "simplified_to_original_p95":evaluation["normalized"]["simplified_to_original"]["p95"],
                            "simplification_seconds":optimization["elapsed_seconds"],"evaluation_seconds":evaluation["elapsed_seconds"],
                            "warp_version":evaluation["warp_version"],"numpy_version":evaluation["numpy_version"],"device":evaluation["device"]})
                        print(f"  {method}: target {target:,}: achieved {optimization['achieved_triangles']:,}, seed {seed}, P95 {evaluation['worst_direction_p95_normalized']*100:.5f}%",flush=True)
                    except (OSError,ValueError,RuntimeError) as exc:
                        report["failures"].append({"asset_id":asset,"method":method,"stage":"evaluation","target":target,"seed":seed,"error":str(exc)})
                save_json(output/"benchmark.json",report)
    report["points"] = summarize(report["rows"],seeds)
    report["comparisons"] = matched_comparisons(report["points"])
    if args.max_p95 is not None:
        report["selections"] = select_versions(report["points"],[a["asset_id"] for a in report["assets"]],args.max_p95)
    report["status"] = "partial_failure" if report["failures"] else "complete"
    report["elapsed_seconds"] = time.perf_counter()-started
    save_json(output/"benchmark.json",report)
    with (output/"results.csv").open("w",newline="") as stream:
        fields = list(report["rows"][0]) if report["rows"] else ["asset_id","target_triangles","seed","worst_direction_p95"]
        writer = csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["rows"])
    plot_tradeoffs(report["points"],output,args.max_p95)
    plot_methods(report["points"],output)
    write_report(output,report)
    print(f"Benchmark {report['status']}: {output/'report.md'}",flush=True)
    return 1 if report["failures"] else 0
