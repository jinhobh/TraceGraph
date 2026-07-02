"""End-to-end CLI tests against the fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracegraph.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def test_analyze_text(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["analyze", str(FIXTURES / "absolute" / "project")])
    out = capsys.readouterr().out
    assert code == 0
    assert "first-party" in out
    assert "load-time cycles: none" in out


def test_analyze_json(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["analyze", "--format", "json", str(FIXTURES / "lazy" / "project")])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert "pkg.a" in payload["modules"]
    contexts = {edge["context"] for edge in payload["edges"]}
    assert {"module", "function", "type_checking"} <= contexts


def test_analyze_dot(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["analyze", "--format", "dot", str(FIXTURES / "cycle" / "project")])
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("digraph tracegraph {")
    assert '"pkg.a" -> "pkg.b"' in out


def test_analyze_surfaces_dynamic_blind_spots(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["analyze", str(FIXTURES / "dynamic" / "project")])
    out = capsys.readouterr().out
    assert code == 0
    assert "<dynamic>" in out  # never silently dropped


def test_cycles_clean_exit_zero(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["cycles", str(FIXTURES / "lazy" / "project")])
    out = capsys.readouterr().out
    assert code == 0
    assert "no load-time cycles" in out


def test_cycles_detected_exit_one(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["cycles", str(FIXTURES / "cycle" / "project")])
    out = capsys.readouterr().out
    assert code == 1
    assert "pkg.a <-> pkg.b" in out


def test_affected_lists_tests(capsys: pytest.CaptureFixture[str]) -> None:
    root = FIXTURES / "src_layout" / "project"
    changed = root / "src" / "mypkg" / "util.py"
    code = main(["affected", "--changed", str(changed), str(root)])
    out = capsys.readouterr().out
    assert code == 0
    assert out.splitlines() == ["tests.test_core"]


def test_affected_rejects_non_module(capsys: pytest.CaptureFixture[str]) -> None:
    root = FIXTURES / "src_layout" / "project"
    code = main(["affected", "--changed", str(root / "README.md"), str(root)])
    assert code == 2
    assert "is not a module" in capsys.readouterr().err


def test_missing_directory_exit_two(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["analyze", str(FIXTURES / "does_not_exist")])
    assert code == 2
    assert "not a directory" in capsys.readouterr().err
