"""Discovery tests: module naming, layouts, and parse-failure handling."""

from __future__ import annotations

from pathlib import Path

from tracegraph.discovery import discover, module_for_path

FIXTURES = Path(__file__).parent / "fixtures"


def test_namespace_packages_indexed() -> None:
    project = discover(FIXTURES / "namespace" / "project")
    assert {"ns", "ns.alpha", "ns.beta", "ns.deep", "ns.deep.mod"} == set(project.index)
    assert project.modules["ns"].is_package
    assert project.modules["ns"].tree is None  # PEP 420: no source of its own
    assert project.modules["ns.deep"].is_package


def test_src_layout_module_names() -> None:
    project = discover(FIXTURES / "src_layout" / "project")
    assert {"mypkg", "mypkg.core", "mypkg.util", "tests", "tests.test_core"} == set(
        project.index
    )
    # src/ is a source root, not a package prefix.
    assert not any(name.startswith("src") for name in project.index)


def test_regular_packages_are_parsed() -> None:
    project = discover(FIXTURES / "absolute" / "project")
    assert project.modules["pkg"].is_package
    assert project.modules["pkg"].tree is not None
    assert not project.modules["pkg.a"].is_package


def test_module_for_path_round_trip() -> None:
    root = FIXTURES / "src_layout" / "project"
    project = discover(root)
    assert module_for_path(project, root / "src" / "mypkg" / "util.py") == "mypkg.util"
    assert module_for_path(project, root / "src" / "mypkg" / "__init__.py") == "mypkg"
    assert module_for_path(project, root / "pyproject.toml") is None
