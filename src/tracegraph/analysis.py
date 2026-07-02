"""Transitive-dependency queries and test impact analysis."""

from __future__ import annotations

from collections import deque
from collections.abc import Collection

from tracegraph.graph import ModuleGraph, strongly_connected_components
from tracegraph.resolver import Context


def transitive_dependencies(
    graph: ModuleGraph,
    module: str,
    contexts: Collection[Context] | None = None,
) -> set[str]:
    """Every first-party module reachable from ``module``, excluding itself.

    Works on the SCC condensation: cycles collapse into single nodes, then a
    breadth-first walk covers the resulting DAG. All edge contexts count by
    default — a lazy import is still a real dependency.
    """
    if module not in graph.modules:
        raise ValueError(f"unknown module: {module}")
    adjacency = graph.successors(contexts)
    components = strongly_connected_components(adjacency)
    component_of = {
        node: number for number, members in enumerate(components) for node in members
    }
    condensed: list[set[int]] = [set() for _ in components]
    for src, targets in adjacency.items():
        for dst in targets:
            if component_of[src] != component_of[dst]:
                condensed[component_of[src]].add(component_of[dst])

    start = component_of[module]
    reachable = {start}
    queue = deque([start])
    while queue:
        for successor in condensed[queue.popleft()]:
            if successor not in reachable:
                reachable.add(successor)
                queue.append(successor)
    result = {node for number in reachable for node in components[number]}
    result.discard(module)
    return result


def dependents_of(graph: ModuleGraph, module: str) -> set[str]:
    """Every first-party module with an import path to ``module``.

    Reverse-reachability from ``module`` on the reversed graph, across ALL
    edge contexts.
    """
    if module not in graph.modules:
        raise ValueError(f"unknown module: {module}")
    reverse: dict[str, list[str]] = {name: [] for name in graph.modules}
    for edge in graph.edges:
        if edge.src in graph.modules and edge.dst in graph.modules:
            reverse[edge.dst].append(edge.src)
    seen = {module}
    queue = deque([module])
    while queue:
        for importer in reverse[queue.popleft()]:
            if importer not in seen:
                seen.add(importer)
                queue.append(importer)
    seen.discard(module)
    return seen


def affected_tests(graph: ModuleGraph, changed: str) -> set[str]:
    """Test modules whose behavior a change to ``changed`` could affect.

    These are the test modules T with a path T -> ... -> ``changed``: the
    dependents of the change, not its dependencies. All edge contexts count —
    a lazy import is still a runtime dependency, and missing an affected test
    is the dangerous error (recall over precision).
    """
    candidates = dependents_of(graph, changed) | {changed}
    return {name for name in candidates if is_test_module(name)}


def is_test_module(name: str) -> bool:
    """Heuristic test-module check, deliberately generous (recall first)."""
    parts = name.split(".")
    leaf = parts[-1]
    return "tests" in parts or leaf.startswith("test_") or leaf.endswith("_test")
