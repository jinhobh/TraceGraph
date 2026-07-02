"""Pytest plugin that tags coverage.py measurement with the running test.

Loaded with ``pytest -p validation._context_plugin`` inside a process started
by ``coverage run``. Each test's setup, call, and teardown are recorded under
a dynamic context equal to the pytest nodeid (``tests/test_x.py::test_y``),
so the coverage database can answer "which tests executed this file?".

Between tests the context is reset to the default (empty) context, which is
where collection-time and import-time execution lands.
"""

from __future__ import annotations

import coverage


def pytest_runtest_logstart(nodeid: str, location: tuple[str, int | None, str]) -> None:
    cov = coverage.Coverage.current()
    if cov is not None:
        cov.switch_context(nodeid)


def pytest_runtest_logfinish(
    nodeid: str, location: tuple[str, int | None, str]
) -> None:
    cov = coverage.Coverage.current()
    if cov is not None:
        cov.switch_context("")
