"""Graph structure and hand-rolled Tarjan SCC tests."""

from __future__ import annotations

from pathlib import Path

from tracegraph.discovery import discover
from tracegraph.graph import (
    ModuleGraph,
    build_graph,
    load_time_cycles,
    strongly_connected_components,
)
from tracegraph.resolver import Binding, Context, Edge

FIXTURES = Path(__file__).parent / "fixtures"


def edge(
    src: str, dst: str, context: Context = "module", binding: Binding = "symbol"
) -> Edge:
    return Edge(
        src, dst, context, resolved=True, category="first_party", binding=binding
    )


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


def test_module_binding_cycle_is_not_a_load_time_cycle() -> None:
    # A cycle realized purely through plain ``import x`` statements binds
    # partially initialized module objects and imports fine.
    graph = graph_of(
        [edge("a", "b", binding="module"), edge("b", "a", binding="module")]
    )
    assert load_time_cycles(graph) == []


def test_one_symbol_edge_makes_the_cycle_load_time() -> None:
    graph = graph_of(
        [edge("a", "b", binding="symbol"), edge("b", "a", binding="module")]
    )
    assert load_time_cycles(graph) == [["a", "b"]]


def test_symbol_edge_outside_the_cycle_does_not_flag_it() -> None:
    # c -> a needs a symbol, but c is not part of the a <-> b cycle; the
    # cycle itself is all module bindings and stays benign.
    graph = graph_of(
        [
            edge("a", "b", binding="module"),
            edge("b", "a", binding="module"),
            edge("c", "a", binding="symbol"),
        ]
    )
    assert load_time_cycles(graph) == []


def test_successors_filters_by_context() -> None:
    graph = graph_of(
        [edge("a", "b"), edge("a", "c", "function"), edge("a", "d", "type_checking")]
    )
    module_only: tuple[Context, ...] = ("module",)
    assert graph.successors(contexts=module_only)["a"] == ["b"]
    assert sorted(graph.successors()["a"]) == ["b", "c", "d"]


def test_external_targets_not_in_adjacency() -> None:
    external = Edge(
        "a", "os", "module", resolved=True, category="stdlib", binding="module"
    )
    graph = ModuleGraph(modules=frozenset({"a"}), edges=(external,))
    assert graph.successors() == {"a": []}
    assert graph.external_targets == frozenset({"os"})


def cycles_for_fixture(case: str) -> list[list[str]]:
    return load_time_cycles(build_graph(discover(FIXTURES / case / "project")))


def test_from_import_cycle_fixture_is_load_time() -> None:
    assert cycles_for_fixture("cycle") == [["pkg.a", "pkg.b"]]


def test_plain_import_cycle_fixture_is_benign() -> None:
    # pkg.a <-> pkg.b via plain ``import x``, imported names used only inside
    # functions: this imports fine and must not be reported.
    assert cycles_for_fixture("cycle_benign") == []


def test_module_scope_attribute_use_makes_cycle_load_time() -> None:
    # Same plain-import cycle, but pkg.a reads pkg.b.value_b at module scope.
    assert cycles_for_fixture("cycle_attr") == [["pkg.a", "pkg.b"]]
