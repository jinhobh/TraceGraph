"""Fixture-driven resolver tests.

Each directory under tests/fixtures/ holds a mini project plus an
expected_edges.json; the produced edge set must equal the expected set
exactly. A resolver change without a corresponding fixture is incomplete.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracegraph.discovery import discover
from tracegraph.graph import build_graph

FIXTURES = Path(__file__).parent / "fixtures"
CASES = sorted(path.name for path in FIXTURES.iterdir() if path.is_dir())

EdgeTuple = tuple[str, str, str, bool, str]


def actual_edges(case: str) -> set[EdgeTuple]:
    project = discover(FIXTURES / case / "project")
    graph = build_graph(project)
    return {
        (edge.src, edge.dst, edge.context, edge.resolved, edge.category)
        for edge in graph.edges
    }


def expected_edges(case: str) -> set[EdgeTuple]:
    raw = json.loads((FIXTURES / case / "expected_edges.json").read_text())
    return {
        (item["src"], item["dst"], item["context"], item["resolved"], item["category"])
        for item in raw
    }


def test_every_fixture_has_expectations() -> None:
    assert CASES, "no fixtures found"
    for case in CASES:
        assert (FIXTURES / case / "expected_edges.json").is_file(), case
        assert (FIXTURES / case / "project").is_dir(), case


@pytest.mark.parametrize("case", CASES)
def test_fixture_edge_set(case: str) -> None:
    assert actual_edges(case) == expected_edges(case)


def test_broken_file_recorded_not_fatal() -> None:
    project = discover(FIXTURES / "broken" / "project")
    assert len(project.parse_errors) == 1
    assert project.parse_errors[0].path.name == "bad.py"
    # The unparseable module stays in the index so imports of it resolve.
    assert "pkg.bad" in project.index
    assert project.modules["pkg.bad"].tree is None
