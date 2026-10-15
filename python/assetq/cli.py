"""Thin experiment entry point around the C++ mesh implementation."""
import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys


def find_binary(name, explicit, parser):
    binary = explicit or shutil.which(name)
    if not binary:
        local = Path(__file__).resolve().parents[2] / "build" / name
        if local.is_file():
            binary = local
    if not binary:
        parser.error(f"Build {name} first, then use --binary or add it to PATH")
    return str(Path(binary).resolve())


def simplify(args, parser):
    binary = find_binary("assetq-simplify", args.binary, parser)
    if any(not math.isfinite(r) or not 0 < r <= 1 for r in args.retain):
        parser.error("--retain fractions must be finite and in (0, 1]")
    # C++ remains the source of truth for parsing/validating the mesh. Obtain its
    # triangle count using the existing inspector, rather than a second OBJ parser.
    inspector = find_binary("assetq-inspect", args.inspector, parser)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    inspection = args.output_dir / "original-inspection.json"
    result = subprocess.run([inspector, str(args.input), "--report", str(inspection)], capture_output=True, text=True)
    if result.returncode:
        print(result.stderr.strip(), file=sys.stderr)
        return result.returncode
    count = json.loads(inspection.read_text())["triangle_count"]
    targets = sorted({max(1, math.floor(count * r)) for r in args.retain}, reverse=True)
    # Protect the source even if it happens to live inside the output directory.
    planned = [args.output_dir / "summary.json"]
    for target in targets:
        planned += [args.output_dir / f"mesh-{target}.obj", args.output_dir / f"mesh-{target}.json"]
    if any(p.resolve() == args.input.resolve() or (p.exists() and args.input.exists() and p.samefile(args.input)) for p in planned):
        parser.error("An output path aliases the input; choose a different output directory")
    reports = []
    for target in targets:
        exported = args.output_dir / f"mesh-{target}.obj"
        report_path = args.output_dir / f"mesh-{target}.json"
        result = subprocess.run([binary, str(args.input), "--target", str(target), "--export", str(exported), "--report", str(report_path),
                                 "--method",args.method,"--feature-angle",str(args.feature_angle),"--feature-weight",str(args.feature_weight),
                                 "--curvature-strength",str(args.curvature_strength),"--boundary-policy",args.boundary_policy,
                                 "--queue-refresh",args.queue_refresh], capture_output=True, text=True)
        if result.returncode:
            print(result.stderr.strip(), file=sys.stderr)
            return result.returncode
        report = json.loads(report_path.read_text())
        report.update(mesh_path=str(exported), report_path=str(report_path))
        reports.append(report)
        print(result.stdout.strip())
    summary = {"schema_version": 1, "input": str(args.input), "requested_retention_fractions": args.retain,
               "original_triangles": count, "method":args.method,"versions": reports}
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Results: {args.output_dir / 'summary.json'}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="Inspect a triangle OBJ and optionally re-export it")
    inspect.add_argument("input", type=Path)
    inspect.add_argument("--report", type=Path, required=True)
    inspect.add_argument("--export", dest="export", type=Path)
    inspect.add_argument("--normalize", action="store_true")
    inspect.add_argument("--binary", type=Path, help="Path to assetq-inspect")
    optimize = sub.add_parser("simplify", help="Run independent C++ QEM triangle budgets")
    optimize.add_argument("input", type=Path)
    optimize.add_argument("--output-dir", type=Path, required=True)
    optimize.add_argument("--retain", type=float, nargs="+", default=[0.75, 0.5, 0.25])
    optimize.add_argument("--binary", type=Path, help="Path to assetq-simplify")
    optimize.add_argument("--inspector", type=Path, help="Path to assetq-inspect")
    optimize.add_argument("--method", choices=["standard","feature","curvature","combined"], default="standard")
    optimize.add_argument("--curvature-strength",type=float,default=4)
    optimize.add_argument("--boundary-policy",choices=["lock","chain"],default="lock")
    optimize.add_argument("--queue-refresh",choices=["tree","sorted"],default="sorted")
    optimize.add_argument("--feature-angle", type=float, default=45, help="Original dihedral crease threshold in degrees")
    optimize.add_argument("--feature-weight", type=float, default=10, help="Additional crease-plane quadric weight")
    evaluation = sub.add_parser("evaluate", help="Measure bidirectional surface distances using NVIDIA Warp")
    evaluation.add_argument("original", type=Path)
    evaluation.add_argument("simplified", type=Path, nargs="+")
    evaluation.add_argument("--report", type=Path, required=True)
    evaluation.add_argument("--samples", type=int, default=100000, help="Samples per direction (default: 100000)")
    evaluation.add_argument("--seed", type=int, default=42)
    evaluation.add_argument("--device", default="cpu", help="Warp device, e.g. cpu or cuda:0")
    evaluation.add_argument("--feature-samples",type=int,default=0)
    evaluation.add_argument("--feature-angle",type=float,default=45)
    benchmark = sub.add_parser("benchmark", help="Run asset/budget/seed experiments and plot tradeoffs")
    benchmark.add_argument("inputs", type=Path, nargs="+")
    benchmark.add_argument("--output-dir", type=Path, required=True, help="New or empty directory")
    benchmark.add_argument("--retain", type=float, nargs="+", default=[0.75,0.5,0.25])
    benchmark.add_argument("--samples", type=int, default=100000)
    benchmark.add_argument("--seeds", type=int, nargs="+", default=[42])
    benchmark.add_argument("--device", default="cpu")
    benchmark.add_argument("--max-p95", type=float, help="Max worst-direction P95 as a fraction of original diagonal")
    benchmark.add_argument("--binary", type=Path, help="Path to assetq-simplify")
    benchmark.add_argument("--inspector", type=Path)
    benchmark.add_argument("--methods",nargs="+",choices=["standard","constraints","feature","curvature","combined","boundary","fast"],default=["standard"])
    benchmark.add_argument("--curvature-strength",type=float,default=4)
    benchmark.add_argument("--feature-angle",type=float,default=45)
    benchmark.add_argument("--feature-weight",type=float,default=10)
    benchmark.add_argument("--feature-samples",type=int,default=2000)
    performance=sub.add_parser("performance",help="Profile stages and compare repeated unprofiled runtime/memory trials")
    performance.add_argument("inputs",type=Path,nargs="+")
    performance.add_argument("--output-dir",type=Path,required=True)
    performance.add_argument("--retain",type=float,nargs="+",default=[.25])
    performance.add_argument("--methods",nargs="+",choices=["standard","feature","curvature","combined","boundary"],default=["standard"])
    performance.add_argument("--variants",nargs="+",choices=["tree","sorted"],default=["tree","sorted"])
    performance.add_argument("--repeats",type=int,default=5)
    performance.add_argument("--warmups",type=int,default=1)
    performance.add_argument("--timeout",type=float,default=300)
    performance.add_argument("--build-label",default="unspecified; supply compiler flags for reproducibility")
    performance.add_argument("--binary",type=Path)
    performance.add_argument("--inspector",type=Path)
    args = parser.parse_args()
    if args.command=="performance":
        try:
            from .performance import run_performance
            return run_performance(args,find_binary("assetq-simplify",args.binary,parser),find_binary("assetq-inspect",args.inspector,parser))
        except (ImportError,OSError,ValueError,RuntimeError) as exc:
            print(f"assetq: {exc}",file=sys.stderr)
            return 1
    if args.command == "benchmark":
        try:
            from .benchmark import run_benchmark
            return run_benchmark(args, find_binary("assetq-simplify",args.binary,parser),
                                 find_binary("assetq-inspect",args.inspector,parser))
        except ImportError as exc:
            print(f"Benchmark requires extras: pip install -e '.[benchmark]' ({exc})",file=sys.stderr)
            return 1
        except (OSError,ValueError,RuntimeError) as exc:
            print(f"assetq: {exc}",file=sys.stderr)
            return 1
    if args.command == "evaluate":
        try:
            from .evaluate import run_cli
            return run_cli(args)
        except ImportError as exc:
            print(f"Evaluation requires numpy and warp-lang: install with pip install -e '.[evaluation]' ({exc})", file=sys.stderr)
            return 1
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"assetq: {exc}", file=sys.stderr)
            return 1
    if args.command == "simplify":
        try:
            return simplify(args, parser)
        except (OSError, ValueError) as exc:
            print(f"assetq: {exc}", file=sys.stderr)
            return 1
    binary = find_binary("assetq-inspect", args.binary, parser)
    if args.normalize and not args.export:
        parser.error("--normalize requires --export")
    command = [str(Path(binary).resolve()), str(args.input), "--report", str(args.report)]
    if args.export:
        command += ["--export", str(args.export)]
    if args.normalize:
        command += ["--normalize"]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            print(result.stderr.strip(), file=sys.stderr)
            return result.returncode
        report = json.loads(args.report.read_text())
    except (OSError, ValueError) as exc:
        print(f"assetq: {exc}", file=sys.stderr)
        return 1
    print(f"Vertices: {report['vertex_count']:,} | Triangles: {report['triangle_count']:,} | Bytes: {report['file_size_bytes']:,}")
    for key, value in report["geometry"].items():
        if key != "degenerate_relative_double_area_threshold":
            print(f"{key.replace('_', ' ').capitalize()}: {value}")
    print(f"Report: {args.report}")
    return 0
