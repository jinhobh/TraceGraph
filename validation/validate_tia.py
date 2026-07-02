"""Validate test impact analysis against coverage.py ground truth.

Runs a target project's pytest suite under coverage.py with one dynamic
context per test (the pytest nodeid, installed by ``_context_plugin``). For
each first-party source module, the test modules whose tests actually executed
its lines form the empirical affected set. That ground truth is compared with
the static prediction from ``tracegraph.analysis.affected_tests`` and
summarized as precision, recall, and reduction.

How to read the numbers:

* A **false negative** — coverage saw a test execute the module, TraceGraph
  did not select that test — is a genuine recall failure, the dangerous kind
  of error. The run fails (exit 1) if recall drops below ``--min-recall``.
* A **false positive** is only an upper bound on over-selection: per-test
  contexts cannot attribute import-time execution (a module's top level runs
  once, at collection, under the default context), so a test that depends on a
  module purely through import side effects looks unaffected to coverage even
  though selecting it is correct. Measured precision is therefore a floor.
* **Reduction** is the fraction of ran test modules a prediction leaves out —
  the point of running TIA at all.

Usage:

    uv run python validation/validate_tia.py                  # this repo
    uv run python validation/validate_tia.py /path/to/project --source pkg
    uv run python validation/validate_tia.py --pytest-args "-k resolver"

The target project's test dependencies must be importable from the current
environment; the suite runs with this interpreter.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from coverage import CoverageData

from tracegraph.analysis import affected_tests, is_test_module
from tracegraph.discovery import Project, discover, module_for_path
from tracegraph.graph import ModuleGraph, build_graph

_REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ModuleResult:
    """Predicted vs. observed affected test modules for one source module."""

    module: str
    truth: set[str]
    predicted: set[str]
    not_run: set[str]  # predicted test modules that never ran (excluded above)

    @property
    def true_positives(self) -> set[str]:
        return self.predicted & self.truth

    @property
    def false_positives(self) -> set[str]:
        return self.predicted - self.truth

    @property
    def false_negatives(self) -> set[str]:
        return self.truth - self.predicted

    def precision(self) -> float:
        if not self.predicted:
            return 1.0 if not self.truth else 0.0
        return len(self.true_positives) / len(self.predicted)

    def recall(self) -> float:
        if not self.truth:
            return 1.0
        return len(self.true_positives) / len(self.truth)

    def reduction(self, universe_size: int) -> float:
        if universe_size == 0:
            return 0.0
        return 1.0 - len(self.predicted) / universe_size


def run_suite(
    project_root: Path, data_file: Path, source: str, pytest_args: list[str]
) -> None:
    """Run the target suite under coverage with per-test contexts."""
    command = [
        sys.executable,
        "-m",
        "coverage",
        "run",
        f"--data-file={data_file}",
        f"--source={source}",
        "-m",
        "pytest",
        "-p",
        "validation._context_plugin",
        *pytest_args,
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(_REPO_ROOT), env.get("PYTHONPATH")) if part
    )
    # The sys.monitoring core (default on 3.13+) disables line events after the
    # first hit, so switch_context() would attribute each line only to the
    # first test that ran it. The C tracer records every context correctly.
    env["COVERAGE_CORE"] = "ctrace"
    # Keep stdout clean for the report: the suite's output goes to stderr.
    completed = subprocess.run(command, cwd=project_root, env=env, stdout=sys.stderr)
    if completed.returncode == 1:
        print(
            "validate_tia: warning: some tests failed; "
            "ground truth still reflects what executed",
            file=sys.stderr,
        )
    elif completed.returncode != 0:
        raise SystemExit(f"validate_tia: test run failed (exit {completed.returncode})")


def collect_ground_truth(
    data_file: Path, project: Project
) -> tuple[dict[str, set[str]], set[str]]:
    """Read the coverage database into (module -> covering test modules, universe).

    The universe is every test module that appears in any per-test context —
    i.e. every test module that actually ran.
    """
    data = CoverageData(str(data_file))
    data.read()
    nodeid_cache: dict[str, str | None] = {}

    def test_module_for(context: str) -> str | None:
        if context not in nodeid_cache:
            test_file = project.root / context.split("::", 1)[0]
            nodeid_cache[context] = module_for_path(project, test_file)
        return nodeid_cache[context]

    truth: dict[str, set[str]] = {}
    universe: set[str] = set()
    for filename in data.measured_files():
        covering: set[str] = set()
        for contexts in data.contexts_by_lineno(filename).values():
            for context in contexts:
                if not context:
                    continue  # default context: collection / import time
                test_module = test_module_for(context)
                if test_module is not None:
                    covering.add(test_module)
        universe |= covering
        module = module_for_path(project, filename)
        if module is not None and not is_test_module(module):
            truth[module] = covering
    return truth, universe


def evaluate(
    graph: ModuleGraph, truth: dict[str, set[str]], universe: set[str]
) -> list[ModuleResult]:
    results = []
    for module, covering in sorted(truth.items()):
        predicted_all = affected_tests(graph, module)
        results.append(
            ModuleResult(
                module=module,
                truth=covering,
                predicted=predicted_all & universe,
                not_run=predicted_all - universe,
            )
        )
    return results


def micro_scores(results: list[ModuleResult]) -> tuple[float, float]:
    tp = sum(len(r.true_positives) for r in results)
    fp = sum(len(r.false_positives) for r in results)
    fn = sum(len(r.false_negatives) for r in results)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return precision, recall


def render_report(results: list[ModuleResult], universe: set[str]) -> str:
    width = max((len(r.module) for r in results), default=6)
    header = (
        f"{'module':<{width}}  truth  pred  TP  FP  FN  precision  recall  reduction"
    )
    lines = [header, "-" * len(header)]
    for r in results:
        lines.append(
            f"{r.module:<{width}}  {len(r.truth):>5}  {len(r.predicted):>4}"
            f"  {len(r.true_positives):>2}  {len(r.false_positives):>2}"
            f"  {len(r.false_negatives):>2}  {r.precision():>9.2f}"
            f"  {r.recall():>6.2f}  {r.reduction(len(universe)):>9.2f}"
        )
    precision, recall = micro_scores(results)
    mean_reduction = (
        sum(r.reduction(len(universe)) for r in results) / len(results)
        if results
        else 0.0
    )
    lines.append("")
    lines.append(
        f"test modules ran: {len(universe)}  |  source modules evaluated: "
        f"{len(results)}"
    )
    lines.append(
        f"micro precision: {precision:.2f}  micro recall: {recall:.2f}"
        f"  mean reduction: {mean_reduction:.2f}"
    )
    misses = [(r.module, t) for r in results for t in sorted(r.false_negatives)]
    if misses:
        lines.append("")
        lines.append("FALSE NEGATIVES (tests coverage saw but TIA missed):")
        lines.extend(f"  {module} -> {test}" for module, test in misses)
    else:
        lines.append("false negatives: none")
    not_run = sorted({t for r in results for t in r.not_run})
    if not_run:
        lines.append(
            "predicted but never ran (excluded from scoring): " + ", ".join(not_run)
        )
    lines.append(
        "note: false positives are an upper bound — per-test contexts cannot"
        " attribute import-time execution."
    )
    return "\n".join(lines)


def render_json(results: list[ModuleResult], universe: set[str]) -> str:
    precision, recall = micro_scores(results)
    payload = {
        "test_modules_ran": sorted(universe),
        "micro_precision": precision,
        "micro_recall": recall,
        "mean_reduction": (
            sum(r.reduction(len(universe)) for r in results) / len(results)
            if results
            else 0.0
        ),
        "modules": [
            {
                "module": r.module,
                "truth": sorted(r.truth),
                "predicted": sorted(r.predicted),
                "false_positives": sorted(r.false_positives),
                "false_negatives": sorted(r.false_negatives),
                "not_run": sorted(r.not_run),
                "precision": r.precision(),
                "recall": r.recall(),
                "reduction": r.reduction(len(universe)),
            }
            for r in results
        ],
    }
    return json.dumps(payload, indent=2)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate TraceGraph test impact analysis against coverage.py."
    )
    parser.add_argument("project", nargs="?", default=".", help="Project root.")
    parser.add_argument(
        "--source",
        help="coverage.py --source value (package name or directory). "
        "Defaults to <project>/src when present, else the project root.",
    )
    parser.add_argument(
        "--pytest-args",
        default="",
        help="Extra arguments passed to pytest, as one shell-quoted string.",
    )
    parser.add_argument(
        "--data-file",
        type=Path,
        help="Reuse/keep the coverage data file at this path instead of a "
        "temporary one. If it already exists, the test run is skipped.",
    )
    parser.add_argument(
        "--min-recall",
        type=float,
        default=1.0,
        help="Exit 1 if micro recall falls below this (default: 1.0).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f"validate_tia: not a directory: {root}", file=sys.stderr)
        return 2
    source = args.source or str(root / "src" if (root / "src").is_dir() else root)

    with tempfile.TemporaryDirectory(prefix="tracegraph-tia-") as tmp:
        data_file = args.data_file or Path(tmp) / "coverage.tia"
        if not data_file.exists():
            run_suite(root, data_file, source, shlex.split(args.pytest_args))
        project = discover(root)
        truth, universe = collect_ground_truth(data_file, project)

    if not universe:
        print(
            "validate_tia: no per-test contexts recorded — did any tests run?",
            file=sys.stderr,
        )
        return 2
    results = evaluate(build_graph(project), truth, universe)
    print(
        render_json(results, universe)
        if args.json
        else render_report(results, universe)
    )
    _, recall = micro_scores(results)
    return 0 if recall >= args.min_recall else 1


if __name__ == "__main__":
    raise SystemExit(main())
