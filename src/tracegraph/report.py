"""Render the module graph as human-readable text, JSON, or Graphviz DOT."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from tracegraph.discovery import Project
from tracegraph.graph import ModuleGraph, load_time_cycles
from tracegraph.resolver import Edge

_EDGE_STYLE = {
    "module": "solid",
    "function": "dashed",
    "type_checking": "dotted",
}


def render_text(project: Project, graph: ModuleGraph) -> str:
    """Human-readable summary of the graph and its diagnostics."""
    packages = sum(1 for module in project.modules.values() if module.is_package)
    by_context = Counter(edge.context for edge in graph.edges)
    lines = [
        f"TraceGraph: {project.root}",
        (
            f"  modules: {len(graph.modules)} first-party"
            f" ({packages} packages), {len(graph.external_targets)} external"
        ),
        (
            f"  edges: {len(graph.edges)}"
            f" (module {by_context['module']},"
            f" function {by_context['function']},"
            f" type_checking {by_context['type_checking']})"
        ),
    ]
    cycles = load_time_cycles(graph)
    if cycles:
        lines.append(f"  load-time cycles: {len(cycles)}")
        for component in cycles:
            lines.append("    " + " <-> ".join(component))
    else:
        lines.append("  load-time cycles: none")
    if graph.unresolved_edges:
        lines.append("  unresolved imports (static analysis blind spots):")
        for edge in graph.unresolved_edges:
            lines.append(f"    {edge.src} -> {edge.dst} [{edge.context}]")
    if project.parse_errors:
        lines.append("  parse errors (files skipped):")
        for error in project.parse_errors:
            lines.append(f"    {error.path}: {error.message}")
    return "\n".join(lines)


def render_json(project: Project, graph: ModuleGraph) -> str:
    """Machine-readable dump of the graph and its diagnostics."""
    payload: dict[str, Any] = {
        "root": str(project.root),
        "modules": sorted(graph.modules),
        "external": sorted(graph.external_targets),
        "edges": [_edge_dict(edge) for edge in graph.edges],
        "cycles": load_time_cycles(graph),
        "unresolved": [_edge_dict(edge) for edge in graph.unresolved_edges],
        "parse_errors": [
            {"path": str(error.path), "message": error.message}
            for error in project.parse_errors
        ],
    }
    return json.dumps(payload, indent=2)


def render_dot(graph: ModuleGraph) -> str:
    """Graphviz DOT export; edge style encodes the import context."""
    lines = ["digraph tracegraph {", "  rankdir=LR;"]
    for name in sorted(graph.modules):
        lines.append(f'  "{name}";')
    for name in sorted(graph.external_targets):
        lines.append(f'  "{name}" [shape=box, style=dashed];')
    for edge in graph.edges:
        attrs = [f"style={_EDGE_STYLE[edge.context]}"]
        if not edge.resolved:
            attrs.append("color=red")
        lines.append(f'  "{edge.src}" -> "{edge.dst}" [{", ".join(attrs)}];')
    lines.append("}")
    return "\n".join(lines)


def _edge_dict(edge: Edge) -> dict[str, Any]:
    return {
        "src": edge.src,
        "dst": edge.dst,
        "context": edge.context,
        "resolved": edge.resolved,
        "category": edge.category,
        "binding": edge.binding,
    }
