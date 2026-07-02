"""Command-line interface for TraceGraph."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tracegraph.analysis import affected_tests
from tracegraph.discovery import discover, module_for_path
from tracegraph.graph import build_graph, load_time_cycles
from tracegraph.report import render_dot, render_json, render_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracegraph",
        description="Static dependency analyzer for Python projects.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="Build and summarize the import graph.")
    analyze.add_argument("path", nargs="?", default=".", help="Project root.")
    analyze.add_argument(
        "--format",
        choices=("text", "json", "dot"),
        default="text",
        help="Output format.",
    )

    cycles = sub.add_parser(
        "cycles", help="Report circular imports (load-time edges only)."
    )
    cycles.add_argument("path", nargs="?", default=".", help="Project root.")

    affected = sub.add_parser("affected", help="List tests affected by a changed file.")
    affected.add_argument("--changed", required=True, help="Path to the changed file.")
    affected.add_argument("path", nargs="?", default=".", help="Project root.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.path)
    if not root.is_dir():
        print(f"tracegraph: not a directory: {root}", file=sys.stderr)
        return 2
    project = discover(root)
    graph = build_graph(project)

    if args.command == "analyze":
        if args.format == "json":
            print(render_json(project, graph))
        elif args.format == "dot":
            print(render_dot(graph))
        else:
            print(render_text(project, graph))
        return 0

    if args.command == "cycles":
        cycles = load_time_cycles(graph)
        if not cycles:
            print("no load-time cycles")
            return 0
        for component in cycles:
            print(" <-> ".join(component))
        return 1

    # affected
    module = module_for_path(project, Path(args.changed))
    if module is None:
        print(
            f"tracegraph: {args.changed} is not a module of {project.root}",
            file=sys.stderr,
        )
        return 2
    for test in sorted(affected_tests(graph, module)):
        print(test)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
