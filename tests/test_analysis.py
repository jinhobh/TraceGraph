"""Transitive-dependency and test-impact-analysis tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracegraph.analysis import (
    affected_tests,
    dependents_of,
    is_test_module,
    transitive_dependencies,
)
from tracegraph.discovery import discover
from tracegraph.graph import ModuleGraph, build_graph, load_time_cycles
from tracegraph.resolver import Context, Edge

FIXTURES = Path(__file__).parent / "fixtures"


def edge(src: str, dst: str, context: Context = "module") -> Edge:
    return Edge(
        src, dst, context, resolved=True, category="first_party", binding="symbol"
    )


def graph_of(edges: list[Edge]) -> ModuleGraph:
    nodes = {e.src for e in edges} | {e.dst for e in edges}
    return ModuleGraph(modules=frozenset(nodes), edges=tuple(edges))


def test_affected_tests_direction() -> None:
    # test_a imports lib.core: a change to lib.core affects test_a. The
    # reverse question (what does test_a depend on?) must not leak in.
    graph = graph_of([edge("tests.test_a", "lib.core")])
    assert affected_tests(graph, "lib.core") == {"tests.test_a"}
    assert affected_tests(graph, "tests.test_a") == {"tests.test_a"}


def test_affected_tests_transitive() -> None:
    graph = graph_of([edge("tests.test_api", "lib.api"), edge("lib.api", "lib.core")])
    assert affected_tests(graph, "lib.core") == {"tests.test_api"}


def test_lazy_edges_count_for_impact() -> None:
    # A function-local or TYPE_CHECKING import is still a runtime dependency;
    # TIA uses ALL edges (recall over precision).
    for context in ("function", "type_checking"):
        graph = graph_of([edge("tests.test_a", "lib.core", context)])
        assert affected_tests(graph, "lib.core") == {"tests.test_a"}


def test_affected_tests_on_src_layout_fixture() -> None:
    project = discover(FIXTURES / "src_layout" / "project")
    graph = build_graph(project)
    assert affected_tests(graph, "mypkg.util") == {"tests.test_core"}
    assert affected_tests(graph, "mypkg.core") == {"tests.test_core"}


def test_lazy_fixture_has_no_load_time_cycle_but_full_impact() -> None:
    project = discover(FIXTURES / "lazy" / "project")
    graph = build_graph(project)
    # pkg.a <-> pkg.b only via TYPE_CHECKING edges: not a load-time cycle...
    assert load_time_cycles(graph) == []
    # ...but the dependency is real for impact analysis.
    assert "pkg.b" in dependents_of(graph, "pkg.a")


def test_transitive_dependencies_condense_cycles() -> None:
    graph = graph_of([edge("a", "b"), edge("b", "c"), edge("c", "b"), edge("d", "a")])
    assert transitive_dependencies(graph, "a") == {"b", "c"}
    assert transitive_dependencies(graph, "b") == {"c"}
    assert transitive_dependencies(graph, "c") == {"b"}
    assert transitive_dependencies(graph, "d") == {"a", "b", "c"}


def test_transitive_dependencies_can_filter_contexts() -> None:
    graph = graph_of([edge("a", "b"), edge("b", "c", "function")])
    assert transitive_dependencies(graph, "a") == {"b", "c"}
    module_only: tuple[Context, ...] = ("module",)
    assert transitive_dependencies(graph, "a", contexts=module_only) == {"b"}


def test_unknown_module_raises() -> None:
    graph = graph_of([edge("a", "b")])
    with pytest.raises(ValueError):
        affected_tests(graph, "nope")
    with pytest.raises(ValueError):
        transitive_dependencies(graph, "nope")


def test_is_test_module_heuristic() -> None:
    assert is_test_module("tests.test_core")
    assert is_test_module("pkg.tests.helpers")
    assert is_test_module("test_standalone")
    assert is_test_module("pkg.core_test")
    assert not is_test_module("pkg.core")
