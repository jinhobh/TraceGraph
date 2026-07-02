"""Graph structure and hand-rolled Tarjan SCC tests."""

from __future__ import annotations

from tracegraph.graph import (
    ModuleGraph,
    load_time_cycles,
    strongly_connected_components,
)
from tracegraph.resolver import Context, Edge


def edge(src: str, dst: str, context: Context = "module") -> Edge:
    return Edge(src, dst, context, resolved=True, category="first_party")


def graph_of(edges: list[Edge]) -> ModuleGraph:
    nodes = {e.src for e in edges} | {e.dst for e in edges}
    return ModuleGraph(modules=frozenset(nodes), edges=tuple(edges))


def test_scc_simple_cycle() -> None:
    adjacency = {"a": ["b"], "b": ["c"], "c": ["a"], "d": ["a"]}
    components = strongly_connected_components(adjacency)
    assert ["a", "b", "c"] in components
    assert ["d"] in components


def test_scc_dag_reverse_topological_order() -> None:
    adjacency = {"a": ["b"], "b": ["c"], "c": []}
    assert strongly_connected_components(adjacency) == [["c"], ["b"], ["a"]]


def test_scc_two_separate_cycles() -> None:
    adjacency = {"a": ["b"], "b": ["a", "c"], "c": ["d"], "d": ["c"]}
    components = strongly_connected_components(adjacency)
    assert sorted(tuple(c) for c in components) == [("a", "b"), ("c", "d")]


def test_scc_deep_chain_does_not_recurse() -> None:
    # Iterative implementation must survive chains far beyond the default
    # Python recursion limit.
    size = 5000
    adjacency = {str(i): [str(i + 1)] for i in range(size)}
    adjacency[str(size)] = []
    components = strongly_connected_components(adjacency)
    assert len(components) == size + 1


def test_module_cycle_detected() -> None:
    graph = graph_of([edge("a", "b"), edge("b", "a")])
    assert load_time_cycles(graph) == [["a", "b"]]


def test_function_cycle_is_not_a_load_time_cycle() -> None:
    graph = graph_of([edge("a", "b"), edge("b", "a", "function")])
    assert load_time_cycles(graph) == []


def test_type_checking_cycle_is_not_a_load_time_cycle() -> None:
    graph = graph_of([edge("a", "b"), edge("b", "a", "type_checking")])
    assert load_time_cycles(graph) == []


def test_successors_filters_by_context() -> None:
    graph = graph_of(
        [edge("a", "b"), edge("a", "c", "function"), edge("a", "d", "type_checking")]
    )
    module_only: tuple[Context, ...] = ("module",)
    assert graph.successors(contexts=module_only)["a"] == ["b"]
    assert sorted(graph.successors()["a"]) == ["b", "c", "d"]


def test_external_targets_not_in_adjacency() -> None:
    external = Edge("a", "os", "module", resolved=True, category="stdlib")
    graph = ModuleGraph(modules=frozenset({"a"}), edges=(external,))
    assert graph.successors() == {"a": []}
    assert graph.external_targets == frozenset({"os"})
